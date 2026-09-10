using System;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// What health must keep separate, and what it must never claim.
    /// </summary>
    /// <remarks>
    /// The whole value of a readiness report is that its answers are
    /// independent. A single red/green light would collapse exactly the
    /// distinctions the state model was built to preserve, so every test here
    /// moves one thing and checks that the others did not move with it: an
    /// uncertain journal degrades incremental polling and leaves an
    /// authoritative observation available, a transaction owned by somebody else
    /// blocks mutation and not observation, an orphan asks to be adopted without
    /// the host ever suggesting the caller can do the adopting.
    ///
    /// Driven through <c>Dispatch</c> with explicit connection ids, because
    /// ownership is a fact about connections and this is where a second one can
    /// be produced deliberately.
    /// </remarks>
    [TestFixture]
    internal sealed class RoboVisionHealthTests
    {
        private const long Owner = RoboVisionHost.LocalClientId;
        private const long Stranger = 7;

        private Harness _rv;
        private int _serial;

        [SetUp]
        public void SetUp()
        {
            Harness.FreshScene();
            _rv = new Harness("health");
            _rv.Result("scene.describe");
        }

        [TearDown]
        public void TearDown()
        {
            _rv.ResetTransactions();
        }

        /// <summary>Ask as a named connection, because ownership is about connections.</summary>
        private JObject Health(long clientId = Owner)
        {
            _serial++;
            var response = _rv.Host.Dispatch(new JObject
            {
                ["rv"] = "1.0",
                ["id"] = "health-" + _serial,
                ["method"] = "system.health",
                ["params"] = new JObject()
            }, clientId);
            Assert.That(response.Value<bool>("ok"), Is.True,
                "system.health failed: " + response.ToString(Newtonsoft.Json.Formatting.None));
            return (JObject)response["result"];
        }

        private static JObject Ready(JObject report, string name)
        {
            return (JObject)report["ready_for"][name];
        }

        [Test]
        public void AReadableSceneIsReadyToObserveAndSaysWhatEarnedIt()
        {
            _rv.CreateObject("Anchor");
            var report = Health();
            var semantic = Ready(report, "semantic_observation");

            Assert.That(semantic.Value<string>("status"), Is.EqualTo(RoboVisionHost.HealthReady));
            Assert.That(semantic.Value<string>("basis"),
                Is.EqualTo("authoritative_resync_succeeded"),
                "semantic readiness was claimed without the resync that earns it");
            Assert.That(semantic.Value<string>("fingerprint"), Is.EqualTo(_rv.Fingerprint()),
                "health reported a fingerprint that did not belong to the state it read");

            var identity = (JObject)report["identity"];
            Assert.That(identity.Value<string>("coordinate_contract"), Does.StartWith("rvcoord:"));
            Assert.That(identity.Value<string>("state_domain"),
                Is.EqualTo(RoboVisionHost.DomainAuthored));
            Assert.That(identity.Value<string>("world_incarnation"),
                Is.EqualTo(_rv.Host.WorldIncarnation));

            // The executor claim is exactly what this call proves, and no more.
            var executor = (JObject)report["subsystems"]["executor"];
            Assert.That(executor.Value<string>("status"), Is.EqualTo(RoboVisionHost.HealthReady));
            Assert.That(executor.Value<string>("basis"),
                Is.EqualTo("this_request_was_delivered_and_executed_on_the_editor_thread"));
            Assert.That(executor.Value<string>("queue_depth"),
                Is.EqualTo(RoboVisionHost.HealthNotApplicable),
                "a queue metric was manufactured where the host keeps none");

            // Nothing is claimed that does not exist yet.
            Assert.That(Ready(report, "semantic_verify").Value<string>("validators"),
                Is.EqualTo("not_implemented"));
            Assert.That(Ready(report, "visual_verify").Value<string>("metrics"),
                Is.EqualTo("not_implemented"));
            Assert.That(((JObject)report["subsystems"]["perception"]).Value<string>("multi_pass"),
                Is.EqualTo("not_implemented"),
                "Unity claimed a multi-pass perception bundle it does not implement");
        }

        [Test]
        public void HealthAuthorsNothingAndDoesNotAdvanceTheRevision()
        {
            _rv.CreateObject("Anchor");
            var revision = _rv.Revision;
            var fingerprint = _rv.Fingerprint();
            var epoch = _rv.Host.Journal.Epoch;

            for (var i = 0; i < 3; i++) Health();

            Assert.That(_rv.Revision, Is.EqualTo(revision),
                "system.health advanced the authored revision");
            Assert.That(_rv.Fingerprint(), Is.EqualTo(fingerprint),
                "system.health changed the scene");
            Assert.That(_rv.Host.Journal.Epoch, Is.EqualTo(epoch),
                "system.health opened a journal epoch, which only an authoritative read may do");
        }

        /// <summary>
        /// A history the host cannot vouch for says nothing about whether it can read.
        /// </summary>
        /// <remarks>
        /// The journal is told directly here rather than through an
        /// unattributable edit: what the journal loses certainty *for* is proved
        /// by the journal tests and by the Blender health gate, and repeating it
        /// would test reconciliation instead of the thing under examination.
        /// </remarks>
        [Test]
        public void AnUncertainJournalDegradesPollingAndNotTheScene()
        {
            _rv.CreateObject("Anchor");
            _rv.Host.Journal.LoseCertainty("gate: the fingerprint moved with nothing to show",
                _rv.Revision);

            var report = Health();
            var journal = (JObject)report["subsystems"]["journal"];
            Assert.That(journal.Value<string>("status"), Is.EqualTo(RoboVisionHost.HealthDegraded));
            Assert.That(journal.Value<bool>("certain"), Is.False);

            var incremental = (JObject)journal["incremental_changes"];
            Assert.That(incremental.Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthDegraded));
            Assert.That(incremental.Value<string>("reason"), Is.EqualTo("journal_uncertain"),
                "the degradation was not attributed");

            Assert.That(Ready(report, "semantic_observation").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthReady),
                "journal uncertainty was allowed to condemn the authoritative scene read");
            Assert.That(Ready(report, "mutate").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthReady),
                "journal uncertainty blocked a mutation it has nothing to do with");
            Assert.That(_rv.Host.Journal.Certain, Is.False,
                "health restored certainty, which only a full authoritative read may do");
        }

        [Test]
        public void AnIdleHostOffersNothingToFinishOrRecover()
        {
            var finish = Ready(Health(), "finish_or_recover");
            Assert.That(finish.Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthNotApplicable));
            Assert.That(finish.Value<string>("situation"),
                Is.EqualTo(RoboVisionHost.SituationNone));
            // A correction is only offered where both of its branches exist:
            // accept and commit, or reject and roll back with proof. Unity can
            // prove a rollback headlessly, so the answer here is ready — and it
            // has to say which mechanism made it ready rather than leaving a
            // reader to assume one.
            var begin = Ready(Health(), "begin_correction");
            Assert.That(begin.Value<string>("status"), Is.EqualTo(RoboVisionHost.HealthReady),
                "an idle host refused a new correction");
            Assert.That(begin.Value<bool>("verified_rollback"), Is.True,
                "a correction was offered without stating that a rollback is provable");
        }

        [Test]
        public void AnOwnedTransactionIsNotAnObstacleToItsOwner()
        {
            var id = _rv.BeginTransaction("owned");
            var report = Health();

            var finish = Ready(report, "finish_or_recover");
            Assert.That(finish.Value<string>("situation"),
                Is.EqualTo(RoboVisionHost.SituationOwned));
            Assert.That(Ready(report, "mutate").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthReady),
                "the transaction's own owner was told it could not mutate");
            Assert.That(Ready(report, "mutate").Value<string>("basis"),
                Is.EqualTo("transaction_owned_by_this_connection"));

            var begin = Ready(report, "begin_correction");
            Assert.That(begin.Value<string>("status"), Is.EqualTo(RoboVisionHost.HealthBlocked),
                "a second correction was offered while one was already open");
            Assert.That(begin.Value<string>("reason"),
                Is.EqualTo("transaction_" + RoboVisionHost.SituationOwned),
                "the blocking state was not named exactly");

            // Never, on any path: the host cannot know what a caller is holding.
            var text = report.ToString(Newtonsoft.Json.Formatting.None);
            Assert.That(text, Does.Not.Contain("recovery_token"),
                "a credential appeared in a health report");
            Assert.That(text, Does.Not.Contain("recoverable_by_this_session"),
                "the host claimed to know whether the caller can recover");

            _rv.EndTransaction("transaction.discard", id);
            var after = Ready(Health(), "finish_or_recover");
            Assert.That(after.Value<string>("situation"), Is.EqualTo(RoboVisionHost.SituationNone));
            Assert.That(((JObject)after["recently_finished"]).Value<string>("state"),
                Is.EqualTo(RoboVisionTransactions.StateAbandoned),
                "the recently finished record was lost");
        }

        [Test]
        public void AForeignTransactionBlocksMutationAndNotObservation()
        {
            _rv.BeginTransaction("someone else's");
            var report = Health(Stranger);

            Assert.That(Ready(report, "finish_or_recover").Value<string>("situation"),
                Is.EqualTo(RoboVisionHost.SituationForeign));
            Assert.That(Ready(report, "mutate").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthBlocked),
                "a stranger was told it could mutate inside another connection's transaction");
            Assert.That(Ready(report, "mutate").Value<string>("reason"),
                Is.EqualTo("transaction_" + RoboVisionHost.SituationForeign));
            Assert.That(Ready(report, "semantic_observation").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthReady),
                "a foreign transaction was allowed to block observation");

            // Health and dispatch have to agree about the same connection.
            _serial++;
            var refused = _rv.Host.Dispatch(new JObject
            {
                ["rv"] = "1.0",
                ["id"] = "health-foreign-" + _serial,
                ["method"] = "object.create",
                ["params"] = new JObject { ["name"] = "Foreign" }
            }, Stranger);
            Assert.That(refused["error"].Value<string>("code"),
                Is.EqualTo("TRANSACTION_FOREIGN"),
                "health and dispatch disagreed about what the stranger may do");
        }

        [Test]
        public void AnOrphanAsksToBeAdoptedWithoutOfferingAdoption()
        {
            var id = _rv.BeginTransaction("orphan");
            // The owning connection goes away, which is the host's real
            // notification rather than a state edit standing in for one.
            _rv.Host.Transactions.ClientDisconnected(Owner);

            var report = Health(Stranger);
            var finish = Ready(report, "finish_or_recover");
            Assert.That(finish.Value<string>("situation"),
                Is.EqualTo(RoboVisionHost.SituationOrphaned));
            Assert.That(((JObject)finish["transaction"]).Value<bool>("adoption_required"), Is.True,
                "the orphan did not ask to be adopted");
            Assert.That(finish.Value<string>("note"), Does.Contain("credential"),
                "health did not say who is unable to vouch for the credential");

            Assert.That(Ready(report, "mutate").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthBlocked));
            Assert.That(Ready(report, "mutate").Value<string>("reason"),
                Is.EqualTo("transaction_" + RoboVisionHost.SituationOrphaned));
            Assert.That(Ready(report, "begin_correction").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthBlocked),
                "a new correction was offered over an orphan");
            Assert.That(Ready(report, "semantic_observation").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthReady),
                "an orphan was allowed to block observation");
            Assert.That(report.ToString(Newtonsoft.Json.Formatting.None),
                Does.Not.Contain("recoverable"),
                "the host told a stranger something about recovering the orphan");

            // An orphan cannot be rolled back, so teardown's forced rollback
            // would leave it open for the next test to trip over. Discarding is
            // the one outcome an unowned transaction can honestly reach.
            _rv.EndTransaction("transaction.discard", id);
        }

        /// <summary>
        /// A batch editor has no capture path and can still author, and says both.
        /// </summary>
        /// <remarks>
        /// Guarded rather than asserted unconditionally: the same suite is run in
        /// a windowed editor, where a SceneView really is available, and a test
        /// that demanded a blocked answer there would fail for being right.
        /// </remarks>
        [Test]
        public void ABatchEditorReportsNoCapturePathAndCanStillAuthor()
        {
            var report = Health();
            var visual = Ready(report, "visual_observation");

            if (UnityEngine.Application.isBatchMode)
            {
                Assert.That(visual.Value<string>("status"), Is.EqualTo(RoboVisionHost.HealthBlocked),
                    "a batch-mode editor claimed a usable capture path");
                Assert.That(visual.Value<string>("reason"), Is.EqualTo("batch_mode"),
                    "the visual block was not attributed");
                Assert.That(Ready(report, "visual_verify").Value<string>("status"),
                    Is.EqualTo(RoboVisionHost.HealthBlocked),
                    "visual verification was offered without a visual observation");
            }
            else
            {
                Assert.That(visual.Value<string>("status"),
                    Is.EqualTo(RoboVisionHost.HealthReady)
                        .Or.EqualTo(RoboVisionHost.HealthBlocked));
                Assert.That(visual["basis"] != null || visual["reason"] != null, Is.True,
                    "the visual answer carried neither a basis nor a reason");
            }

            // Whichever it was, it says nothing about authoring.
            Assert.That(Ready(report, "semantic_observation").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthReady));
            Assert.That(Ready(report, "mutate").Value<string>("status"),
                Is.EqualTo(RoboVisionHost.HealthReady),
                "a missing capture path was allowed to block authoring");
            Assert.That(_rv.Call("object.create", new JObject { ["name"] = "AuthoredAnyway" })
                    .Value<string>("outcome"), Is.EqualTo("applied"),
                "health said mutation was ready and it was not");
        }
    }
}
