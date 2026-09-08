using System.Linq;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// SerializedObject/SerializedProperty editing, which is how Unity edits
    /// are supposed to be made: undoable, prefab-aware, and never by rewriting
    /// asset YAML.
    /// </summary>
    public sealed class RoboVisionSerializedTests
    {
        private Harness _rv;

        [SetUp]
        public void SetUp()
        {
            Harness.FreshScene();
            _rv = new Harness("serialized");
        }

        [TearDown]
        public void TearDown() => _rv.ResetTransactions();

        [Test]
        public void ComponentsCanBeAddedListedAndRemoved()
        {
            var id = _rv.CreateObject("Componentised");

            var added = _rv.Result("component.add", new JObject
            {
                ["object"] = id,
                ["type"] = "UnityEngine.BoxCollider"
            });
            Assert.That(added.Value<string>("type"), Is.EqualTo("UnityEngine.BoxCollider"));
            Assert.That(GameObject.Find("Componentised").GetComponent<BoxCollider>(), Is.Not.Null,
                "component.add did not reach the editor");

            var listed = _rv.Result("component.list", new JObject { ["object"] = id });
            var types = listed["components"].Select(entry => entry.Value<string>("type")).ToList();
            Assert.That(types, Contains.Item("UnityEngine.BoxCollider"));

            var componentId = listed["components"]
                .First(entry => entry.Value<string>("type") == "UnityEngine.BoxCollider")
                .Value<string>("id");
            _rv.Call("component.remove", new JObject { ["component"] = componentId });
            Assert.That(GameObject.Find("Componentised").GetComponent<BoxCollider>(), Is.Null,
                "component.remove did not reach the editor");
        }

        [Test]
        public void SerializedInspectEnumeratesProperties()
        {
            var id = _rv.CreateObject("Inspected");
            _rv.Call("component.add", new JObject { ["object"] = id, ["type"] = "UnityEngine.BoxCollider" });
            var componentId = _rv.Result("component.list", new JObject { ["object"] = id })["components"]
                .First(entry => entry.Value<string>("type") == "UnityEngine.BoxCollider")
                .Value<string>("id");

            var inspected = _rv.Result("serialized.inspect", new JObject { ["target"] = componentId });
            var paths = inspected["properties"].Select(entry => entry.Value<string>("path")).ToList();
            Assert.That(paths, Is.Not.Empty);
            Assert.That(paths, Has.Some.Contains("m_Size").Or.Some.Contains("m_Center"),
                "BoxCollider properties were not enumerated: " + string.Join(", ", paths));
        }

        [Test]
        public void SerializedSetWritesThroughToTheEditor()
        {
            var id = _rv.CreateObject("Sized");
            _rv.Call("component.add", new JObject { ["object"] = id, ["type"] = "UnityEngine.BoxCollider" });
            var componentId = _rv.Result("component.list", new JObject { ["object"] = id })["components"]
                .First(entry => entry.Value<string>("type") == "UnityEngine.BoxCollider")
                .Value<string>("id");

            var set = _rv.Result("serialized.set", new JObject
            {
                ["target"] = componentId,
                ["path"] = "m_IsTrigger",
                ["value"] = true
            });
            Assert.That(set.Value<bool>("changed"), Is.True);

            var collider = GameObject.Find("Sized").GetComponent<BoxCollider>();
            Assert.That(collider.isTrigger, Is.True, "serialized.set did not reach the component");

            // serialized.get returns the descriptor directly, while serialized.set
            // wraps it under "property". Asserting on both shapes keeps that
            // asymmetry from drifting unnoticed.
            var read = _rv.Result("serialized.get", new JObject
            {
                ["target"] = componentId,
                ["path"] = "m_IsTrigger"
            });
            Assert.That(read.Value<string>("path"), Is.EqualTo("m_IsTrigger"));
            Assert.That(read.Value<bool>("value"), Is.True,
                "serialized.get did not read back what serialized.set wrote");
            Assert.That(set["property"].Value<string>("path"), Is.EqualTo("m_IsTrigger"),
                "serialized.set must return the property it wrote");
        }

        [Test]
        public void SerializedSetRefusesToReplaceScriptReferences()
        {
            var id = _rv.CreateObject("ScriptGuard");
            _rv.Call("component.add", new JObject { ["object"] = id, ["type"] = "UnityEngine.BoxCollider" });
            var componentId = _rv.Result("component.list", new JObject { ["object"] = id })["components"]
                .First(entry => entry.Value<string>("type") == "UnityEngine.BoxCollider")
                .Value<string>("id");

            _rv.Call("serialized.set", new JObject
            {
                ["target"] = componentId,
                ["path"] = "m_Script",
                ["value"] = "anything"
            }, ok: false, code: "UNSUPPORTED");
        }

        [Test]
        public void UnknownSerializedPropertyIsNotFound()
        {
            var id = _rv.CreateObject("NoSuchProperty");
            _rv.Call("serialized.set", new JObject
            {
                ["target"] = id,
                ["path"] = "m_DefinitelyNotAProperty",
                ["value"] = 1
            }, ok: false, code: "NOT_FOUND");
        }

        [Test]
        public void SerializedMutationParticipatesInVerifiedRollback()
        {
            var id = _rv.CreateObject("RollbackProperty");
            _rv.Call("component.add", new JObject { ["object"] = id, ["type"] = "UnityEngine.BoxCollider" });
            var componentId = _rv.Result("component.list", new JObject { ["object"] = id })["components"]
                .First(entry => entry.Value<string>("type") == "UnityEngine.BoxCollider")
                .Value<string>("id");
            var baseline = _rv.Fingerprint();

            var tx = _rv.BeginTransaction("serialized rollback");
            _rv.Call("serialized.set", new JObject
            {
                ["target"] = componentId,
                ["path"] = "m_IsTrigger",
                ["value"] = true
            });
            Assert.That(GameObject.Find("RollbackProperty").GetComponent<BoxCollider>().isTrigger, Is.True);

            _rv.EndTransaction("transaction.rollback", tx);

            Assert.That(GameObject.Find("RollbackProperty").GetComponent<BoxCollider>().isTrigger, Is.False,
                "rollback reported success but the serialized property kept its new value");
            Assert.That(_rv.Fingerprint(), Is.EqualTo(baseline));
        }

        [Test]
        public void AssetBackedComponentReferencesAreReported()
        {
            var id = _rv.CreateObject("Renderer");
            _rv.Call("component.add", new JObject { ["object"] = id, ["type"] = "UnityEngine.MeshFilter" });
            var componentId = _rv.Result("component.list", new JObject { ["object"] = id })["components"]
                .First(entry => entry.Value<string>("type") == "UnityEngine.MeshFilter")
                .Value<string>("id");

            // A built-in mesh asset is persistent, so its reference must be
            // reported as an asset rather than as a session handle.
            var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
            var mesh = cube.GetComponent<MeshFilter>().sharedMesh;
            GameObject.Find("Renderer").GetComponent<MeshFilter>().sharedMesh = mesh;
            Object.DestroyImmediate(cube);

            var read = _rv.Result("serialized.get", new JObject
            {
                ["target"] = componentId,
                ["path"] = "m_Mesh"
            });
            Assert.That(read.Value<string>("property_type"), Is.EqualTo("ObjectReference"),
                "an asset-backed field must be reported as an object reference");
            Assert.That(read["value"], Is.Not.Null,
                "the referenced asset was not reported at all");
        }
    }
}
