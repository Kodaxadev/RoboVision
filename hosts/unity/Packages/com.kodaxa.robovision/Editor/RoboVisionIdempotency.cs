using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// A lost reply must not become a second cube.
    /// </summary>
    /// <remarks>
    /// The scope of a logical invocation is (world, key). The world, because an
    /// operation was planned against one editing context and a retry must never
    /// be reinterpreted in another. Not the bridge: Unity destroys and rebuilds
    /// every static in this package on a domain reload while the scenes stay
    /// exactly where they were, so the bridge is new and the invocation is the
    /// same invocation.
    ///
    /// Not the transaction either. A transaction is recorded context, not a
    /// namespace: if one key could mean different side effects in different
    /// transactions, a tombstone left after a transaction ended would be
    /// ambiguous about which operation it described. A deliberate execution
    /// inside another transaction uses a fresh key, exactly as a deliberate
    /// re-execution anywhere else does.
    /// </remarks>
    internal sealed class RoboVisionInvocations
    {
        internal const string Reserved = "reserved";
        internal const string Terminal = "terminal";
        internal const string Interrupted = "interrupted";

        /// <summary>The attempt provably had no effect, and the key may run again.</summary>
        /// <remarks>
        /// A durable state rather than a deletion: the ledger is append-only and
        /// its records still describe the attempt, so a resume that could not
        /// tell this from a completed operation would replay a response that
        /// never existed.
        /// </remarks>
        internal const string ProvedNotApplied = "proved_not_applied";

        internal sealed class Invocation
        {
            public string Key;
            public string Recipe;
            public string World;
            public string Transaction;
            public string State;
            public long IntentSequence;
            public JObject Response;
            public JObject Original;
            public string TransactionOutcome;
        }

        private readonly Dictionary<string, Invocation> _records =
            new Dictionary<string, Invocation>(StringComparer.Ordinal);
        private readonly List<string> _order = new List<string>();
        private const int Retained = 512;

        // ------------------------------------------------------------- lookup

        /// <summary>Decide what a delivery is: first, duplicate, mismatch or unknowable.</summary>
        /// <returns>The stored response to replay, or null to execute.</returns>
        internal JObject Check(string key, string recipe, int attempt, string world)
        {
            if (!_records.TryGetValue(key, out var record))
            {
                if (attempt > 1)
                {
                    // The honest case. A forgotten key and a never-seen key are
                    // indistinguishable to the host, so a client that says it is
                    // retrying must not be answered by executing: the first
                    // attempt may already have applied.
                    throw new RoboVisionException(
                        "INDETERMINATE",
                        "this is a retry of an operation this host has no record of; "
                        + "it may already have been applied",
                        false,
                        new JObject
                        {
                            ["idempotency_key"] = key,
                            ["attempt"] = attempt,
                            ["remedy"] = "Re-observe the scene, then issue a new operation with a new key."
                        });
                }
                return null;
            }

            if (!String.Equals(record.Recipe, recipe, StringComparison.Ordinal))
            {
                // Same key, different computation. Executing either one would be
                // a guess about which the client meant.
                throw new RoboVisionException(
                    "IDEMPOTENCY_MISMATCH",
                    "that idempotency key was used for a different operation",
                    false,
                    new JObject
                    {
                        ["idempotency_key"] = key,
                        ["recorded_recipe"] = record.Recipe,
                        ["requested_recipe"] = recipe
                    });
            }

            if (!String.Equals(record.World, world, StringComparison.Ordinal))
            {
                throw new RoboVisionException(
                    "STALE_WORLD",
                    "that operation was planned against an editing context that is no longer open",
                    false,
                    new JObject
                    {
                        ["idempotency_key"] = key,
                        ["recorded_world"] = record.World,
                        ["current_world"] = world
                    });
            }

            if (record.State == Reserved)
            {
                // Not retryable, matching the Blender host exactly. A client
                // reading this flag must not be given different advice by the
                // two hosts for the same condition, and re-delivering into an
                // operation that is still running is not what to do next:
                // re-observe, then decide.
                throw new RoboVisionException(
                    "IN_PROGRESS",
                    "that operation is already executing",
                    false,
                    new JObject { ["idempotency_key"] = key });
            }

            // Evidence retained, and eligible to run: the previous attempt is on
            // record as having had no effect.
            if (record.State == ProvedNotApplied) return null;

            if (record.State == Interrupted)
            {
                throw new RoboVisionException(
                    "INDETERMINATE",
                    "that operation was interrupted before its outcome was recorded",
                    false,
                    new JObject
                    {
                        ["idempotency_key"] = key,
                        ["remedy"] = "Re-observe the scene; the operation may or may not have applied."
                    });
            }

            // The original result is replayed; the envelope around it describes
            // the host as it is now. Those are different things and a client must
            // be able to tell them apart — it must never conclude that the
            // operation originally executed at the revision its retry happened to
            // arrive at.
            var response = record.Response != null
                ? (JObject)record.Response.DeepClone()
                : new JObject();
            response["replayed"] = true;
            var original = record.Original != null
                ? (JObject)record.Original.DeepClone()
                : new JObject();
            if (!String.IsNullOrEmpty(record.TransactionOutcome))
            {
                // A retry after the transaction ended is answered with what
                // happened to it, rather than being executed again as if it were
                // new work.
                original["transaction_outcome"] = record.TransactionOutcome;
            }
            response["original_execution"] = original;
            return response;
        }

        // ------------------------------------------------------------ writing

        internal void Reserve(string key, string recipe, string world, string transaction,
            long intentSequence)
        {
            if (!_records.ContainsKey(key)) _order.Add(key);
            _records[key] = new Invocation
            {
                Key = key,
                Recipe = recipe,
                World = world,
                Transaction = transaction,
                State = Reserved,
                IntentSequence = intentSequence
            };
            Trim();
        }

        internal void Complete(string key, JObject response, JObject original)
        {
            if (key == null || !_records.TryGetValue(key, out var record)) return;
            record.State = Terminal;
            record.Response = response != null ? (JObject)response.DeepClone() : new JObject();
            record.Original = original != null ? (JObject)original.DeepClone() : new JObject();
        }

        /// <summary>An execution that never reached a terminal outcome leaves evidence.</summary>
        /// <remarks>
        /// Not deleted: a reservation dropped silently is indistinguishable from
        /// a key that was never used, which is exactly the confusion this class
        /// exists to prevent.
        /// </remarks>
        internal void Release(string key)
        {
            if (key == null || !_records.TryGetValue(key, out var record)) return;
            if (record.State == Reserved) record.State = Interrupted;
        }

        /// <summary>Record that the attempt provably had no effect.</summary>
        /// <remarks>
        /// Only ever called where automatic recovery restored the pre-operation
        /// fingerprint, which is proof that nothing happened, so the key stays
        /// eligible to execute. A state and not a deletion: deleting the live
        /// record while the append-only ledger still held the attempt made the
        /// two disagree, and a resume rebuilt from the ledger would have called
        /// it terminal and replayed a response that was never produced.
        /// </remarks>
        internal void ProveNotApplied(string key)
        {
            if (key == null || !_records.TryGetValue(key, out var record)) return;
            record.State = ProvedNotApplied;
            record.Response = null;
        }

        /// <summary>A transaction ended; its operations keep their tombstones.</summary>
        /// <remarks>
        /// Discarding transaction-scoped records with the transaction reopens the
        /// hole: operation executes, reply is lost, transaction rolls back,
        /// client retries — and with the key gone it executes again, against a
        /// scene where the first one was undone. The record stays and reports
        /// what became of the transaction instead.
        /// </remarks>
        internal void NoteTransactionOutcome(string transaction, string outcome)
        {
            if (String.IsNullOrEmpty(transaction)) return;
            foreach (var record in _records.Values)
            {
                if (String.Equals(record.Transaction, transaction, StringComparison.Ordinal))
                    record.TransactionOutcome = outcome;
            }
        }

        /// <summary>Rebuild what a previous bridge knew about this world, from the ledger.</summary>
        /// <remarks>
        /// Only ever called once world continuity has been proved by reading the
        /// editor. The outcome recorded with the result decides the state, not
        /// the mere presence of one: an attempt that ended in proved
        /// non-application is not a completed operation and must not be replayed
        /// as if it were.
        /// </remarks>
        internal void Adopt(Dictionary<string, JObject> resumed, string world)
        {
            foreach (var pair in resumed)
            {
                var intent = pair.Value["intent"] as JObject ?? new JObject();
                var result = pair.Value["result"] as JObject;
                var outcome = result != null ? result.Value<string>("outcome") : null;
                string state;
                if (result == null) state = Interrupted;
                else if (outcome == ProvedNotApplied) state = ProvedNotApplied;
                else if (outcome == Interrupted || outcome == "indeterminate") state = Interrupted;
                else state = Terminal;

                if (!_records.ContainsKey(pair.Key)) _order.Add(pair.Key);
                _records[pair.Key] = new Invocation
                {
                    Key = pair.Key,
                    Recipe = intent.Value<string>("recipe_hash") ?? String.Empty,
                    World = intent.Value<string>("world") ?? world,
                    Transaction = intent.Value<string>("transaction"),
                    State = state,
                    IntentSequence = intent.Value<long?>("sequence") ?? 0L,
                    Response = state == Terminal ? result["response"] as JObject : null,
                    Original = state == Terminal
                        ? new JObject
                        {
                            ["request_id"] = intent.Value<string>("request_id"),
                            ["world_incarnation"] = intent.Value<string>("world"),
                            ["recipe_hash"] = intent.Value<string>("recipe_hash"),
                            ["pre_revision"] = intent["pre_revision"],
                            ["pre_fingerprint"] = intent["pre_fingerprint"],
                            ["post_revision"] = result["post_revision"],
                            ["post_fingerprint"] = result["post_fingerprint"],
                            ["outcome"] = outcome
                        }
                        : null
                };
            }
            Trim();
        }

        private void Trim()
        {
            while (_order.Count > Retained)
            {
                var oldest = _order[0];
                _order.RemoveAt(0);
                _records.Remove(oldest);
            }
        }
    }
}
