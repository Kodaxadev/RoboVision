using System;
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
    /// Which editing context is open, and what that is allowed to invalidate.
    /// </summary>
    /// <remarks>
    /// The world incarnation used to be the set of loaded scene handles, which
    /// answered the wrong question: opening a second scene additively changes
    /// what the main stage contains, not which context the editor is in. It was
    /// measured before it was changed — an empty additive open refused the
    /// client's cursor with a stale-world error and reset the revision from 2 to
    /// 0 — and these pin the corrected boundaries in both directions.
    ///
    /// "The additive scene's objects are visible" is not evidence for any of
    /// this. What matters is what happens to the incarnation, the revision and
    /// the client's position.
    /// </remarks>
    public sealed class RoboVisionWorldTests
    {
        private const string Folder = "Assets/RoboVisionWorld";
        private Harness _rv;

        [SetUp]
        public void SetUp()
        {
            Harness.FreshScene();
            _rv = new Harness("world");
            if (!System.IO.Directory.Exists(Folder)) System.IO.Directory.CreateDirectory(Folder);
            AssetDatabase.Refresh();
            // The scene swap FreshScene just performed replaces the world;
            // settle it so each test starts from a stable incarnation.
            _rv.Result("scene.describe");
        }

        [TearDown]
        public void TearDown()
        {
            _rv.ResetTransactions();
            if (PrefabStageUtility.GetCurrentPrefabStage() != null) StageUtility.GoToMainStage();
            if (AssetDatabase.IsValidFolder(Folder)) AssetDatabase.DeleteAsset(Folder);
            AssetDatabase.Refresh();
        }

        private string World() => _rv.Result("scene.describe").Value<string>("world_incarnation");
        private long Revision() => _rv.Result("scene.describe").Value<long>("revision");
        private string Cursor() => _rv.Result("scene.changes_since").Value<string>("cursor");

        private JArray EventsSince(string cursor)
        {
            return (JArray)_rv.Result("scene.changes_since", new JObject { ["cursor"] = cursor })["events"];
        }

        private static string[] Types(JArray events) =>
            events.Select(e => e.Value<string>("type")).ToArray();

        /// <summary>Unity refuses to open a scene additively while the untitled one is unsaved.</summary>
        private void SaveMain(string name)
        {
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), Folder + "/" + name + ".unity");
        }

        private string SavePrefab(string name)
        {
            var source = new GameObject(name);
            source.AddComponent<BoxCollider>();
            var path = Folder + "/" + name + ".prefab";
            PrefabUtility.SaveAsPrefabAsset(source, path);
            UnityEngine.Object.DestroyImmediate(source);
            return path;
        }

        // ------------------------------------------------------ same context

        [Test]
        public void OpeningASceneAdditivelyIsAnEditNotADifferentWorld()
        {
            _rv.CreateObject("Resident");
            SaveMain("AdditiveMain");
            var world = World();
            var revision = Revision();
            var cursor = Cursor();

            var additive = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            try
            {
                Assert.That(World(), Is.EqualTo(world),
                    "adding a scene to the main stage was reported as entering a different "
                    + "editing context, which voids every handle the client holds");

                // An empty additive scene moves the fingerprint and produces no
                // object difference at all, so it is exactly the case that used
                // to cost the journal its certainty.
                var events = EventsSince(cursor);
                Assert.That(Types(events), Is.EquivalentTo(new[] { RoboVisionJournal.SceneLoaded }),
                    "opening an empty scene reported: "
                    + events.ToString(Newtonsoft.Json.Formatting.None));
                Assert.That(_rv.Result("scene.changes_since").Value<bool>("certain"), Is.True,
                    "the host lost track of a scene it watched being opened");
                Assert.That(Revision(), Is.GreaterThan(revision),
                    "the world gained a scene and the revision did not move");
            }
            finally
            {
                EditorSceneManager.CloseScene(additive, true);
            }
        }

        [Test]
        public void ClosingAnAdditiveSceneReportsWhatLeftRatherThanReplacingTheWorld()
        {
            _rv.CreateObject("Resident");
            SaveMain("CloseMain");
            var additive = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            var guest = new GameObject("Guest");
            SceneManager.MoveGameObjectToScene(guest, additive);

            var world = World();
            var guestId = _rv.Result("scene.describe")["scenes"]
                .SelectMany(scene => scene["objects"])
                .First(o => o.Value<string>("name") == "Guest")
                .Value<string>("id");
            var cursor = Cursor();

            EditorSceneManager.CloseScene(additive, true);

            Assert.That(World(), Is.EqualTo(world),
                "closing an additive scene was reported as leaving the editing context");
            var events = EventsSince(cursor);
            Assert.That(Types(events), Contains.Item(RoboVisionJournal.SceneUnloaded),
                "the scene that left was not reported: "
                + events.ToString(Newtonsoft.Json.Formatting.None));
            Assert.That(events.Where(e => e.Value<string>("type") == RoboVisionJournal.ObjectDeleted)
                    .SelectMany(e => e["ids"]).Select(x => x.Value<string>()),
                Contains.Item(guestId),
                "an object that left with its scene was not reported as gone");
        }

        [Test]
        public void SwitchingTheActiveSceneKeepsTheWorldAndIsVisible()
        {
            _rv.CreateObject("Resident");
            SaveMain("SwitchMain");
            var main = SceneManager.GetActiveScene();
            var additive = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            try
            {
                var world = World();
                var cursor = Cursor();

                SceneManager.SetActiveScene(additive);

                Assert.That(World(), Is.EqualTo(world),
                    "changing which scene is active was reported as a different editing context");
                // Nothing authored moved, so nothing is journalled — but the
                // agent still has to be able to see where its next object will
                // land, so the flag is reported even though it is not hashed.
                Assert.That(EventsSince(cursor), Is.Empty,
                    "switching the active scene manufactured authored history");
                var active = _rv.Result("scene.describe")["scenes"]
                    .Where(s => s.Value<bool>("active")).ToList();
                Assert.That(active.Count, Is.EqualTo(1), "exactly one scene should be reported active");
                Assert.That(active[0].Value<string>("handle"),
                    Is.EqualTo(additive.handle.ToString()),
                    "the host reported a different scene as active than the editor has");
            }
            finally
            {
                SceneManager.SetActiveScene(main);
                EditorSceneManager.CloseScene(additive, true);
            }
        }

        // ------------------------------------------------------- new context

        [Test]
        public void ASingleModeLoadReplacesTheWorldAndRefusesItsCursors()
        {
            _rv.CreateObject("Doomed");
            var world = World();
            var cursor = Cursor();

            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

            Assert.That(World(), Is.Not.EqualTo(world),
                "nothing of the previous editing universe is loaded and the world did not rotate");
            Assert.That(Revision(), Is.EqualTo(0), "a replaced world did not restart its revision");
            var refused = _rv.Call("scene.changes_since", new JObject { ["cursor"] = cursor },
                ok: false, code: "STALE_WORLD");
            Assert.That(((JObject)refused["error"]["data"]).Value<string>("current_cursor"),
                Is.Not.Null.And.Not.Empty, "the refusal did not say where to resume from");
        }

        /// <summary>
        /// Entering and leaving a Prefab Stage are both changes of context.
        /// </summary>
        /// <remarks>
        /// Not deducible from the loaded scene set: probing showed the main
        /// stage's scene stays loaded behind an open Prefab Stage with its
        /// handle unchanged, and comes back with the same handle afterwards. The
        /// stage is therefore part of what identifies the world.
        ///
        /// Leaving is a new world rather than a return to the previous one for a
        /// reason worth stating: while the Prefab Stage was open the host was
        /// reading the prefab, so it has no history for the main stage over that
        /// period and cannot vouch for it. Resuming the old identity would claim
        /// continuity across a gap nothing observed.
        /// </remarks>
        [Test]
        public void EnteringAndLeavingAPrefabStageAreBothDifferentWorlds()
        {
            var path = SavePrefab("StageA");
            _rv.CreateObject("InMainStage");
            var mainWorld = World();
            var mainCursor = Cursor();

            PrefabStageUtility.OpenPrefab(path);
            var stageWorld = World();
            Assert.That(stageWorld, Is.Not.EqualTo(mainWorld),
                "opening a prefab for editing did not change the editing context");
            _rv.Call("scene.changes_since", new JObject { ["cursor"] = mainCursor },
                ok: false, code: "STALE_WORLD");
            var stageCursor = Cursor();

            StageUtility.GoToMainStage();
            var backWorld = World();
            Assert.That(backWorld, Is.Not.EqualTo(stageWorld),
                "leaving the prefab stage did not change the editing context");
            Assert.That(backWorld, Is.Not.EqualTo(mainWorld),
                "the host resumed a world it stopped observing while the prefab was open");
            _rv.Call("scene.changes_since", new JObject { ["cursor"] = stageCursor },
                ok: false, code: "STALE_WORLD");
        }

        [Test]
        public void OnePrefabStageToAnotherIsADifferentWorld()
        {
            var a = SavePrefab("PrefabA");
            var b = SavePrefab("PrefabB");

            PrefabStageUtility.OpenPrefab(a);
            var worldA = World();
            var cursorA = Cursor();

            PrefabStageUtility.OpenPrefab(b);
            Assert.That(World(), Is.Not.EqualTo(worldA),
                "editing a different prefab was reported as the same editing context");
            Assert.That(PrefabStageUtility.GetCurrentPrefabStage().assetPath, Is.EqualTo(b),
                "precondition: the second prefab is not the one open");
            _rv.Call("scene.changes_since", new JObject { ["cursor"] = cursorA },
                ok: false, code: "STALE_WORLD");
        }

        /// <summary>A snapshot is evidence about one world, and stops applying outside it.</summary>
        [Test]
        public void ASnapshotFromAReplacedWorldIsRefused()
        {
            _rv.CreateObject("Doomed");
            var snapshot = _rv.Result("scene.snapshot").Value<string>("snapshot");

            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            _rv.Result("scene.describe");

            var refused = _rv.Call("scene.diff", new JObject { ["from_snapshot"] = snapshot },
                ok: false, code: "STALE_WORLD");
            Assert.That(((JObject)refused["error"]["data"]).Value<string>("current_world_incarnation"),
                Is.EqualTo(World()), "the refusal did not name the world that is open");
        }
    }
}
