using System.Linq;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>Scene observation, mutation and the identity that addresses it.</summary>
    public sealed class RoboVisionSceneStateTests
    {
        private Harness _rv;

        [SetUp]
        public void SetUp()
        {
            Harness.FreshScene();
            _rv = new Harness("scene");
        }

        [TearDown]
        public void TearDown() => _rv.ResetTransactions();

        [Test]
        public void SnapshotFingerprintIsStableWhenNothingChanges()
        {
            _rv.CreateObject("Stable", 1f, 2f, 3f);
            var first = _rv.Fingerprint();
            var second = _rv.Fingerprint();
            Assert.That(second, Is.EqualTo(first), "reading the scene twice produced different fingerprints");
        }

        [Test]
        public void FingerprintMovesWhenTheSceneMoves()
        {
            var id = _rv.CreateObject("Mover");
            var before = _rv.Fingerprint();
            _rv.Call("object.transform", new JObject
            {
                ["object"] = id,
                ["local_position"] = new JArray(5f, 0f, 0f)
            });
            Assert.That(_rv.Fingerprint(), Is.Not.EqualTo(before));
        }

        [Test]
        public void CreateInspectAndMutateRoundTrip()
        {
            var id = _rv.CreateObject("Subject", 1f, 2f, 3f);
            Assert.That(id, Is.Not.Null.And.Not.Empty);

            var inspected = _rv.Result("object.inspect", new JObject { ["object"] = id });
            Assert.That(inspected.Value<string>("name"), Is.EqualTo("Subject"));
            var position = inspected["local_position"].Select(v => (float)v).ToArray();
            Assert.That(position, Is.EqualTo(new[] { 1f, 2f, 3f }).Within(1e-4f));

            _rv.Call("object.transform", new JObject
            {
                ["object"] = id,
                ["local_position"] = new JArray(-4f, 0.5f, 9f)
            });

            var after = _rv.Result("object.inspect", new JObject { ["object"] = id });
            var moved = after["local_position"].Select(v => (float)v).ToArray();
            Assert.That(moved, Is.EqualTo(new[] { -4f, 0.5f, 9f }).Within(1e-4f));

            // The editor, not just the response, must agree.
            var live = GameObject.Find("Subject");
            Assert.That(live, Is.Not.Null);
            Assert.That(live.transform.localPosition.x, Is.EqualTo(-4f).Within(1e-4f));
        }

        [Test]
        public void DeleteRemovesTheObjectFromTheScene()
        {
            var id = _rv.CreateObject("Doomed");
            _rv.Call("object.delete", new JObject { ["object"] = id });
            Assert.That(GameObject.Find("Doomed"), Is.Null);
            _rv.Call("object.inspect", new JObject { ["object"] = id }, ok: false, code: "NOT_FOUND");
        }

        [Test]
        public void DiffReportsCreationAgainstAnEarlierSnapshot()
        {
            var snapshot = _rv.Result("scene.snapshot").Value<string>("snapshot");
            var id = _rv.CreateObject("Appeared");

            var diff = _rv.Result("scene.diff", new JObject { ["from_snapshot"] = snapshot });
            Assert.That(diff.Value<bool>("equal"), Is.False);
            var created = diff["created"].Select(entry => entry.Value<string>("id")).ToList();
            Assert.That(created, Contains.Item(id));
        }

        [Test]
        public void HierarchyChangesAreVisibleThroughInspection()
        {
            var parentId = _rv.CreateObject("Parent");
            var childId = _rv.CreateObject("Child");

            var parent = GameObject.Find("Parent");
            var child = GameObject.Find("Child");
            Undo.SetTransformParent(child.transform, parent.transform, "test parent");

            var inspected = _rv.Result("object.inspect", new JObject { ["object"] = childId });
            Assert.That(inspected.Value<string>("parent"), Is.EqualTo(parentId),
                "the host did not report the new parent");
        }

        [Test]
        public void SessionIdentityAddressesTheObjectItWasIssuedFor()
        {
            var first = _rv.CreateObject("First");
            var second = _rv.CreateObject("Second");

            Assert.That(first, Is.Not.EqualTo(second), "two objects were issued the same handle");
            Assert.That(first, Does.StartWith("unity:session:"),
                "an unsaved scene object has no GlobalObjectId and must get a session handle");

            Assert.That(_rv.Result("object.inspect", new JObject { ["object"] = first }).Value<string>("name"),
                Is.EqualTo("First"));
            Assert.That(_rv.Result("object.inspect", new JObject { ["object"] = second }).Value<string>("name"),
                Is.EqualTo("Second"));
        }

        [Test]
        public void SessionIdentityIsStableAcrossRepeatedInspection()
        {
            var id = _rv.CreateObject("Repeat");
            for (var i = 0; i < 5; i++)
            {
                var seen = _rv.Result("object.inspect", new JObject { ["object"] = id }).Value<string>("id");
                Assert.That(seen, Is.EqualTo(id), "the handle changed between inspections");
            }
        }

        [Test]
        public void SessionHandleIsReportedAsNonPersistent()
        {
            var id = _rv.CreateObject("Ephemeral");
            var inspected = _rv.Result("object.inspect", new JObject { ["object"] = id });
            Assert.That(inspected.Value<bool>("identity_persistent"), Is.False,
                "a session handle must not be advertised as durable identity");
        }

        [Test]
        public void DestroyedObjectsStopResolving()
        {
            var id = _rv.CreateObject("Transient");
            Object.DestroyImmediate(GameObject.Find("Transient"));
            _rv.Call("object.inspect", new JObject { ["object"] = id }, ok: false, code: "NOT_FOUND");
        }

        [Test]
        public void ComponentStateIsPartOfTheObjectRecord()
        {
            var id = _rv.CreateObject("WithComponents");
            var inspected = _rv.Result("object.inspect", new JObject { ["object"] = id });
            var components = inspected["components"].Select(v => (string)v).ToList();
            Assert.That(components, Contains.Item("UnityEngine.Transform"));
        }
    }
}
