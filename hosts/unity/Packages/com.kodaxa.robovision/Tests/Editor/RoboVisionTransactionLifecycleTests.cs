using System;
using System.Collections;
using System.Linq;
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
    /// What happens to an open transaction when the world under it changes.
    /// </summary>
    /// <remarks>
    /// Measured before any of this existed, and it was worse than a missing
    /// feature. A transaction stored no world, so after a Single-mode load it
    /// stayed <c>active</c> with a begin fingerprint from a world that was gone,
    /// accepted a further mutation, and <c>transaction.commit</c> <em>succeeded</em>
    /// — collapsing undo groups in a universe its checkpoint never described.
    /// Entering a Prefab Stage, leaving one and swapping one for another all did
    /// the same.
    ///
    /// A transaction may never silently migrate into a different world. It ends,
    /// with a reason, and a later commit or rollback is told what happened
    /// rather than that it never existed.
    /// </remarks>
    public sealed class RoboVisionTransactionLifecycleTests
    {
        private const string Folder = "Assets/RoboVisionTxLifecycle";
        private const string ScenePath = Folder + "/TxLifecycle.unity";
        private const string TokenKey = "RoboVision.Tests.TxToken";
        private const string IdKey = "RoboVision.Tests.TxId";
        private const string PrintKey = "RoboVision.Tests.TxFingerprint";
        private Harness _rv;

        [SetUp]
        public void SetUp()
        {
            if (!System.IO.Directory.Exists(Folder)) System.IO.Directory.CreateDirectory(Folder);
            AssetDatabase.Refresh();
            Harness.FreshScene();
            _rv = new Harness("txlife");
            _rv.Result("scene.describe");
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var key in new[] { TokenKey, IdKey, PrintKey }) SessionState.EraseString(key);
            if (PrefabStageUtility.GetCurrentPrefabStage() != null) StageUtility.GoToMainStage();
            if (AssetDatabase.IsValidFolder(Folder)) AssetDatabase.DeleteAsset(Folder);
            AssetDatabase.Refresh();
        }

        private JObject Begin(string label)
        {
            return _rv.Result("transaction.begin", new JObject { ["label"] = label });
        }

        private void AssertAbandoned(string id, string reason)
        {
            foreach (var method in new[] { "transaction.commit", "transaction.rollback" })
            {
                var refused = _rv.Call(method, new JObject { ["transaction"] = id },
                    ok: false, code: "TRANSACTION_ABANDONED");
                var data = (JObject)refused["error"]["data"];
                Assert.That(data.Value<string>("reason"), Is.EqualTo(reason),
                    method + " did not say why the transaction ended");
                Assert.That(data.Value<string>("state"),
                    Is.EqualTo(RoboVisionTransactions.StateAbandoned));
            }
        }

        [Test]
        public void AWorldReplacementAbandonsTheTransactionRatherThanMigratingIt()
        {
            var begun = Begin("across a load");
            var id = begun.Value<string>("transaction");
            Assert.That(begun.Value<string>("world_incarnation"),
                Is.EqualTo(_rv.Result("scene.describe").Value<string>("world_incarnation")),
                "a transaction must record the world it was opened in");
            _rv.CreateObject("InsideTransaction");

            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            _rv.Result("scene.describe");

            var hello = _rv.Result("system.hello")["transaction"];
            Assert.That(hello.Value<bool>("active"), Is.False,
                "a transaction survived into a world its checkpoint never described");
            AssertAbandoned(id, "world_replaced");

            // And the new world is usable, rather than wedged by a ghost.
            var fresh = Begin("in the new world");
            Assert.That(fresh.Value<string>("transaction"), Is.Not.EqualTo(id));
            _rv.Result("transaction.rollback", new JObject
            {
                ["transaction"] = fresh.Value<string>("transaction")
            });
        }

        [Test]
        public void EnteringAPrefabStageAbandonsAnOpenTransaction()
        {
            var source = new GameObject("Staged");
            var prefab = Folder + "/Staged.prefab";
            PrefabUtility.SaveAsPrefabAsset(source, prefab);
            UnityEngine.Object.DestroyImmediate(source);

            var id = Begin("into a prefab").Value<string>("transaction");
            _rv.CreateObject("InsideTransaction");

            PrefabStageUtility.OpenPrefab(prefab);
            _rv.Result("scene.describe");

            AssertAbandoned(id, "world_replaced");
            Assert.That(_rv.Result("system.hello")["transaction"].Value<bool>("active"), Is.False,
                "a transaction opened in the main stage was still open inside a prefab");
        }

        /// <summary>
        /// A rebuilt bridge leaves a saved-scene transaction recoverable, and says so.
        /// </summary>
        /// <remarks>
        /// The rollback mechanism was measured across the reload before this was
        /// written, because persisting metadata and calling that survival would
        /// prove nothing: reverting to the transaction's undo group after a
        /// domain reload really did restore the begin fingerprint. So the
        /// transaction comes back — orphaned, with no owner, waiting for the
        /// token its owner was given.
        /// </remarks>
        [UnityTest]
        public IEnumerator ADomainReloadLeavesASavedTransactionOrphanedAndAdoptable()
        {
            _rv.CreateObject("Committed");
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), ScenePath);
            _rv.Result("scene.describe");

            var begun = Begin("across a reload");
            SessionState.SetString(IdKey, begun.Value<string>("transaction"));
            SessionState.SetString(TokenKey, begun.Value<string>("recovery_token"));
            SessionState.SetString(PrintKey, begun.Value<string>("begin_fingerprint"));
            _rv.CreateObject("InsideTransaction");

            yield return new EnterPlayMode();
            yield return new ExitPlayMode();

            var id = SessionState.GetString(IdKey, null);
            var token = SessionState.GetString(TokenKey, null);
            var beginFingerprint = SessionState.GetString(PrintKey, null);
            Assert.That(token, Is.Not.Null.And.Not.Empty, "the test lost its own state");

            var after = new Harness("txlife-after");
            var state = after.Result("system.hello")["transaction"]["state"];
            Assert.That(state.Value<string>("transaction"), Is.EqualTo(id),
                "the interrupted transaction was not restored");
            Assert.That(state.Value<string>("state"), Is.EqualTo(RoboVisionTransactions.StateOrphaned));
            Assert.That(state.Value<string>("reason"), Is.EqualTo("bridge_reloaded"));
            Assert.That(state.Value<bool>("verified_rollback_available"), Is.True);

            // No connection owns it, so nothing may continue it until the token
            // is presented — including the client that happens to be local.
            after.Call("object.create", new JObject { ["name"] = "Sneaky" },
                ok: false, code: "TRANSACTION_ORPHANED");
            after.Call("transaction.rollback", new JObject { ["transaction"] = id },
                ok: false, code: "TRANSACTION_ORPHANED");
            after.Call("transaction.adopt", new JObject
            {
                ["transaction"] = id,
                ["recovery_token"] = "wrong"
            }, ok: false, code: "TRANSACTION_ADOPTION_REFUSED");

            var adopted = after.Result("transaction.adopt", new JObject
            {
                ["transaction"] = id,
                ["recovery_token"] = token
            });
            Assert.That(adopted.Value<string>("state"), Is.EqualTo(RoboVisionTransactions.StateActive));

            var rolled = after.Result("transaction.rollback", new JObject { ["transaction"] = id });
            Assert.That(rolled.Value<string>("fingerprint"), Is.EqualTo(beginFingerprint),
                "the rollback did not reproduce the checkpoint it promised");
            Assert.That(after.Result("scene.describe")["scenes"].SelectMany(s => s["objects"])
                    .Select(o => o.Value<string>("name")),
                Is.EquivalentTo(new[] { "Committed" }),
                "the interrupted transaction's work was not undone");
        }

        /// <summary>
        /// The other outcome an adopted transaction has to be able to reach.
        /// </summary>
        /// <remarks>
        /// A rollback proves the checkpoint can be reproduced. A commit proves
        /// the opposite direction: that the work done before the interruption is
        /// kept, and that the finished record still carries the evidence which
        /// made the outcome meaningful. Asserting only the rollback would leave
        /// the more common ending untested.
        /// </remarks>
        [UnityTest]
        public IEnumerator ADomainReloadLeavesASavedTransactionCommittable()
        {
            _rv.CreateObject("Kept");
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), ScenePath);
            _rv.Result("scene.describe");

            var begun = Begin("committed across a reload");
            SessionState.SetString(IdKey, begun.Value<string>("transaction"));
            SessionState.SetString(TokenKey, begun.Value<string>("recovery_token"));
            SessionState.SetString(PrintKey, begun.Value<string>("begin_fingerprint"));
            _rv.CreateObject("AlsoKept");

            yield return new EnterPlayMode();
            yield return new ExitPlayMode();

            var id = SessionState.GetString(IdKey, null);
            var token = SessionState.GetString(TokenKey, null);
            var beginFingerprint = SessionState.GetString(PrintKey, null);
            var after = new Harness("txlife-commit");

            after.Result("transaction.adopt", new JObject
            {
                ["transaction"] = id,
                ["recovery_token"] = token
            });
            var committed = after.Result("transaction.commit", new JObject { ["transaction"] = id });
            Assert.That(committed.Value<bool>("committed"), Is.True);
            Assert.That(committed.Value<string>("begin_fingerprint"), Is.EqualTo(beginFingerprint));
            Assert.That(committed.Value<string>("final_fingerprint"),
                Is.Not.EqualTo(beginFingerprint), "a commit that kept nothing");

            Assert.That(after.Result("scene.describe")["scenes"].SelectMany(s => s["objects"])
                    .Select(o => o.Value<string>("name")),
                Is.EquivalentTo(new[] { "Kept", "AlsoKept" }),
                "the interrupted transaction's work was not kept by its commit");

            // And the outcome is readable afterwards, with the proof intact — the
            // path a caller whose acknowledgement was lost actually takes.
            var status = after.Result("transaction.status", new JObject { ["transaction"] = id });
            var finished = (JObject)status["finished"];
            Assert.That(finished.Value<string>("state"),
                Is.EqualTo(RoboVisionTransactions.StateCommitted));
            Assert.That(finished.Value<string>("begin_fingerprint"), Is.EqualTo(beginFingerprint));
            Assert.That(finished.Value<string>("final_fingerprint"),
                Is.EqualTo(committed.Value<string>("final_fingerprint")));
        }

        /// <summary>
        /// In an unsaved scene the checkpoint cannot be reproduced, and the host says so.
        /// </summary>
        /// <remarks>
        /// Also measured: the reload re-addresses every object, so the ids the
        /// checkpoint names are gone. Undo removed the right objects and the
        /// begin fingerprint still could not be reproduced. Restoring that as a
        /// rollback-capable transaction would promise a proof the mechanism
        /// cannot deliver, so it comes back uncertain and can only be discarded.
        /// </remarks>
        [UnityTest]
        public IEnumerator ADomainReloadInAnUnsavedSceneLeavesTheTransactionUncertain()
        {
            var sessionId = _rv.CreateObject("UnsavedBefore");
            Assert.That(sessionId, Does.StartWith("unity:session:"),
                "precondition: the checkpoint must be made of session-scoped identities");

            var begun = Begin("unsaved across a reload");
            SessionState.SetString(IdKey, begun.Value<string>("transaction"));
            SessionState.SetString(TokenKey, begun.Value<string>("recovery_token"));
            _rv.CreateObject("UnsavedInside");

            yield return new EnterPlayMode();
            yield return new ExitPlayMode();

            var id = SessionState.GetString(IdKey, null);
            var token = SessionState.GetString(TokenKey, null);
            var after = new Harness("txlife-unsaved");
            var state = after.Result("system.hello")["transaction"]["state"];

            Assert.That(state.Value<string>("state"),
                Is.EqualTo(RoboVisionTransactions.StateRecoveryUncertain),
                "a transaction whose checkpoint identities are gone was offered as recoverable");
            Assert.That(state.Value<string>("reason"), Is.EqualTo("checkpoint_identities_lost"));
            Assert.That(state.Value<bool>("verified_rollback_available"), Is.False,
                "the host offered a verified rollback it cannot perform");

            // Not even the right token makes it executable: authority is not the
            // missing piece here, evidence is.
            after.Call("transaction.adopt", new JObject
            {
                ["transaction"] = id,
                ["recovery_token"] = token
            }, ok: false, code: "TRANSACTION_ADOPTION_REFUSED");
            after.Call("transaction.rollback", new JObject { ["transaction"] = id },
                ok: false, code: "TRANSACTION_RECOVERY_UNCERTAIN");
            after.Call("transaction.commit", new JObject { ["transaction"] = id },
                ok: false, code: "TRANSACTION_RECOVERY_UNCERTAIN");

            var discarded = after.Result("transaction.discard", new JObject { ["transaction"] = id });
            Assert.That(discarded.Value<string>("state"), Is.EqualTo(RoboVisionTransactions.StateAbandoned));
            Assert.That(after.Result("system.hello")["transaction"].Value<bool>("active"), Is.False);
            // And the editor is usable again.
            var fresh = Begin("after discarding");
            after.Result("transaction.rollback", new JObject
            {
                ["transaction"] = fresh.Value<string>("transaction")
            });
        }
    }
}
