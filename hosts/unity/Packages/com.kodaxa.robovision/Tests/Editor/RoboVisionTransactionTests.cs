using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// The guarantees that decide whether an agent can trust the Unity host:
    /// concurrency, verified rollback, recovery, and refusing to bury a human's edit.
    /// </summary>
    public sealed class RoboVisionTransactionTests
    {
        private Harness _rv;

        [SetUp]
        public void SetUp()
        {
            Harness.FreshScene();
            _rv = new Harness("tx");
        }

        [TearDown]
        public void TearDown() => _rv.ResetTransactions();

        [Test]
        public void StaleRevisionIsRejectedAndChangesNothing()
        {
            var id = _rv.CreateObject("Drift");
            var observed = _rv.Revision;

            // A human moves the object while the agent is still planning.
            var live = GameObject.Find("Drift");
            Undo.RecordObject(live.transform, "human move");
            live.transform.localPosition = new Vector3(3f, 0f, 0f);

            _rv.Call("object.transform",
                new JObject { ["object"] = id, ["local_position"] = new JArray(0f, 5f, 0f) },
                ifRevision: observed, ok: false, code: "STALE_REVISION");

            Assert.That(GameObject.Find("Drift").transform.localPosition.x, Is.EqualTo(3f).Within(1e-4f),
                "the rejected mutation still moved the object");
            Assert.That(GameObject.Find("Drift").transform.localPosition.y, Is.EqualTo(0f).Within(1e-4f),
                "the rejected mutation applied its own translation");
        }

        [Test]
        public void ReObservingMakesTheSameMutationAcceptable()
        {
            var id = _rv.CreateObject("Retry");
            var live = GameObject.Find("Retry");
            Undo.RecordObject(live.transform, "human move");
            live.transform.localPosition = new Vector3(3f, 0f, 0f);

            _rv.Call("object.transform",
                new JObject { ["object"] = id, ["local_position"] = new JArray(0f, 5f, 0f) },
                ifRevision: 0, ok: false, code: "STALE_REVISION");

            var current = _rv.Result("scene.describe").Value<long>("revision");
            _rv.Call("object.transform",
                new JObject { ["object"] = id, ["local_position"] = new JArray(0f, 5f, 0f) },
                ifRevision: current);

            Assert.That(GameObject.Find("Retry").transform.localPosition.y, Is.EqualTo(5f).Within(1e-4f));
        }

        [Test]
        public void CommitReportsTheBeginAndFinalFingerprints()
        {
            var begin = _rv.Result("transaction.begin", new JObject { ["label"] = "commit" });
            var id = begin.Value<string>("transaction");
            _rv.CreateObject("Committed");
            var commit = _rv.EndTransaction("transaction.commit", id);

            Assert.That(commit.Value<bool>("committed"), Is.True);
            Assert.That(commit.Value<string>("final_fingerprint"),
                Is.Not.EqualTo(begin.Value<string>("begin_fingerprint")),
                "a transaction that created an object reported an unchanged fingerprint");
            Assert.That(GameObject.Find("Committed"), Is.Not.Null, "the committed object is gone");
        }

        [Test]
        public void RollbackRestoresTheBeginFingerprintAndRemovesTheObject()
        {
            var baseline = _rv.Fingerprint();
            var id = _rv.BeginTransaction("rollback");
            _rv.CreateObject("Temporary");
            Assert.That(GameObject.Find("Temporary"), Is.Not.Null);

            var rolled = _rv.EndTransaction("transaction.rollback", id);

            Assert.That(rolled.Value<bool>("rolled_back"), Is.True);
            Assert.That(GameObject.Find("Temporary"), Is.Null, "rollback left the created object behind");
            Assert.That(_rv.Fingerprint(), Is.EqualTo(baseline),
                "rollback reported success without restoring the begin fingerprint");
        }

        [Test]
        public void RollbackRestoresAMutatedTransform()
        {
            var objectId = _rv.CreateObject("Restored", 1f, 1f, 1f);
            var baseline = _rv.Fingerprint();

            var tx = _rv.BeginTransaction("restore transform");
            _rv.Call("object.transform", new JObject
            {
                ["object"] = objectId,
                ["local_position"] = new JArray(9f, 9f, 9f)
            });
            Assert.That(GameObject.Find("Restored").transform.localPosition.x, Is.EqualTo(9f).Within(1e-4f));

            _rv.EndTransaction("transaction.rollback", tx);

            Assert.That(GameObject.Find("Restored").transform.localPosition.x, Is.EqualTo(1f).Within(1e-4f),
                "rollback claimed success but the transform was not restored");
            Assert.That(_rv.Fingerprint(), Is.EqualTo(baseline));
        }

        [Test]
        public void RollbackLeavesUnrelatedObjectsAlone()
        {
            var bystanderId = _rv.CreateObject("Bystander", 7f, 0f, 0f);
            var subjectId = _rv.CreateObject("Subject");
            var bystanderBefore = _rv.Result("object.inspect", new JObject { ["object"] = bystanderId }).ToString();
            var baseline = _rv.Fingerprint();

            var tx = _rv.BeginTransaction("scoped");
            _rv.Call("object.transform", new JObject
            {
                ["object"] = subjectId,
                ["local_position"] = new JArray(0f, 0f, 4f)
            });
            _rv.EndTransaction("transaction.rollback", tx);

            var bystanderAfter = _rv.Result("object.inspect", new JObject { ["object"] = bystanderId }).ToString();
            Assert.That(bystanderAfter, Is.EqualTo(bystanderBefore),
                "rollback altered an object the transaction never touched");
            Assert.That(_rv.Fingerprint(), Is.EqualTo(baseline));
        }

        [Test]
        public void FailedMutationLeavesNoPartialState()
        {
            var id = _rv.CreateObject("Recover");
            var before = _rv.Fingerprint();

            // A property that does not exist fails after the host has taken its
            // checkpoint, which is the shape of a mid-operation failure.
            var response = _rv.Call("serialized.set", new JObject
            {
                ["target"] = id,
                ["path"] = "m_ThisPropertyDoesNotExist",
                ["value"] = 3
            }, ok: false);

            Assert.That(_rv.Fingerprint(), Is.EqualTo(before),
                "a failed mutation changed the scene: " + response.ToString(Newtonsoft.Json.Formatting.None));
        }

        [Test]
        public void OutOfBandEditDuringATransactionBlocksAutomaticRollback()
        {
            var id = _rv.BeginTransaction("contaminated");
            _rv.CreateObject("AgentWork");

            // A human adds their own object while the agent's transaction is open.
            var human = new GameObject("HumanWork");
            Undo.RegisterCreatedObjectUndo(human, "human work");
            _rv.Call("scene.describe"); // let the host observe the external change

            _rv.Call("transaction.rollback", new JObject { ["transaction"] = id },
                ok: false, code: "TRANSACTION_CONTAMINATED");
            Assert.That(GameObject.Find("HumanWork"), Is.Not.Null,
                "a refused rollback still destroyed the human's object");

            var forced = _rv.EndTransaction("transaction.rollback", id, force: true);
            Assert.That(forced.Value<bool>("forced_after_external_change"), Is.True,
                "a forced rollback did not report that it overrode an external change");
        }

        /// <summary>
        /// An identity-only change still contaminates a transaction, on purpose.
        /// </summary>
        /// <remarks>
        /// Saving an untitled scene moves the deep fingerprint without moving
        /// the authored scene revision: every object with only a session handle
        /// earns a durable GlobalObjectId, which is re-addressing rather than
        /// authoring. The two counters disagreeing here is not a bug — the
        /// revision answers "did the authored scene move" and the fingerprint
        /// answers "does this hash the way it did", and after an upgrade it
        /// genuinely does not.
        ///
        /// Contamination is judged on the fingerprint, and that stays
        /// deliberately conservative rather than being softened to match the
        /// revision. Forcing past it does not help either, and that is the part
        /// worth pinning: a rollback proves restoration by reproducing the
        /// checkpoint's fingerprint, and it cannot, because the identities in
        /// that checkpoint no longer exist. So it reports ROLLBACK_INCOMPLETE
        /// rather than claiming a restoration it cannot demonstrate — which is
        /// the correct answer, and the reason not to soften contamination here.
        ///
        /// Whether a transaction should instead re-checkpoint across an identity
        /// upgrade belongs to the transaction adoption and recovery work; this
        /// pins the behaviour so that change has to be deliberate.
        /// </remarks>
        [Test]
        public void AnIdentityUpgradeContaminatesAnOpenTransaction()
        {
            var directory = "Assets/RoboVisionTxIdentity";
            if (!System.IO.Directory.Exists(directory)) System.IO.Directory.CreateDirectory(directory);
            AssetDatabase.Refresh();
            var path = directory + "/TxIdentity.unity";
            try
            {
                var sessionId = _rv.CreateObject("Upgraded");
                Assert.That(sessionId, Does.StartWith("unity:session:"),
                    "precondition: the object must have no durable identity yet");

                var id = _rv.BeginTransaction("spanning a save");
                var revision = _rv.Result("scene.describe").Value<long>("revision");

                UnityEditor.SceneManagement.EditorSceneManager.SaveScene(
                    UnityEngine.SceneManagement.SceneManager.GetActiveScene(), path);
                _rv.Call("scene.describe"); // let the host observe it

                Assert.That(_rv.Result("scene.describe").Value<long>("revision"), Is.EqualTo(revision),
                    "re-addressing an object moved the authored scene revision");

                _rv.Call("transaction.rollback", new JObject { ["transaction"] = id },
                    ok: false, code: "TRANSACTION_CONTAMINATED");

                // And forcing it does not manufacture a restoration: the
                // checkpoint named objects by identities that no longer exist.
                var forced = _rv.Call("transaction.rollback",
                    new JObject { ["transaction"] = id, ["force"] = true },
                    ok: false, code: "ROLLBACK_INCOMPLETE");
                Assert.That(((JObject)forced["error"]["data"]).Value<string>("expected_fingerprint"),
                    Is.Not.EqualTo(((JObject)forced["error"]["data"]).Value<string>("actual_fingerprint")),
                    "the rollback reported incomplete with nothing to distinguish");
            }
            finally
            {
                AssetDatabase.DeleteAsset(path);
                if (AssetDatabase.IsValidFolder(directory)) AssetDatabase.DeleteAsset(directory);
                AssetDatabase.Refresh();
            }
        }

        [Test]
        public void SecondTransactionIsRefusedWhileOneIsActive()
        {
            var id = _rv.BeginTransaction("first");
            _rv.Call("transaction.begin", new JObject { ["label"] = "second" },
                ok: false, code: "TRANSACTION_ACTIVE");
            _rv.EndTransaction("transaction.commit", id);
        }

        [Test]
        public void CommitWithoutATransactionIsRefused()
        {
            _rv.Call("transaction.commit", new JObject { ["transaction"] = "tx:nothing" },
                ok: false, code: "NO_TRANSACTION");
        }

        [Test]
        public void EditorUndoAfterCommitIsVisibleToTheHost()
        {
            var tx = _rv.BeginTransaction("undo interaction");
            _rv.CreateObject("Undoable");
            _rv.EndTransaction("transaction.commit", tx);
            Assert.That(GameObject.Find("Undoable"), Is.Not.Null);

            var afterCommit = _rv.Fingerprint();
            Undo.PerformUndo();

            var afterUndo = _rv.Fingerprint();
            Assert.That(afterUndo, Is.Not.EqualTo(afterCommit),
                "the host did not notice that the user undid its committed work");

            Undo.PerformRedo();
            Assert.That(_rv.Fingerprint(), Is.EqualTo(afterCommit),
                "redo did not return the scene to the committed state the host recorded");
        }

        [Test]
        public void RevisionAdvancesAcrossMutations()
        {
            // Read through a dispatch, not off the host: setup opened a new
            // scene, and the scene revision resets with the document incarnation,
            // so a number captured before the host has looked belongs to a world
            // that is no longer loaded.
            var before = _rv.Result("scene.describe").Value<long>("revision");
            _rv.CreateObject("Advance");
            Assert.That(_rv.Result("scene.describe").Value<long>("revision"), Is.GreaterThan(before),
                "a mutation did not advance the scene revision");
        }
    }
}
