using System.IO;
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
    /// Which references survive a reload and which are session-scoped.
    /// </summary>
    /// <remarks>
    /// This is the contract an agent has to be able to rely on, so it is
    /// asserted rather than described:
    ///
    /// <c>unity:GlobalObjectId_*</c> is durable. It is issued once an object
    /// lives in a saved scene or asset, and it resolves again after the scene is
    /// closed and reopened.
    ///
    /// <c>unity:session:*</c> is not durable. It exists for objects that have no
    /// persistent identity yet — an unsaved scene object — and it is valid only
    /// for the current editor session and domain. The host reports
    /// <c>identity_persistent</c> so a client never has to guess which it holds.
    /// </remarks>
    public sealed class RoboVisionIdentityLifecycleTests
    {
        private const string SceneDirectory = "Assets/RoboVisionGate4";
        private Harness _rv;
        private string _scenePath;

        [SetUp]
        public void SetUp()
        {
            Harness.FreshScene();
            _rv = new Harness("identity");
            if (!Directory.Exists(SceneDirectory)) Directory.CreateDirectory(SceneDirectory);
            _scenePath = SceneDirectory + "/IdentityLifecycle.unity";
        }

        [TearDown]
        public void TearDown()
        {
            _rv.ResetTransactions();
            if (File.Exists(_scenePath)) AssetDatabase.DeleteAsset(_scenePath);
            if (Directory.Exists(SceneDirectory) && Directory.GetFiles(SceneDirectory).Length == 0)
                AssetDatabase.DeleteAsset(SceneDirectory);
            AssetDatabase.Refresh();
        }

        [Test]
        public void UnsavedSceneObjectsGetASessionHandleMarkedNonPersistent()
        {
            var id = _rv.CreateObject("Unsaved");
            var inspected = _rv.Result("object.inspect", new JObject { ["object"] = id });

            Assert.That(id, Does.StartWith("unity:session:"));
            Assert.That(inspected.Value<bool>("identity_persistent"), Is.False);
        }

        [Test]
        public void SavingTheSceneUpgradesIdentityToAGlobalObjectId()
        {
            var sessionId = _rv.CreateObject("Persisted");
            var scene = SceneManager.GetActiveScene();
            Assert.That(EditorSceneManager.SaveScene(scene, _scenePath), Is.True, "the scene did not save");

            var inspected = _rv.Result("object.inspect", new JObject { ["object"] = sessionId });
            var durableId = inspected.Value<string>("id");

            Assert.That(durableId, Does.StartWith("unity:GlobalObjectId_"),
                "an object in a saved scene must be addressable by GlobalObjectId");
            Assert.That(inspected.Value<bool>("identity_persistent"), Is.True);
            Assert.That(durableId, Is.Not.EqualTo(sessionId));
        }

        [Test]
        public void GlobalObjectIdSurvivesClosingAndReopeningTheScene()
        {
            _rv.CreateObject("Durable");
            var scene = SceneManager.GetActiveScene();
            EditorSceneManager.SaveScene(scene, _scenePath);

            var durableId = _rv.Result("object.inspect",
                new JObject { ["object"] = _rv.Result("scene.describe")["scenes"][0]["objects"][0].Value<string>("id") })
                .Value<string>("id");
            Assert.That(durableId, Does.StartWith("unity:GlobalObjectId_"));

            // Close the scene entirely, then reopen it from disk.
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            Assert.That(GameObject.Find("Durable"), Is.Null, "the scene was not actually closed");
            EditorSceneManager.OpenScene(_scenePath, OpenSceneMode.Single);

            var reopened = _rv.Result("object.inspect", new JObject { ["object"] = durableId });
            Assert.That(reopened.Value<string>("name"), Is.EqualTo("Durable"),
                "a GlobalObjectId did not resolve after the scene was reopened");
            Assert.That(reopened.Value<bool>("identity_persistent"), Is.True);
        }

        [Test]
        public void SessionHandleFromAClosedSceneStopsResolving()
        {
            var sessionId = _rv.CreateObject("Ephemeral");
            Assert.That(sessionId, Does.StartWith("unity:session:"));

            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

            _rv.Call("object.inspect", new JObject { ["object"] = sessionId },
                ok: false, code: "NOT_FOUND");
        }

        [Test]
        public void AdditiveScenesAreObservedAndTheirObjectsAddressable()
        {
            _rv.CreateObject("InFirstScene");
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), _scenePath);

            var additive = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            var extra = new GameObject("InAdditiveScene");
            Undo.RegisterCreatedObjectUndo(extra, "additive object");
            SceneManager.MoveGameObjectToScene(extra, additive);

            var described = _rv.Result("scene.describe");
            Assert.That(described["scenes"].Count(), Is.GreaterThanOrEqualTo(2),
                "the host did not report the additively loaded scene");

            var names = described["scenes"].SelectMany(s => s["objects"]).Select(o => o.Value<string>("name"));
            Assert.That(names, Contains.Item("InAdditiveScene"));
            Assert.That(names, Contains.Item("InFirstScene"));
        }

        [Test]
        public void FingerprintCoversObjectsInEveryLoadedScene()
        {
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), _scenePath);
            var before = _rv.Fingerprint();

            var additive = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            var extra = new GameObject("AdditiveResident");
            Undo.RegisterCreatedObjectUndo(extra, "additive object");
            SceneManager.MoveGameObjectToScene(extra, additive);

            Assert.That(_rv.Fingerprint(), Is.Not.EqualTo(before),
                "an object added in an additive scene did not move the fingerprint");
        }
    }
}
