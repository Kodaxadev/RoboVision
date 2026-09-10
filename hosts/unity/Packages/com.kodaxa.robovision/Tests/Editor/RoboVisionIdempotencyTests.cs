using System;
using System.IO;
using System.Linq;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// A lost reply must not become a second cube, and the host must not invent randomness.
    /// </summary>
    /// <remarks>
    /// Behavioural parity with the Blender host rather than a translation of it.
    /// The identities are the same five — request, key, attempt, recipe, seed —
    /// and the frame deliberately is not: these assert Unity's own convention and
    /// that a request planned in Blender's is refused here rather than executed.
    /// </remarks>
    [TestFixture]
    internal sealed class RoboVisionIdempotencyTests
    {
        private Harness _rv;

        [SetUp]
        public void SetUp()
        {
            Harness.FreshScene();
            _rv = new Harness("idem");
            _rv.Call("scene.snapshot");
        }

        [TearDown]
        public void TearDown()
        {
            _rv.ResetTransactions();
        }

        private static JObject Key(string key, int attempt = 1)
        {
            return new JObject { ["idempotency_key"] = key, ["attempt"] = attempt };
        }

        private static JObject Cube(string name)
        {
            return new JObject { ["name"] = name };
        }

        private static int ObjectCount()
        {
            return UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None).Length;
        }

        // ------------------------------------------------------------- recipe

        [Test]
        public void a_recipe_names_the_computation_not_the_delivery()
        {
            var once = RoboVisionRecipe.Hash("object.create", Cube("A"), "0.1.0",
                RoboVisionHost.DeterminismExact);
            var withTransport = (JObject)Cube("A").DeepClone();
            withTransport["idempotency_key"] = "k";
            withTransport["attempt"] = 4;
            withTransport["request_id"] = "r-99";
            var again = RoboVisionRecipe.Hash("object.create", withTransport, "0.1.0",
                RoboVisionHost.DeterminismExact);
            Assert.That(again, Is.EqualTo(once),
                "delivery identity changed the recipe, so every retry would be a new computation");

            var different = RoboVisionRecipe.Hash("object.create", Cube("B"), "0.1.0",
                RoboVisionHost.DeterminismExact);
            Assert.That(different, Is.Not.EqualTo(once), "two computations hashed the same");
            Assert.That(once, Does.StartWith("rvrecipe:"));
        }

        [Test]
        public void the_coordinate_contract_is_unity_s_own_frame()
        {
            // Not Blender's. Pretending the two hosts share a frame would make
            // every cross-editor claim quietly wrong.
            Assert.That(RoboVisionRecipe.CanonicalFrame,
                Is.EqualTo("rvframe:unity_y_up_left_handed_metres"));
            Assert.That(RoboVisionRecipe.CanonicalFrame,
                Is.Not.EqualTo("rvframe:blender_z_up_right_handed_metres"));

            var environment = RoboVisionRecipe.Environment();
            Assert.That(environment.Value<string>("editor"), Is.EqualTo("unity"));
            Assert.That(environment.Value<string>("up"), Is.EqualTo("+Y"));
            Assert.That(environment.Value<string>("handedness"), Is.EqualTo("left"));
            Assert.That(RoboVisionRecipe.CoordinateContract(), Does.StartWith("rvcoord:"));
            Assert.That(RoboVisionRecipe.CoordinateContract(),
                Is.EqualTo(RoboVisionRecipe.CoordinateContract()), "the contract is not stable");
        }

        [Test]
        public void a_request_planned_in_another_frame_is_refused()
        {
            var refused = _rv.Call("object.create", Cube("Elsewhere"), ok: false,
                code: "COORDINATE_CONTRACT_CHANGED",
                envelope: new JObject
                {
                    ["expected_coordinate_contract"] = "rvcoord:0000000000000000"
                });
            var data = (JObject)refused["error"]["data"];
            Assert.That(data.Value<string>("current_coordinate_contract"),
                Is.EqualTo(RoboVisionRecipe.CoordinateContract()));
            Assert.That(data["units"].Value<string>("canonical_unit"), Does.Contain("unity unit"));
            Assert.That(GameObject.Find("Elsewhere"), Is.Null,
                "a refused request still authored something");
        }

        [Test]
        public void a_request_planned_against_another_world_is_refused()
        {
            _rv.Call("object.create", Cube("Anywhere"), ok: false, code: "STALE_WORLD",
                envelope: new JObject { ["expected_world"] = "rvworld:not-this-one" });
            Assert.That(GameObject.Find("Anywhere"), Is.Null);
        }

        // -------------------------------------------------------- duplicates

        [Test]
        public void a_redelivered_mutation_replays_instead_of_repeating()
        {
            var before = ObjectCount();
            var first = _rv.Call("object.create", Cube("Once"), envelope: Key("k-once"));
            Assert.That(ObjectCount(), Is.EqualTo(before + 1));

            var again = _rv.Call("object.create", Cube("Once"), envelope: Key("k-once", 2));
            Assert.That(ObjectCount(), Is.EqualTo(before + 1), "a redelivery created a second object");
            Assert.That(again.Value<bool>("replayed"), Is.True, "a duplicate was not marked as one");
            Assert.That(again["result"].Value<string>("id"),
                Is.EqualTo(first["result"].Value<string>("id")));
        }

        [Test]
        public void a_replay_reports_the_original_execution_separately()
        {
            var first = _rv.Call("object.create", Cube("Original"), envelope: Key("k-orig"));
            var executedAt = first.Value<long>("revision");
            // Move the world on, so "now" and "then" cannot be confused.
            _rv.CreateObject("Interloper");

            var again = _rv.Call("object.create", Cube("Original"), envelope: Key("k-orig", 2));
            var original = (JObject)again["original_execution"];
            Assert.That(again.Value<long>("revision"), Is.EqualTo(_rv.Revision),
                "the envelope described the world as it was, not as it is");
            Assert.That(original.Value<long>("post_revision"), Is.EqualTo(executedAt),
                "the original execution was reported at the retry's revision");
            Assert.That(original.Value<string>("world_incarnation"),
                Is.EqualTo(_rv.Host.WorldIncarnation));
            Assert.That(original.Value<string>("recipe_hash"), Does.StartWith("rvrecipe:"));
        }

        [Test]
        public void the_same_key_for_a_different_computation_is_refused()
        {
            _rv.Call("object.create", Cube("First"), envelope: Key("k-clash"));
            var refused = _rv.Call("object.create", Cube("Second"), ok: false,
                code: "IDEMPOTENCY_MISMATCH", envelope: Key("k-clash", 2));
            var data = (JObject)refused["error"]["data"];
            Assert.That(data.Value<string>("recorded_recipe"),
                Is.Not.EqualTo(data.Value<string>("requested_recipe")));
            Assert.That(GameObject.Find("Second"), Is.Null);
        }

        [Test]
        public void a_retry_of_a_key_this_host_never_saw_is_indeterminate()
        {
            // A forgotten key and a never-seen key are indistinguishable here, so
            // a client that says it is retrying must not be answered by
            // executing: the first attempt may already have applied.
            var refused = _rv.Call("object.create", Cube("Unknown"), ok: false, code: "INDETERMINATE",
                envelope: Key("k-never-seen", 2));
            Assert.That(refused["error"]["data"].Value<string>("remedy"), Is.Not.Null);
            Assert.That(GameObject.Find("Unknown"), Is.Null);
        }

        [Test]
        public void a_recovered_failure_leaves_the_key_free_to_run_again()
        {
            // The operation failed and automatic recovery proved the
            // pre-operation fingerprint was restored, so it definitively did not
            // apply. That is a durable state, not a deletion — and it is the one
            // that lets a client retry a failed call instead of being told
            // nobody knows.
            var missing = new JObject { ["object"] = "unity:session:0:99999" };
            _rv.Call("object.delete", missing, ok: false, code: "NOT_FOUND", envelope: Key("k-gone"));
            var retried = _rv.Call("object.delete", missing, ok: false, envelope: Key("k-gone", 2));
            Assert.That(retried["error"].Value<string>("code"), Is.EqualTo("NOT_FOUND"),
                "a provably-unapplied key was not eligible to run again");
        }

        // ------------------------------------------------------------ seeds

        [Test]
        public void a_stochastic_tool_refuses_to_invent_its_own_randomness()
        {
            RegisterScatter();
            var refused = _rv.Call("test.scatter", new JObject(), ok: false, code: "SEED_REQUIRED",
                envelope: Key("k-noseed"));
            var data = (JObject)refused["error"]["data"];
            Assert.That(data["seed_channels"].Select(c => c.Value<string>()).ToArray(),
                Is.EqualTo(new[] { "seed" }));
            Assert.That(data.Value<string>("determinism"),
                Is.EqualTo(RoboVisionHost.DeterminismSeeded));

            var seeded = _rv.Call("test.scatter", new JObject { ["seed"] = 7 }, envelope: Key("k-seed"));
            var replay = _rv.Call("test.scatter", new JObject { ["seed"] = 7 }, envelope: Key("k-seed", 2));
            Assert.That(replay.Value<bool>("replayed"), Is.True, "a seeded retry re-executed");
            Assert.That(replay["result"].ToString(Newtonsoft.Json.Formatting.None),
                Is.EqualTo(seeded["result"].ToString(Newtonsoft.Json.Formatting.None)),
                "the replay reported different randomness than the original");

            _rv.Call("test.scatter", new JObject { ["seed"] = 8 }, ok: false,
                code: "IDEMPOTENCY_MISMATCH", envelope: Key("k-seed", 2));
        }

        [Test]
        public void determinism_metadata_cannot_contradict_itself()
        {
            // A stochastic tool cannot be registered as if it were reproducible,
            // or the metadata is decoration.
            foreach (var bad in new[]
                     {
                         new { Seeds = new[] { "seed" }, Determinism = RoboVisionHost.DeterminismExact },
                         new { Seeds = Array.Empty<string>(), Determinism = RoboVisionHost.DeterminismSeeded },
                         new { Seeds = Array.Empty<string>(), Determinism = "invented" }
                     })
            {
                var name = "test.bad-" + bad.Determinism + "-" + bad.Seeds.Length;
                Assert.That(() => _rv.Host.AddTool(name, _ => new JObject(),
                        seeds: bad.Seeds, determinism: bad.Determinism),
                    Throws.InstanceOf<InvalidOperationException>(),
                    "the registry accepted " + name);
            }
        }

        [Test]
        public void a_side_effecting_tool_must_say_how_a_duplicate_is_resolved()
        {
            Assert.That(() => _rv.Host.AddTool("test.unpoliced", _ => new JObject(),
                    sideEffecting: true),
                Throws.InstanceOf<InvalidOperationException>());
            Assert.That(() => _rv.Host.AddTool("test.policy-without-effect", _ => new JObject(),
                    duplicatePolicy: RoboVisionHost.DuplicateReplay),
                Throws.InstanceOf<InvalidOperationException>());
        }

        [Test]
        public void ARetryAfterTheTransactionEndedIsToldWhatBecameOfIt()
        {
            // The tombstone stays when the transaction ends. Discarding the
            // records with it would reopen the hole: operation executes, reply is
            // lost, transaction ends, client retries — and with the key gone it
            // executes again, against a scene where the first one was undone.
            var begun = _rv.BeginTransactionRaw(new JObject { ["label"] = "ended" });
            var transaction = begun.Value<string>("transaction");
            _rv.Call("object.create", Cube("Undone"), envelope: Key("k-in-tx"));
            _rv.EndTransaction("transaction.discard", transaction);

            var again = _rv.Call("object.create", Cube("Undone"), envelope: Key("k-in-tx", 2));
            Assert.That(again.Value<bool>("replayed"), Is.True,
                "a retry after the transaction ended executed as new work");
            Assert.That(again["original_execution"].Value<string>("transaction_outcome"),
                Is.EqualTo(RoboVisionTransactions.StateAbandoned),
                "the replay did not say what became of the transaction");
        }

        [Test]
        public void ADeclaredDuplicatePolicyIsWhatActuallyHappens()
        {
            // transaction.rollback mutates, so the invocation ledger used to
            // intercept a keyed redelivery and hand back the first execution's
            // stored result — never reaching the state machine that would have
            // said the transaction is finished. The declaration was decoration.
            var id = _rv.BeginTransactionRaw(new JObject { ["label"] = "policy" })
                .Value<string>("transaction");
            _rv.Call("object.create", Cube("Rolled"), envelope: Key("k-policy-op"));

            var parameters = new JObject { ["transaction"] = id };
            _rv.Call("transaction.rollback", parameters, envelope: Key("k-policy"));
            _rv.ActiveTransactionEnded();
            var again = _rv.Call("transaction.rollback", parameters, ok: false,
                code: "TRANSACTION_FINISHED", envelope: Key("k-policy", 2));
            Assert.That(again["replayed"], Is.Null,
                "a terminal_state tool was answered by replay");
        }

        // ----------------------------------------------------------- ledger

        [Test]
        public void the_ledger_records_intent_before_the_side_effect()
        {
            _rv.Call("object.create", Cube("Recorded"), envelope: Key("k-ledger"));
            var records = ReadLedger();
            var intent = records.Last(r => r.Value<string>("type") == RoboVisionLedger.Intent
                                           && r.Value<string>("idempotency_key") == "k-ledger");
            var result = records.Last(r => r.Value<string>("type") == RoboVisionLedger.Result
                                           && r.Value<long>("intent") == intent.Value<long>("sequence"));
            Assert.That(result.Value<long>("sequence"), Is.GreaterThan(intent.Value<long>("sequence")),
                "the outcome was recorded before the intention to cause it");
            Assert.That(intent.Value<string>("recipe_hash"), Does.StartWith("rvrecipe:"));
            Assert.That(intent.Value<string>("frame"), Is.EqualTo(RoboVisionRecipe.CanonicalFrame));
            Assert.That(intent.Value<string>("state_domain"), Is.EqualTo(RoboVisionHost.DomainAuthored));
            Assert.That(intent.Value<string>("world"), Is.EqualTo(_rv.Host.WorldIncarnation));
            Assert.That(result.Value<string>("outcome"), Is.EqualTo("applied"));
        }

        [Test]
        public void a_ledger_never_carries_a_recovery_secret()
        {
            var begun = _rv.BeginTransactionRaw(new JObject { ["label"] = "secret check" });
            var secret = begun.Value<string>("recovery_token");
            _rv.Call("object.create", Cube("Inside"), envelope: Key("k-secret"));
            _rv.EndTransaction("transaction.commit", begun.Value<string>("transaction"));

            Assert.That(secret, Is.Not.Null.And.Not.Empty);
            var raw = String.Join("\n", ReadLedger().Select(r => r.ToString(Newtonsoft.Json.Formatting.None)));
            Assert.That(raw, Does.Not.Contain(secret), "the operation ledger recorded a recovery secret");
        }

        [Test]
        public void an_interrupted_operation_is_not_a_completed_one()
        {
            // Rebuilt from the durable records rather than from a live host: an
            // intent with no result is exactly what a crash leaves behind, and it
            // must never be replayed as if it had finished.
            var invocations = new RoboVisionInvocations();
            invocations.Adopt(new System.Collections.Generic.Dictionary<string, JObject>
            {
                ["k-torn"] = new JObject
                {
                    ["intent"] = new JObject
                    {
                        ["sequence"] = 1, ["recipe_hash"] = "rvrecipe:x", ["world"] = "rvworld:w"
                    },
                    ["result"] = null
                },
                ["k-void"] = new JObject
                {
                    ["intent"] = new JObject
                    {
                        ["sequence"] = 2, ["recipe_hash"] = "rvrecipe:y", ["world"] = "rvworld:w"
                    },
                    ["result"] = new JObject
                    {
                        ["outcome"] = RoboVisionInvocations.ProvedNotApplied
                    }
                }
            }, "rvworld:w");

            // Code is a field, so it is read rather than matched on by name.
            string torn = null;
            try { invocations.Check("k-torn", "rvrecipe:x", 2, "rvworld:w"); }
            catch (RoboVisionException refusal) { torn = refusal.Code; }
            Assert.That(torn, Is.EqualTo("INDETERMINATE"),
                "an interrupted operation was treated as a completed one");
            Assert.That(invocations.Check("k-void", "rvrecipe:y", 2, "rvworld:w"), Is.Null,
                "a provably-unapplied operation was not eligible to run again");
        }

        // ----------------------------------------------------------- helpers

        private void RegisterScatter()
        {
            if (_rv.Host.HasTool("test.scatter")) return;
            _rv.Host.AddTool("test.scatter", parameters =>
                {
                    var random = new System.Random(parameters.Value<int>("seed"));
                    var placed = new JArray();
                    for (var index = 0; index < 3; index++)
                    {
                        var go = new GameObject("Scatter" + index);
                        Undo.RegisterCreatedObjectUndo(go, "RoboVision scatter");
                        go.transform.localPosition = new Vector3(
                            (float)Math.Round(random.NextDouble(), 6),
                            (float)Math.Round(random.NextDouble(), 6), 0f);
                        placed.Add(new JArray(go.transform.localPosition.x,
                            go.transform.localPosition.y, go.transform.localPosition.z));
                    }
                    return new JObject { ["placed"] = placed };
                },
                mutating: true, seeds: new[] { "seed" },
                determinism: RoboVisionHost.DeterminismSeeded,
                summary: "Test-only stochastic placement.");
        }

        private JObject[] ReadLedger()
        {
            var path = _rv.Host.Ledger.Path;
            Assert.That(File.Exists(path), Is.True, "no ledger was written at " + path);
            return File.ReadAllLines(path)
                .Where(line => !String.IsNullOrWhiteSpace(line))
                .Select(JObject.Parse)
                .ToArray();
        }
    }
}
