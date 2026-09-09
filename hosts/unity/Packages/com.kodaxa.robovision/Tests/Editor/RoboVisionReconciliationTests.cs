using System.Linq;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// One reconciliation path, and what each answer's revision is worth.
    /// </summary>
    /// <remarks>
    /// These run without the host's editor subscriptions installed, which is not
    /// a shortcut: it is the missed-notification case, permanently. A change made
    /// directly to the scene is never announced, so an answer is only current if
    /// the call that produced it re-read the scene. That is also a real hazard in
    /// a running editor — hierarchyChanged fires on the next update while the
    /// transport dispatches up to eight requests per update, so a burst runs with
    /// nothing notified in between.
    /// </remarks>
    public sealed class RoboVisionReconciliationTests
    {
        private Harness _rv;

        [SetUp]
        public void SetUp()
        {
            Harness.FreshScene();
            _rv = new Harness("reconcile");
        }

        [TearDown]
        public void TearDown() => _rv.ResetTransactions();

        [Test]
        public void EveryToolDeclaresWhatItsAnswerIsWorth()
        {
            var methods = _rv.Result("system.capabilities", new JObject { ["limit"] = 500 })["methods"] as JArray;
            Assert.That(methods, Is.Not.Null.And.Not.Empty, "the capability catalog is empty");

            var cheap = new JObject();
            foreach (var entry in methods.OfType<JObject>())
            {
                var name = entry.Value<string>("name");
                var reads = entry.Value<string>("reads");
                Assert.That(
                    new[]
                    {
                        RoboVisionHost.ReadsAuthoritative,
                        RoboVisionHost.ReadsNotified,
                        RoboVisionHost.ReadsIndependent
                    },
                    Contains.Item(reads),
                    name + " declares an unknown read consistency: " + reads);
                if (entry.Value<bool>("mutating"))
                    Assert.That(reads, Is.EqualTo(RoboVisionHost.ReadsAuthoritative),
                        name + " mutates but does not re-read first");
                // Evidence is a different axis: it says a call produces a durable
                // artifact, not that it re-reads the scene.
                if (entry.Value<bool>("evidence"))
                    Assert.That(reads, Is.EqualTo(RoboVisionHost.ReadsAuthoritative),
                        name + " produces evidence from a baseline it did not establish");
                if (reads != RoboVisionHost.ReadsAuthoritative) cheap[name] = reads;
            }

            var expected = new JObject
            {
                ["scene.changes_since"] = RoboVisionHost.ReadsNotified,
                ["system.ping"] = RoboVisionHost.ReadsNotified,
                ["system.hello"] = RoboVisionHost.ReadsNotified,
                ["system.capabilities"] = RoboVisionHost.ReadsIndependent,
                ["system.method"] = RoboVisionHost.ReadsIndependent
            };
            Assert.That(JToken.DeepEquals(cheap, expected), Is.True,
                "the set of non-authoritative tools changed without review: " + cheap.ToString(Newtonsoft.Json.Formatting.None));
        }

        [Test]
        public void AnAuthoritativeReadDiscoversAnUnannouncedChange()
        {
            _rv.CreateObject("Anchor");
            var revisionBefore = _rv.Result("scene.describe").Value<long>("revision");

            // Nothing announces this: no Undo registration, no editor tick.
            var stray = new GameObject("Unannounced");

            var response = _rv.Call("scene.describe");
            Assert.That(response.Value<string>("consistency"),
                Is.EqualTo(RoboVisionHost.ReadsAuthoritative));

            var described = (JObject)response["result"];
            var names = described["scenes"].SelectMany(scene => scene["objects"])
                .Select(o => o.Value<string>("name")).ToList();
            Assert.That(names, Contains.Item("Unannounced"),
                "an authoritative read did not return current state");
            Assert.That(described.Value<long>("revision"), Is.Not.EqualTo(revisionBefore),
                "current state was returned stamped with the revision of the state before it");

            Object.DestroyImmediate(stray);
        }

        [Test]
        public void ACheapReadDoesNotGoLookingForOne()
        {
            _rv.CreateObject("Anchor");
            var revisionBefore = _rv.Result("scene.describe").Value<long>("revision");

            var stray = new GameObject("AlsoUnannounced");

            var ping = _rv.Call("system.ping");
            Assert.That(ping.Value<string>("consistency"), Is.EqualTo(RoboVisionHost.ReadsNotified));
            Assert.That(ping.Value<long>("revision"), Is.EqualTo(revisionBefore),
                "a cheap read performed an authoritative reconcile; the class is decoration if it does");

            var catalog = _rv.Call("system.capabilities", new JObject { ["limit"] = 1 });
            Assert.That(catalog.Value<string>("consistency"), Is.EqualTo(RoboVisionHost.ReadsIndependent));
            Assert.That(catalog.Value<long>("revision"), Is.EqualTo(revisionBefore),
                "reading the catalog touched the scene");

            // The change is still there, and the next authoritative read finds it.
            Assert.That(_rv.Result("scene.describe").Value<long>("revision"), Is.Not.EqualTo(revisionBefore),
                "no read ever discovered the change");

            Object.DestroyImmediate(stray);
        }

        [Test]
        public void AMutationThatChangesNothingIsANoop()
        {
            var id = _rv.CreateObject("Static", 2f, 0f, 0f);
            var revision = _rv.Result("scene.describe").Value<long>("revision");

            var same = _rv.Call("object.transform", new JObject
            {
                ["object"] = id,
                ["local_position"] = new JArray(2f, 0f, 0f)
            });
            Assert.That(same.Value<string>("outcome"), Is.EqualTo("noop"),
                "a transform that changed nothing did not report noop");
            Assert.That(same.Value<long>("revision"), Is.EqualTo(revision),
                "a no-op advanced the scene revision; revision names state, not commands run");

            var moved = _rv.Call("object.transform", new JObject
            {
                ["object"] = id,
                ["local_position"] = new JArray(5f, 0f, 0f)
            });
            Assert.That(moved.Value<string>("outcome"), Is.EqualTo("applied"));
            Assert.That(moved.Value<long>("revision"), Is.EqualTo(revision + 1),
                "a real change did not advance the revision");
        }

        [Test]
        public void ARejectedMutationDoesNotAdvanceTheRevision()
        {
            _rv.CreateObject("Bystander");
            var revision = _rv.Result("scene.describe").Value<long>("revision");

            _rv.Call("object.transform", new JObject
            {
                ["object"] = "unity:GlobalObjectId_V1-0-00000000000000000000000000000000-0-0",
                ["local_position"] = new JArray(1f, 1f, 1f)
            }, ok: false);

            Assert.That(_rv.Result("scene.describe").Value<long>("revision"), Is.EqualTo(revision),
                "a rejected command advanced the scene revision");
        }

        [Test]
        public void AnUnannouncedChangeIsStillCaughtBeforeAMutationRuns()
        {
            var id = _rv.CreateObject("Subject");
            var observed = _rv.Result("scene.describe").Value<long>("revision");

            var stray = new GameObject("SlippedIn");

            // The pre-mutation resync is authoritative, so the revision the agent
            // planned against is already stale and the mutation must be refused.
            _rv.Call("object.transform", new JObject
            {
                ["object"] = id,
                ["local_position"] = new JArray(9f, 0f, 0f)
            }, ifRevision: observed, ok: false, code: "STALE_REVISION");

            Object.DestroyImmediate(stray);
        }
    }
}
