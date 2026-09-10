using System.Collections;
using System.IO;
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
    /// The evidence Unity can produce and Blender structurally cannot.
    /// </summary>
    /// <remarks>
    /// A domain reload destroys and rebuilds every static in this package while
    /// the editing world it was operating on stays exactly where it was. That is
    /// the case the durable operation records exist for: the bridge that reserved
    /// an idempotency key is gone, and a redelivery of that key must still be
    /// recognised rather than executed a second time.
    ///
    /// The direction of the argument matters and is asserted here. World
    /// continuity is established by reading the editor back and matching it
    /// exactly, and only then may the ledger be adopted. The ledger is never
    /// evidence that the world is the same — that would be circular, and a file
    /// left behind by a world that is gone would then authorise replaying its own
    /// contents into a world it never described.
    /// </remarks>
    public sealed class RoboVisionLedgerReloadTests
    {
        private const string Folder = "Assets/RoboVisionLedger";
        private const string ScenePath = Folder + "/Ledger.unity";
        private const string WorldKey = "RoboVision.Tests.LedgerWorld";
        private const string BridgeKey = "RoboVision.Tests.LedgerBridge";
        private const string PathKey = "RoboVision.Tests.LedgerPath";
        private const string CountKey = "RoboVision.Tests.LedgerCount";

        private Harness _rv;

        [SetUp]
        public void SetUp()
        {
            if (!Directory.Exists(Folder)) Directory.CreateDirectory(Folder);
            AssetDatabase.Refresh();
            Harness.FreshScene();
            _rv = new Harness("ledger");
            _rv.Result("scene.describe");
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var key in new[] { WorldKey, BridgeKey, PathKey, CountKey })
                SessionState.EraseString(key);
            if (PrefabStageUtility.GetCurrentPrefabStage() != null) StageUtility.GoToMainStage();
            if (AssetDatabase.IsValidFolder(Folder)) AssetDatabase.DeleteAsset(Folder);
            AssetDatabase.Refresh();
        }

        private static JObject Key(string key, int attempt = 1)
        {
            return new JObject { ["idempotency_key"] = key, ["attempt"] = attempt };
        }

        private static int ObjectCount()
        {
            return Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None).Length;
        }

        private static string SavePrefab(string name)
        {
            var source = new GameObject(name);
            var path = Folder + "/" + name + ".prefab";
            PrefabUtility.SaveAsPrefabAsset(source, path);
            Object.DestroyImmediate(source);
            return path;
        }

        /// <summary>Save first: an unsaved scene is re-addressed by the reload.</summary>
        private void SaveTheWorld()
        {
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), ScenePath);
            _rv.Result("scene.describe");
        }

        [UnityTest]
        public IEnumerator ASameWorldDomainReloadResumesTheOperationRecords()
        {
            SaveTheWorld();
            SessionState.SetString(WorldKey, _rv.Host.WorldIncarnation);
            SessionState.SetString(BridgeKey, _rv.Host.Bridge);
            SessionState.SetString(PathKey, _rv.Host.Ledger.Path);

            _rv.Call("object.create", new JObject { ["name"] = "Durable" }, envelope: Key("k-reload"));
            SessionState.SetString(CountKey, ObjectCount().ToString());

            // Play mode is what rebuilds the scripting domain here. Its own state
            // domain is a separate question, proven elsewhere; what matters is
            // that the statics on the far side are new ones.
            yield return new EnterPlayMode();
            yield return new ExitPlayMode();

            var after = new Harness("ledger-after");
            after.Result("scene.describe");

            Assert.That(after.Host.Bridge, Is.Not.EqualTo(SessionState.GetString(BridgeKey, null)),
                "the bridge was not rebuilt, so this proves nothing about surviving one");
            Assert.That(after.Host.WorldIncarnation,
                Is.EqualTo(SessionState.GetString(WorldKey, null)),
                "the world was not recognised as the same one");
            Assert.That(after.Host.WorldResumed, Is.True,
                "the world was not proved continuous, so nothing may be adopted from disk");
            Assert.That(after.Host.Ledger.Path, Is.EqualTo(SessionState.GetString(PathKey, null)),
                "the resumed world is reading a different ledger");

            var expected = int.Parse(SessionState.GetString(CountKey, "-1"));
            var replay = after.Call("object.create", new JObject { ["name"] = "Durable" },
                envelope: Key("k-reload", 2));
            Assert.That(replay.Value<bool>("replayed"), Is.True,
                "a bridge rebuilt under a surviving world forgot an operation it had applied");
            Assert.That(ObjectCount(), Is.EqualTo(expected),
                "the redelivery applied a second time across the reload");
            Assert.That(replay["original_execution"].Value<string>("world_incarnation"),
                Is.EqualTo(after.Host.WorldIncarnation));
        }

        [UnityTest]
        public IEnumerator TheLedgerIsNotEvidenceThatTheWorldIsTheSame()
        {
            SaveTheWorld();
            _rv.Call("object.create", new JObject { ["name"] = "Orphaned" }, envelope: Key("k-unproven"));
            SessionState.SetString(PathKey, _rv.Host.Ledger.Path);
            SessionState.SetString(WorldKey, _rv.Host.WorldIncarnation);

            // Discard the only thing that can prove continuity, and change
            // nothing else. The scene is untouched and the records are still on
            // disk, so if the file were self-authorising this would replay.
            WorldMemory.Forget();

            yield return new EnterPlayMode();
            yield return new ExitPlayMode();

            var after = new Harness("ledger-unproven");
            after.Result("scene.describe");

            Assert.That(File.Exists(SessionState.GetString(PathKey, null)), Is.True,
                "the test needs the previous world's records to still exist on disk");
            Assert.That(after.Host.WorldResumed, Is.False);
            Assert.That(after.Host.WorldIncarnation,
                Is.Not.EqualTo(SessionState.GetString(WorldKey, null)));
            Assert.That(after.Host.Ledger.Path, Is.Not.EqualTo(SessionState.GetString(PathKey, null)),
                "an unproved world adopted the previous world's ledger file");

            // Unknown here, and a retry of an unknown key is refused rather than
            // executed: it may already have applied in the world that is gone.
            after.Call("object.create", new JObject { ["name"] = "Orphaned" },
                ok: false, code: "INDETERMINATE", envelope: Key("k-unproven", 2));
        }

        [Test]
        public void AReplacedWorldDoesNotInheritTheDurableRecords()
        {
            _rv.Call("object.create", new JObject { ["name"] = "Doomed" }, envelope: Key("k-replaced"));
            var before = _rv.Host.Ledger.Path;

            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            _rv.Result("scene.describe");

            Assert.That(_rv.Host.Ledger.Path, Is.Not.EqualTo(before),
                "a replaced world kept writing to the previous world's ledger");
            // An idempotency key names an operation planned against one editing
            // context, and a world that has been replaced is not that context
            // however recently it was.
            _rv.Call("object.create", new JObject { ["name"] = "Doomed" },
                ok: false, code: "INDETERMINATE", envelope: Key("k-replaced", 2));
        }

        /// <summary>
        /// Every way of replacing the world, and none of them inherits the records.
        /// </summary>
        /// <remarks>
        /// Four transitions, because Unity has four ways to leave an editing
        /// context and only one of them is a scene load. Entering a Prefab Stage
        /// changes the world while the main stage's scene stays loaded with its
        /// handle unchanged, which is not detectable from the loaded scene set at
        /// all — so a durable record keyed on anything less than the world would
        /// follow the agent into a universe it was never planned against.
        /// </remarks>
        [Test]
        public void EveryWorldReplacementDefeatsTheDurableRecords()
        {
            var a = SavePrefab("LedgerA");
            var b = SavePrefab("LedgerB");
            var seen = new System.Collections.Generic.List<string>();

            void Boundary(string name, System.Action transition)
            {
                var key = "k-" + name;
                _rv.Call("object.create", new JObject { ["name"] = name }, envelope: Key(key));
                var before = _rv.Host.Ledger.Path;
                seen.Add(before);

                transition();
                _rv.Result("scene.describe");

                Assert.That(_rv.Host.Ledger.Path, Is.Not.EqualTo(before),
                    name + ": a replaced world kept the previous world's ledger");
                _rv.Call("object.create", new JObject { ["name"] = name },
                    ok: false, code: "INDETERMINATE", envelope: Key(key, 2));
            }

            Boundary("SingleLoad", () =>
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single));
            Boundary("IntoPrefab", () => PrefabStageUtility.OpenPrefab(a));
            Boundary("BetweenPrefabs", () => PrefabStageUtility.OpenPrefab(b));
            Boundary("BackToMain", StageUtility.GoToMainStage);

            seen.Add(_rv.Host.Ledger.Path);
            Assert.That(seen, Is.Unique, "two different worlds shared one ledger file");
        }

        [Test]
        public void AKeyFromAnotherWorldIsRefusedRatherThanReinterpreted()
        {
            // The pin is checked before anything else, so the refusal names the
            // world rather than the key.
            var refused = _rv.Call("object.create", new JObject { ["name"] = "Elsewhere" },
                ok: false, code: "STALE_WORLD",
                envelope: new JObject
                {
                    ["idempotency_key"] = "k-foreign",
                    ["attempt"] = 1,
                    ["expected_world"] = "rvworld:00000000-0000-0000-0000-000000000000"
                });
            Assert.That(refused["error"]["data"].Value<string>("current_world_incarnation"),
                Is.EqualTo(_rv.Host.WorldIncarnation));
            Assert.That(GameObject.Find("Elsewhere"), Is.Null);
        }
    }
}
