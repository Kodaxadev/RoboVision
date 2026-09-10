using System.Linq;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// What an unattended loop has to supply before it may author anything.
    /// </summary>
    /// <remarks>
    /// Low-level delivery stays permissive so an operator at a console can still
    /// poke the host. An agent driving it for hours cannot be trusted to have
    /// remembered any of this by convention, so under the autonomous contract the
    /// requirements are checked rather than assumed — and a first delivery
    /// planned against world A must not execute in world B merely because it is
    /// technically not a retry.
    /// </remarks>
    [TestFixture]
    internal sealed class RoboVisionAutonomousTests
    {
        private Harness _rv;

        [SetUp]
        public void SetUp()
        {
            Harness.FreshScene();
            _rv = new Harness("autonomous");
            _rv.Result("scene.describe");
        }

        [TearDown]
        public void TearDown()
        {
            _rv.ResetTransactions();
        }

        private JObject Pinned(bool world = true, bool contract = true, bool key = true)
        {
            var envelope = new JObject { ["contract"] = RoboVisionHost.Autonomous };
            if (world) envelope["expected_world"] = _rv.Host.WorldIncarnation;
            if (contract) envelope["expected_coordinate_contract"] = RoboVisionRecipe.CoordinateContract();
            if (key)
            {
                envelope["idempotency_key"] = "auto-" + System.Guid.NewGuid().ToString("N");
                envelope["attempt"] = 1;
            }
            return envelope;
        }

        private static string[] Missing(JObject refused)
        {
            return refused["error"]["data"]["missing"].Select(m => m.Value<string>()).ToArray();
        }

        [Test]
        public void AnAutonomousMutationMustNameEverythingThatCouldHaveMoved()
        {
            var refused = _rv.Call("object.create", new JObject { ["name"] = "Unpinned" },
                ok: false, code: "CONTRACT_VIOLATION",
                envelope: new JObject { ["contract"] = RoboVisionHost.Autonomous });
            Assert.That(Missing(refused), Is.EquivalentTo(new[]
            {
                "expected_world", "expected_coordinate_contract", "if_revision",
                "idempotency_key", "attempt"
            }));
            Assert.That(GameObject.Find("Unpinned"), Is.Null,
                "a refused autonomous call still authored something");
        }

        [Test]
        public void AFullyPinnedAutonomousMutationIsAccepted()
        {
            var created = _rv.Call("object.create", new JObject { ["name"] = "Pinned" },
                ifRevision: _rv.Revision, envelope: Pinned());
            Assert.That(created.Value<string>("outcome"), Is.EqualTo("applied"));
            Assert.That(GameObject.Find("Pinned"), Is.Not.Null);
        }

        [Test]
        public void ALowLevelCallStaysPermissive()
        {
            // No contract declared: an operator at a console is not an
            // unattended loop, and refusing them would be a different product.
            _rv.Call("object.create", new JObject { ["name"] = "ByHand" });
            Assert.That(GameObject.Find("ByHand"), Is.Not.Null);
        }

        [Test]
        public void AnObservationBoundCallMustNameTheStateItPlannedAgainst()
        {
            // transaction.begin never changes the scene, so `mutating` would not
            // catch it — and its checkpoint is worth nothing if it is not the
            // state the caller planned against.
            var refused = _rv.Call("transaction.begin", new JObject { ["label"] = "unpinned" },
                ok: false, code: "CONTRACT_VIOLATION",
                envelope: new JObject { ["contract"] = RoboVisionHost.Autonomous });
            Assert.That(Missing(refused), Is.EquivalentTo(new[]
            {
                "expected_world", "expected_coordinate_contract", "if_revision"
            }), "an observation-bound, non-mutating call asked for a mutation's identity");

            var begun = _rv.Result("transaction.begin", new JObject { ["label"] = "pinned" },
                ifRevision: _rv.Revision);
            _rv.EndTransaction("transaction.discard", begun.Value<string>("transaction"));
        }

        [Test]
        public void ATransactionCannotCheckpointAWorldThatAlreadyMoved()
        {
            var planned = _rv.Revision;
            _rv.CreateObject("MovedSince");
            Assert.That(_rv.Revision, Is.Not.EqualTo(planned), "the scene did not move");

            // Refused without any contract declared: observation binding is a
            // property of the tool, not a privilege of the autonomous path.
            _rv.Call("transaction.begin", new JObject { ["label"] = "stale" },
                ifRevision: planned, ok: false, code: "STALE_REVISION");
            Assert.That(_rv.Result("system.hello")["transaction"].Value<bool>("active"), Is.False,
                "a refused begin left a transaction open");
        }

        [Test]
        public void AnAutonomousCallInTheWrongWorldIsRefusedBeforeAnythingElse()
        {
            var refused = _rv.Call("object.create", new JObject { ["name"] = "Misplaced" },
                ifRevision: _rv.Revision, ok: false, code: "STALE_WORLD",
                envelope: new JObject
                {
                    ["contract"] = RoboVisionHost.Autonomous,
                    ["expected_world"] = "rvworld:somewhere-else",
                    ["expected_coordinate_contract"] = RoboVisionRecipe.CoordinateContract(),
                    ["idempotency_key"] = "auto-wrong-world",
                    ["attempt"] = 1
                });
            Assert.That(refused["error"]["data"].Value<string>("expected_world"),
                Is.EqualTo("rvworld:somewhere-else"));
            Assert.That(GameObject.Find("Misplaced"), Is.Null);
        }
    }
}
