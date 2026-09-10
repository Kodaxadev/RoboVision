using System;
using System.Collections;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// The two states only a real editor lifecycle can produce, and what health owes them.
    /// </summary>
    /// <remarks>
    /// Play mode and a domain reload are the cases an agent is most likely to
    /// meet and least able to reason about, and both are exactly where a single
    /// health light would mislead. Play mode blocks authoring while the transport
    /// and the scene are perfectly alive; a reload can leave a transaction whose
    /// checkpoint cannot be reproduced, which must never be reported as something
    /// a recovery could still put right.
    ///
    /// Separated from the rest of the health tests because these are
    /// <c>[UnityTest]</c> coroutines driving the editor across a domain boundary,
    /// which is a different kind of test from asserting on a report.
    /// </remarks>
    public sealed class RoboVisionHealthLifecycleTests
    {
        private const string Folder = "Assets/RoboVisionHealth";
        private const string ScenePath = Folder + "/Health.unity";
        private const string IdKey = "RoboVision.Tests.HealthTx";

        [SetUp]
        public void SetUp()
        {
            if (!System.IO.Directory.Exists(Folder)) System.IO.Directory.CreateDirectory(Folder);
            AssetDatabase.Refresh();
        }

        [TearDown]
        public void TearDown()
        {
            SessionState.EraseString(IdKey);
            if (AssetDatabase.IsValidFolder(Folder)) AssetDatabase.DeleteAsset(Folder);
            AssetDatabase.Refresh();
        }

        private static JObject Ready(JObject report, string name)
        {
            return (JObject)report["ready_for"][name];
        }

        /// <summary>
        /// Play mode blocks authoring without claiming the transport or scene is dead.
        /// </summary>
        /// <remarks>
        /// The distinction is the whole point. An agent told "the host is
        /// unhealthy" stops; an agent told "authoring is blocked because the
        /// editor is playing, and observation is ready" can keep looking at the
        /// running scene and can plan for the moment play mode ends.
        /// </remarks>
        [UnityTest]
        public IEnumerator PlayModeBlocksAuthoringWithoutCondemningTheTransportOrTheScene()
        {
            Harness.FreshScene();
            var before = new Harness("health-play");
            before.CreateObject("Authored");
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), ScenePath);

            var edit = before.Result("system.health");
            Assert.That(Ready(edit, "mutate").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthReady),
                "precondition: an edit-mode host must be ready to author");

            yield return new EnterPlayMode();

            var rv = new Harness("health-play-in");
            var report = rv.Result("system.health");

            // The request arrived and ran, which is the executor's entire claim.
            Assert.That(((JObject)report["subsystems"]["executor"]).Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthReady),
                "play mode was reported as a dead executor");
            Assert.That(Ready(report, "semantic_observation").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthReady),
                "play mode was allowed to condemn the scene read");

            var mutate = Ready(report, "mutate");
            Assert.That(mutate.Value<string>("status"), Is.EqualTo(RoboVisionHost.HealthBlocked),
                "play mode did not block authored mutation");
            Assert.That(mutate.Value<string>("reason"), Is.EqualTo("play_mode"),
                "the block was not attributed to play mode");
            Assert.That(Ready(report, "begin_correction").Value<string>("reason"),
                Is.EqualTo("play_mode"),
                "a correction was offered, or refused for the wrong reason, in play mode");

            // The state domain says which universe this report is about, so the
            // authored revision it carries cannot be mistaken for a version of
            // what is currently on screen.
            Assert.That(((JObject)report["identity"]).Value<string>("state_domain"),
                Is.EqualTo(RoboVisionHost.DomainPlayRuntime),
                "a play-mode health report was presented as authored state");

            // And health agrees with what dispatch actually does.
            rv.Call("object.create", new JObject { ["name"] = "RuntimeIntruder" },
                ok: false, code: "PLAY_MODE_MUTATION_REFUSED");

            yield return new ExitPlayMode();

            var after = new Harness("health-play-after").Result("system.health");
            Assert.That(Ready(after, "mutate").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthReady),
                "authoring did not become possible again after leaving play mode");
            Assert.That(((JObject)after["identity"]).Value<string>("state_domain"),
                Is.EqualTo(RoboVisionHost.DomainAuthored));
        }

        /// <summary>
        /// A transaction whose checkpoint cannot be reproduced is never offered as recoverable.
        /// </summary>
        /// <remarks>
        /// In an unsaved scene a domain reload re-addresses every object, so the
        /// begin fingerprint names identities that no longer exist. Undo can
        /// still run and still cannot reproduce it. Health must therefore report
        /// a situation with no verified rollback behind it — the one honest
        /// outcome is discarding — rather than a transaction that merely needs
        /// adopting.
        /// </remarks>
        [UnityTest]
        public IEnumerator ARecoveryUncertainTransactionDoesNotClaimVerifiedRecovery()
        {
            Harness.FreshScene();
            var rv = new Harness("health-uncertain");
            var sessionId = rv.CreateObject("UnsavedBefore");
            Assert.That(sessionId, Does.StartWith("unity:session:"),
                "precondition: the checkpoint must be made of session-scoped identities");

            var begun = rv.BeginTransactionRaw(new JObject { ["label"] = "uncertain" });
            SessionState.SetString(IdKey, begun.Value<string>("transaction"));
            rv.CreateObject("UnsavedInside");

            yield return new EnterPlayMode();
            yield return new ExitPlayMode();

            var id = SessionState.GetString(IdKey, null);
            var after = new Harness("health-uncertain-after");
            var report = after.Result("system.health");

            var finish = Ready(report, "finish_or_recover");
            Assert.That(finish.Value<string>("situation"),
                Is.EqualTo(RoboVisionHost.SituationRecoveryUncertain),
                "a transaction whose checkpoint is gone was offered as an ordinary orphan");
            Assert.That(finish.Value<string>("status"), Is.EqualTo(RoboVisionHost.HealthDegraded),
                "an unrecoverable transaction was reported as ready to resolve");
            Assert.That(finish.Value<bool>("verified_rollback_available"), Is.False,
                "health offered a verified rollback the mechanism cannot perform");
            Assert.That(((JObject)finish["transaction"]).Value<string>("reason"),
                Is.EqualTo("checkpoint_identities_lost"));

            Assert.That(Ready(report, "mutate").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthBlocked));
            Assert.That(Ready(report, "mutate").Value<string>("reason"),
                Is.EqualTo("transaction_" + RoboVisionHost.SituationRecoveryUncertain));
            Assert.That(Ready(report, "semantic_observation").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthReady),
                "an unrecoverable transaction was allowed to block observation");

            // The world was resumed and health says so, which is the difference
            // between "this transaction is uncertain" and "this is a new world".
            Assert.That(((JObject)report["identity"]).Value<string>("world_resumption"),
                Is.EqualTo("verified"));

            // Health said no verified rollback was available, and there is none.
            after.Call("transaction.rollback", new JObject { ["transaction"] = id },
                ok: false, code: "TRANSACTION_RECOVERY_UNCERTAIN");
            after.EndTransaction("transaction.discard", id);
        }
    }
}
