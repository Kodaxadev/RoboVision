using System.IO;
using System.Linq;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// Prefab semantics, which is where "just edit the object" stops being true.
    /// </summary>
    /// <remarks>
    /// A prefab instance's components are not free-standing objects: edits
    /// become overrides, and the asset behind them has its own identity. An
    /// agent that cannot tell an instance from its source will eventually edit
    /// the wrong one.
    /// </remarks>
    public sealed class RoboVisionPrefabTests
    {
        private const string Directory = "Assets/RoboVisionPrefabs";
        private Harness _rv;

        [SetUp]
        public void SetUp()
        {
            Harness.FreshScene();
            _rv = new Harness("prefab");
            if (!System.IO.Directory.Exists(Directory)) System.IO.Directory.CreateDirectory(Directory);
            AssetDatabase.Refresh();
        }

        [TearDown]
        public void TearDown()
        {
            _rv.ResetTransactions();
            if (AssetDatabase.IsValidFolder(Directory)) AssetDatabase.DeleteAsset(Directory);
            AssetDatabase.Refresh();
        }

        private static GameObject SavePrefab(string name, out string path)
        {
            var source = new GameObject(name);
            source.AddComponent<BoxCollider>();
            path = Directory + "/" + name + ".prefab";
            var asset = PrefabUtility.SaveAsPrefabAsset(source, path);
            Object.DestroyImmediate(source);
            Assert.That(asset, Is.Not.Null, "prefab asset was not created at " + path);
            return asset;
        }

        [Test]
        public void PrefabInstanceIsVisibleAndAddressable()
        {
            var asset = SavePrefab("Simple", out _);
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(asset);
            Undo.RegisterCreatedObjectUndo(instance, "instantiate prefab");

            var described = _rv.Result("scene.describe");
            var entry = described["scenes"]
                .SelectMany(scene => scene["objects"])
                .FirstOrDefault(o => o.Value<string>("name") == "Simple");
            Assert.That(entry, Is.Not.Null, "the prefab instance was not reported in the scene");

            var id = entry.Value<string>("id");
            var inspected = _rv.Result("object.inspect", new JObject { ["object"] = id });
            Assert.That(inspected.Value<string>("name"), Is.EqualTo("Simple"));
            Assert.That(PrefabUtility.IsPartOfPrefabInstance(instance), Is.True,
                "the object under test is not actually a prefab instance");
        }

        [Test]
        public void EditingAnInstancePropertyCreatesAnOverrideAndLeavesTheAssetAlone()
        {
            var asset = SavePrefab("Overridden", out var path);
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(asset);
            Undo.RegisterCreatedObjectUndo(instance, "instantiate prefab");

            var id = _rv.Result("scene.describe")["scenes"]
                .SelectMany(scene => scene["objects"])
                .First(o => o.Value<string>("name") == "Overridden")
                .Value<string>("id");

            _rv.Call("object.transform", new JObject
            {
                ["object"] = id,
                ["local_position"] = new JArray(5f, 0f, 0f)
            });

            Assert.That(instance.transform.localPosition.x, Is.EqualTo(5f).Within(1e-4f));
            var overrides = PrefabUtility.GetPropertyModifications(instance);
            Assert.That(overrides, Is.Not.Null.And.Not.Empty,
                "editing an instance did not record a prefab override");

            // The asset on disk must be untouched by an instance-level edit.
            var reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            Assert.That(reloaded.transform.localPosition.x, Is.EqualTo(0f).Within(1e-4f),
                "an instance edit leaked into the prefab asset");
        }

        [Test]
        public void NestedPrefabInstancesAreReported()
        {
            var childAsset = SavePrefab("Child", out _);

            var parentSource = new GameObject("Parent");
            var nested = (GameObject)PrefabUtility.InstantiatePrefab(childAsset);
            nested.transform.SetParent(parentSource.transform);
            var parentPath = Directory + "/Parent.prefab";
            var parentAsset = PrefabUtility.SaveAsPrefabAsset(parentSource, parentPath);
            Object.DestroyImmediate(parentSource);
            Assert.That(parentAsset, Is.Not.Null);

            var instance = (GameObject)PrefabUtility.InstantiatePrefab(parentAsset);
            Undo.RegisterCreatedObjectUndo(instance, "instantiate nested prefab");

            var names = _rv.Result("scene.describe")["scenes"]
                .SelectMany(scene => scene["objects"])
                .Select(o => o.Value<string>("name"))
                .ToList();
            Assert.That(names, Contains.Item("Parent"));
            Assert.That(names, Contains.Item("Child"),
                "the nested prefab's child was not reported: " + string.Join(", ", names));

            var childEntry = _rv.Result("scene.describe")["scenes"]
                .SelectMany(scene => scene["objects"])
                .First(o => o.Value<string>("name") == "Child");
            Assert.That(childEntry.Value<string>("parent"), Is.Not.Null,
                "the nested child did not report its parent");
        }

        [Test]
        public void ObjectsInsidePrefabStageAreObserved()
        {
            SavePrefab("Staged", out var path);
            var stage = UnityEditor.SceneManagement.PrefabStageUtility.OpenPrefab(path);
            try
            {
                Assert.That(stage, Is.Not.Null, "the prefab stage did not open");

                var names = _rv.Result("scene.describe")["scenes"]
                    .SelectMany(scene => scene["objects"])
                    .Select(o => o.Value<string>("name"))
                    .ToList();
                Assert.That(names, Contains.Item("Staged"),
                    "the host did not observe the prefab stage contents: " + string.Join(", ", names));
            }
            finally
            {
                UnityEditor.SceneManagement.StageUtility.GoToMainStage();
            }
        }

        [Test]
        public void PrefabAssetObjectsAreNotConfusedWithSceneInstances()
        {
            var asset = SavePrefab("Distinct", out _);
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(asset);
            Undo.RegisterCreatedObjectUndo(instance, "instantiate prefab");

            var described = _rv.Result("scene.describe");
            var matches = described["scenes"]
                .SelectMany(scene => scene["objects"])
                .Where(o => o.Value<string>("name") == "Distinct")
                .ToList();

            Assert.That(matches.Count, Is.EqualTo(1),
                "the prefab asset and its scene instance were both reported as scene objects");
        }
    }
}
