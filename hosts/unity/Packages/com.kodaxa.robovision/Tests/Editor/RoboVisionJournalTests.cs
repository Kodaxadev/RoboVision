using System.Linq;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// The Unity change journal, including everything it must refuse to answer.
    /// </summary>
    /// <remarks>
    /// Every editor notification is a hint here, never the record. The shapes of
    /// change Unity reports differently — an Undo-recorded edit, a hierarchy
    /// reparent, a component property, a prefab override, a broad scene-dirty
    /// event with no object information — all have to arrive at the journal
    /// through the same authoritative reconciliation, because the one thing they
    /// have in common is that none of them is guaranteed to be delivered.
    ///
    /// Silence is the dangerous direction: an empty event list means "nothing
    /// changed".
    /// </remarks>
    public sealed class RoboVisionJournalTests
    {
        private const string PrefabDirectory = "Assets/RoboVisionJournalPrefabs";
        private Harness _rv;

        [SetUp]
        public void SetUp()
        {
            Harness.FreshScene();
            _rv = new Harness("journal");
            // The scene swap FreshScene just performed is itself a document
            // change; settle it so each test starts from a stable incarnation.
            _rv.Result("scene.describe");
        }

        [TearDown]
        public void TearDown()
        {
            _rv.ResetTransactions();
            if (AssetDatabase.IsValidFolder(PrefabDirectory)) AssetDatabase.DeleteAsset(PrefabDirectory);
        }

        private string Cursor() => _rv.Result("scene.changes_since").Value<string>("cursor");

        private JArray EventsSince(string cursor)
        {
            return (JArray)_rv.Result("scene.changes_since", new JObject { ["cursor"] = cursor })["events"];
        }

        private static string[] Types(JArray events) =>
            events.Select(e => e.Value<string>("type")).ToArray();

        private static string[] Sources(JArray events) =>
            events.Select(e => e.Value<string>("source")).ToArray();

        [Test]
        public void ABootstrapClaimsNoHistory()
        {
            _rv.CreateObject("Preexisting");
            var start = _rv.Result("scene.changes_since");
            Assert.That(start.Value<bool>("bootstrap"), Is.True);
            Assert.That((JArray)start["events"], Is.Empty,
                "a bootstrap handed back history it cannot prove is complete");
            Assert.That(start.Value<string>("cursor"), Does.StartWith("rvcursor:"));
        }

        [Test]
        public void AnAgentMutationIsAttributedToItsRequest()
        {
            var cursor = Cursor();
            var id = _rv.CreateObject("Journalled");

            var events = EventsSince(cursor);
            var created = events.Where(e => e.Value<string>("type") == RoboVisionJournal.ObjectCreated).ToList();
            Assert.That(created, Has.Count.EqualTo(1), "a create produced " + created.Count + " create events");
            Assert.That(created[0]["ids"].Select(x => x.Value<string>()), Contains.Item(id));
            Assert.That(created[0].Value<string>("source"), Is.EqualTo(RoboVisionJournal.SourceAgent));
            Assert.That(created[0].Value<string>("request"), Is.Not.Null.And.Not.Empty,
                "an agent event carried no request id");
        }

        [Test]
        public void AnAgentMutationIsNotJournalledTwiceWhenItsNotificationArrives()
        {
            var cursor = Cursor();
            _rv.CreateObject("Once");
            var afterMutation = EventsSince(cursor);
            Assert.That(afterMutation, Has.Count.EqualTo(1));

            // Unity publishes the change on a later frame. The host is told
            // something happened, re-reads, and finds the scene exactly as it
            // left it — so there is nothing to report a second time.
            _rv.Host.MarkDirty();
            _rv.Result("scene.describe");

            var afterNotification = EventsSince(cursor);
            Assert.That(afterNotification, Has.Count.EqualTo(1),
                "the agent's own mutation was journalled again when its notification arrived: "
                + afterNotification.ToString(Newtonsoft.Json.Formatting.None));
            Assert.That(Sources(afterNotification), Is.EqualTo(new[] { RoboVisionJournal.SourceAgent }));
        }

        [Test]
        public void AnUndoRecordedEditorChangeIsAttributedToTheEditor()
        {
            var id = _rv.CreateObject("Recorded");
            var cursor = Cursor();

            // The ordinary shape of a human edit: recorded with Undo, made by
            // something other than RoboVision.
            var go = GameObject.Find("Recorded");
            Undo.RecordObject(go.transform, "move Recorded");
            go.transform.localPosition = new Vector3(4f, 0f, 0f);

            _rv.Result("scene.describe");
            var events = EventsSince(cursor);
            Assert.That(events, Is.Not.Empty, "an Undo-recorded editor change produced no events");
            Assert.That(Sources(events), Has.All.EqualTo(RoboVisionJournal.SourceEditor));
            Assert.That(events[0]["ids"].Select(x => x.Value<string>()), Contains.Item(id));
        }

        [Test]
        public void HierarchyCreateDeleteAndReparentAreJournalled()
        {
            var parent = _rv.CreateObject("Parent");
            var child = _rv.CreateObject("Child");
            var cursor = Cursor();

            GameObject.Find("Child").transform.SetParent(GameObject.Find("Parent").transform);
            _rv.Result("scene.describe");
            var reparent = EventsSince(cursor);
            Assert.That(Types(reparent), Contains.Item(RoboVisionJournal.ObjectChanged),
                "a reparent was not journalled: " + reparent.ToString(Newtonsoft.Json.Formatting.None));
            Assert.That(reparent.SelectMany(e => e["ids"]).Select(x => x.Value<string>()),
                Contains.Item(child));

            cursor = Cursor();
            Object.DestroyImmediate(GameObject.Find("Child"));
            _rv.Result("scene.describe");
            Assert.That(Types(EventsSince(cursor)), Contains.Item(RoboVisionJournal.ObjectDeleted),
                "a delete was not journalled");
            Assert.That(parent, Is.Not.Null);
        }

        [Test]
        public void AComponentPropertyChangeIsJournalled()
        {
            var id = _rv.CreateObject("Component");
            _rv.Result("component.add", new JObject
            {
                ["object"] = id,
                ["type"] = typeof(BoxCollider).FullName
            });
            var cursor = Cursor();

            var collider = GameObject.Find("Component").GetComponent<BoxCollider>();
            collider.isTrigger = true;
            _rv.Result("scene.describe");

            var events = EventsSince(cursor);
            Assert.That(events, Is.Not.Empty, "a component property change produced no events");
            Assert.That(events.SelectMany(e => e["ids"]).Select(x => x.Value<string>()), Contains.Item(id));
            Assert.That(Sources(events), Has.All.EqualTo(RoboVisionJournal.SourceEditor));
        }

        [Test]
        public void APrefabInstanceOverrideIsJournalled()
        {
            if (!AssetDatabase.IsValidFolder(PrefabDirectory))
                AssetDatabase.CreateFolder("Assets", "RoboVisionJournalPrefabs");
            var source = new GameObject("JournalPrefab");
            source.AddComponent<BoxCollider>();
            var path = PrefabDirectory + "/JournalPrefab.prefab";
            var asset = PrefabUtility.SaveAsPrefabAsset(source, path);
            Object.DestroyImmediate(source);
            Assert.That(asset, Is.Not.Null, "prefab asset was not created");

            var instance = (GameObject)PrefabUtility.InstantiatePrefab(asset);
            _rv.Result("scene.describe");
            var cursor = Cursor();

            // An override on the instance, not an edit of the asset.
            instance.transform.localPosition = new Vector3(3f, 1f, 0f);
            _rv.Result("scene.describe");

            var events = EventsSince(cursor);
            Assert.That(events, Is.Not.Empty, "a prefab instance override produced no events");
            Assert.That(Sources(events), Has.All.EqualTo(RoboVisionJournal.SourceEditor));
        }

        [Test]
        public void MarkingASceneDirtyIsNotAChange()
        {
            _rv.CreateObject("Untouched");
            var cursor = Cursor();
            var revision = _rv.Result("scene.describe").Value<long>("revision");

            // A broad editor signal that carries no object information and does
            // not describe scene content. It must not manufacture history.
            EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
            _rv.Host.MarkDirty();

            Assert.That(_rv.Result("scene.describe").Value<long>("revision"), Is.EqualTo(revision),
                "marking a scene dirty advanced the scene revision");
            Assert.That(EventsSince(cursor), Is.Empty,
                "marking a scene dirty produced journal events");
        }

        [Test]
        public void SeveralMutationsInOneEditorUpdateEachGetOneEvent()
        {
            var cursor = Cursor();
            // No editor tick happens between these: this is the burst the
            // transport's per-update command budget actually produces.
            var a = _rv.CreateObject("Burst0");
            var b = _rv.CreateObject("Burst1");
            _rv.Call("object.transform", new JObject
            {
                ["object"] = a,
                ["local_position"] = new JArray(1f, 0f, 0f)
            });
            _rv.Call("object.delete", new JObject { ["object"] = b });

            var events = EventsSince(cursor);
            var sequences = events.Select(e => e.Value<long>("sequence")).ToList();
            Assert.That(sequences, Is.Ordered, "sequences arrived out of order");
            Assert.That(sequences.Distinct().Count(), Is.EqualTo(sequences.Count), "a sequence was reused");
            Assert.That(Types(events), Contains.Item(RoboVisionJournal.ObjectCreated));
            Assert.That(Types(events), Contains.Item(RoboVisionJournal.ObjectDeleted));
            Assert.That(Sources(events), Has.All.EqualTo(RoboVisionJournal.SourceAgent),
                "the agent's own burst was partly blamed on the editor");
        }

        [Test]
        public void ANoopMutationProducesNoRevisionAndNoEvent()
        {
            var id = _rv.CreateObject("Still", 2f, 0f, 0f);
            var cursor = Cursor();
            var revision = _rv.Result("scene.describe").Value<long>("revision");

            var same = _rv.Call("object.transform", new JObject
            {
                ["object"] = id,
                ["local_position"] = new JArray(2f, 0f, 0f)
            });
            Assert.That(same.Value<string>("outcome"), Is.EqualTo("noop"));
            Assert.That(same.Value<long>("revision"), Is.EqualTo(revision));
            Assert.That(EventsSince(cursor), Is.Empty, "a no-op produced journal events");
        }

        [Test]
        public void AnUnannouncedChangeIsJournalledWhenAReadFindsIt()
        {
            _rv.CreateObject("Anchor");
            var cursor = Cursor();

            // Nothing announces this: no Undo registration, no editor tick.
            var stray = new GameObject("NeverAnnounced");

            _rv.Result("scene.describe");
            var events = EventsSince(cursor);
            Assert.That(events, Is.Not.Empty,
                "a change discovered by an authoritative read was absorbed with nothing journalled");
            Assert.That(Sources(events), Has.All.EqualTo(RoboVisionJournal.SourceEditor));

            Object.DestroyImmediate(stray);
        }

        [Test]
        public void AnUnannouncedChangeIsJournalledWhenAMutationsResyncFindsIt()
        {
            var id = _rv.CreateObject("Subject");
            var cursor = Cursor();

            var stray = new GameObject("SlippedIn");

            // The mutation's own pre-mutation resync is the first authoritative
            // read. The editor change must not be absorbed into the agent's.
            _rv.Call("object.transform", new JObject
            {
                ["object"] = id,
                ["local_position"] = new JArray(7f, 0f, 0f)
            });

            var events = EventsSince(cursor);
            Assert.That(Sources(events), Contains.Item(RoboVisionJournal.SourceEditor),
                "the change found at resync was not attributed to the editor: "
                + events.ToString(Newtonsoft.Json.Formatting.None));
            Assert.That(Sources(events), Contains.Item(RoboVisionJournal.SourceAgent),
                "the agent's own mutation was not journalled");

            Object.DestroyImmediate(stray);
        }

        [Test]
        public void ACursorFromAReplacedDocumentIsRefused()
        {
            _rv.CreateObject("Resident");
            var stale = Cursor();
            var epochBefore = _rv.Result("scene.changes_since").Value<long>("epoch");

            // A different loaded world. Epoch numbers collide across documents —
            // both start at 1 — so the document is what has to distinguish them.
            Harness.FreshScene();
            _rv.Result("scene.describe");
            Assert.That(_rv.Result("scene.changes_since").Value<long>("epoch"), Is.EqualTo(epochBefore),
                "precondition: both sides must sit at the same epoch for this to be the dangerous case");

            _rv.CreateObject("BrandNew");
            var refused = _rv.Call("scene.changes_since", new JObject { ["cursor"] = stale },
                ok: false, code: "STALE_DOCUMENT");
            var data = (JObject)refused["error"]["data"];
            Assert.That(data.Value<string>("current_cursor"), Is.Not.Null.And.Not.Empty,
                "the refusal did not say where to resume from");
            Assert.That(data.Value<string>("current_document_incarnation"),
                Is.EqualTo(_rv.Result("scene.describe").Value<string>("document_incarnation")));
        }

        /// <summary>
        /// Saving gives the document a file. It does not load a different world.
        /// </summary>
        /// <remarks>
        /// It does, in Unity specifically, change how an object is addressed: an
        /// unsaved object has only a session handle, and saving is what earns it
        /// a durable GlobalObjectId. That is a real, client-visible change and
        /// is reported — unlike Blender, where identity is durable from the
        /// start and saving changes nothing at all.
        ///
        /// How it is reported is a known gap, pinned here so that fixing it has
        /// to be deliberate. An identity upgrade arrives as OBJECT_DELETED plus
        /// OBJECT_CREATED, which says an object was destroyed and another built,
        /// when one object simply became addressable. This is the Unity instance
        /// of the audit requirement in CROSS_EDITOR_STATE.md §11.1: control-plane
        /// identity changes must eventually be exposed as what they are.
        /// </remarks>
        [Test]
        public void SavingUpgradesIdentityWithoutLoadingADifferentDocument()
        {
            var sessionId = _rv.CreateObject("Persisted");
            Assert.That(sessionId, Does.StartWith("unity:session:"),
                "precondition: an unsaved object has only a session handle");
            var incarnation = _rv.Result("scene.describe").Value<string>("document_incarnation");
            var cursor = Cursor();

            var path = "Assets/RoboVisionJournalSaved.unity";
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), path);
            try
            {
                var after = _rv.Result("scene.describe");
                Assert.That(after.Value<string>("document_incarnation"), Is.EqualTo(incarnation),
                    "saving gave the document a file; it did not load a different world, so the "
                    + "incarnation must not rotate");

                var events = EventsSince(cursor);
                var ids = events.SelectMany(e => e["ids"]).Select(x => x.Value<string>()).ToList();
                Assert.That(ids, Contains.Item(sessionId),
                    "the retired session handle was not named, so a client holding it is not told");
                Assert.That(ids.Any(id => id.StartsWith("unity:GlobalObjectId_")), Is.True,
                    "the durable identity the object acquired was not named");
                Assert.That(Sources(events), Has.All.EqualTo(RoboVisionJournal.SourceEditor),
                    "a save is not the agent's mutation");
                // The known gap, asserted rather than glossed.
                Assert.That(Types(events), Is.EquivalentTo(new[]
                    {
                        RoboVisionJournal.ObjectDeleted, RoboVisionJournal.ObjectCreated
                    }),
                    "an identity upgrade is reported as a delete and a create; see §11.1");
            }
            finally
            {
                AssetDatabase.DeleteAsset(path);
            }
        }

        [Test]
        public void ImpossibleCursorsAreRefusedAndAlwaysOfferAWayBack()
        {
            var current = Cursor();
            var parts = current.Split(':');
            var ahead = parts[0] + ":" + parts[1] + ":" + parts[2] + ":" + (long.Parse(parts[3]) + 500);

            var cases = new JToken[]
            {
                ahead, "", "nonsense", "rvcursor:a:b", "rvcursor:a:b:c",
                parts[0] + ":" + parts[1] + ":0:1", JValue.CreateNull(), new JValue(7)
            };
            foreach (var value in cases)
            {
                var refused = _rv.Call("scene.changes_since", new JObject { ["cursor"] = value },
                    ok: false, code: "INVALID_PARAMS");
                var offered = ((JObject)refused["error"]["data"]).Value<string>("current_cursor");
                Assert.That(offered, Is.Not.Null.And.Not.Empty,
                    "the refusal of " + value + " did not say where to resume from");
            }

            // The parameters this replaced must be refused, not quietly ignored.
            _rv.Call("scene.changes_since", new JObject { ["after"] = 0 }, ok: false, code: "INVALID_PARAMS");
            _rv.Call("scene.changes_since", new JObject { ["epoch"] = 1 }, ok: false, code: "INVALID_PARAMS");
        }

        [Test]
        public void ForgottenHistoryIsRefusedRatherThanTruncated()
        {
            var aged = Cursor();
            for (var i = 0; i < RoboVisionJournal.RetainedEvents / 2 + 8; i++)
            {
                var id = _rv.CreateObject("Churn" + i);
                _rv.Call("object.delete", new JObject { ["object"] = id });
            }

            var refused = _rv.Call("scene.changes_since", new JObject { ["cursor"] = aged },
                ok: false, code: "SEQUENCE_TOO_OLD");
            Assert.That(((JObject)refused["error"]["data"]).Value<string>("current_cursor"),
                Is.Not.Null.And.Not.Empty, "the refusal did not say where to resume from");

            // A current cursor still works, so the refusal is about retention
            // rather than the journal being broken.
            Assert.That(EventsSince(Cursor()), Is.Empty);
        }

        [Test]
        public void TransactionContaminationIsStillDetected()
        {
            var id = _rv.CreateObject("Guarded");
            var tx = _rv.BeginTransaction("contamination");
            _rv.Call("object.transform", new JObject
            {
                ["object"] = id,
                ["local_position"] = new JArray(1f, 0f, 0f)
            });

            // A human edit inside the transaction's window, which rollback
            // cannot claim to have restored.
            var stray = new GameObject("Intruder");
            _rv.Result("scene.describe");

            var response = _rv.Call("transaction.rollback", new JObject { ["transaction"] = tx }, ok: false);
            Assert.That(response["error"].Value<string>("code"), Is.EqualTo("TRANSACTION_CONTAMINATED"),
                "an out-of-band edit inside a transaction was not detected after the refactor");

            Object.DestroyImmediate(stray);
        }
    }
}
