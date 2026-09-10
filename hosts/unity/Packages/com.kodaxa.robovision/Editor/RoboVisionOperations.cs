using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json.Linq;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// What an operation is, before it is allowed to be one.
    /// </summary>
    /// <remarks>
    /// Everything dispatch needs settled <em>before</em> a side effect: which
    /// randomness will be used, what computation was asked for, what an
    /// unattended caller has to supply, where the durable record lives, and how a
    /// failed attempt is closed out. Separate from dispatch because dispatch is
    /// the order these happen in, and this is what each of them means.
    /// </remarks>
    internal sealed partial class RoboVisionHost
    {
        /// <summary>The contract an unattended agent invokes under.</summary>
        internal const string Autonomous = "autonomous";

        private RoboVisionLedger _ledger;
        private RoboVisionInvocations _invocations;
        private string _ledgerWorld;
        /// <summary>The last time the durable spine could not be read or written.</summary>
        /// <remarks>
        /// One fact, overwritten — not a log and not a history. It is kept because
        /// a recovering agent needs to know the record it is about to trust may
        /// have a hole in it, and <c>system.health</c> is where it asks.
        /// </remarks>
        private JObject _ledgerError;

        internal JObject LedgerError => _ledgerError;

        internal void NoteLedgerError(string operation, Exception error)
        {
            _ledgerError = new JObject
            {
                ["operation"] = operation,
                ["error"] = error.GetType().Name + ": " + error.Message
            };
        }

        internal RoboVisionLedger Ledger { get { EnsureOperationRecords(); return _ledger; } }
        internal RoboVisionInvocations Invocations { get { EnsureOperationRecords(); return _invocations; } }

        /// <summary>Read the editor before answering any question about the world.</summary>
        /// <remarks>
        /// The world incarnation minted in the reconciler's constructor is
        /// provisional: which world a rebuilt bridge woke up in cannot be decided
        /// without looking. A request that pins an expected world would otherwise
        /// be compared against a placeholder.
        /// </remarks>
        internal void EnsureWorldSettled()
        {
            if (!WorldSettled) _reconciler.Resync();
        }

        /// <summary>
        /// Follow the world into whichever incarnation is now open.
        /// </summary>
        /// <remarks>
        /// A new world gets its own ledger file and an empty invocation record.
        /// Nothing is carried across: an idempotency key names an operation
        /// planned against one editing context, and a world that has been
        /// replaced is not that context however recently it was.
        ///
        /// Adoption runs only where the world was <em>proved</em> continuous by
        /// reading the editor back and matching it exactly. The ledger is never
        /// evidence that the world is the same — that would be circular, and a
        /// stale file left behind by a previous project state would then be able
        /// to authorise replaying its own contents.
        /// </remarks>
        private void EnsureOperationRecords()
        {
            var world = WorldIncarnation;
            if (_ledger != null && String.Equals(_ledgerWorld, world, StringComparison.Ordinal)) return;
            _ledger = RoboVisionLedger.Open(world);
            _invocations = new RoboVisionInvocations();
            _ledgerWorld = world;
            // A previous world's failure says nothing about this one's file.
            _ledgerError = null;
            if (!WorldSettled || !WorldResumed) return;
            try
            {
                _invocations.Adopt(_ledger.Resume(), world);
            }
            catch (IOException ex)
            {
                // An unreadable ledger is not a licence to invent history. The
                // records stay empty, which means a retry of a key from before
                // the reload is answered INDETERMINATE rather than executed.
                NoteLedgerError("resume", ex);
                UnityEngine.Debug.LogWarning("RoboVision: could not resume the operation ledger: " + ex.Message);
            }
        }

        /// <summary>Collect the randomness this operation will use, or refuse to guess it.</summary>
        /// <remarks>
        /// The host must never pick a seed itself. If it did, and the reply were
        /// lost, the retry could not even describe the computation that may
        /// already have happened — the one thing a client needs in order to ask
        /// about it.
        /// </remarks>
        private static JObject SeedsFor(ToolSpec spec, JObject parameters)
        {
            var seeds = new JObject();
            if (spec.Seeds == null || spec.Seeds.Length == 0) return seeds;
            var missing = new List<string>();
            foreach (var channel in spec.Seeds)
            {
                var value = parameters[channel];
                if (value == null || value.Type == JTokenType.Null) missing.Add(channel);
                else seeds[channel] = value.DeepClone();
            }
            if (missing.Count == 0) return seeds;
            throw new RoboVisionException(
                "SEED_REQUIRED",
                spec.Name + " is stochastic and needs its randomness recorded: "
                + String.Join(", ", missing.ToArray()),
                false,
                new JObject
                {
                    ["method"] = spec.Name,
                    ["missing_seeds"] = new JArray(missing.ToArray()),
                    ["seed_channels"] = new JArray(spec.Seeds),
                    ["determinism"] = spec.Determinism,
                    ["remedy"] = "Generate a seed once, keep it with the operation, and send it "
                                 + "with every retry."
                });
        }

        /// <summary>What was asked for, independent of which delivery is asking.</summary>
        private string RecipeFor(ToolSpec spec, JObject parameters, JObject seeds)
        {
            var hashable = new JObject();
            foreach (var property in parameters.Properties())
            {
                if (Array.IndexOf(spec.Seeds ?? Array.Empty<string>(), property.Name) >= 0) continue;
                hashable[property.Name] = property.Value.DeepClone();
            }
            return RoboVisionRecipe.Hash(
                spec.Name, hashable, HostVersion, spec.Determinism,
                seeds: seeds,
                targets: new JObject { ["world"] = WorldIncarnation });
        }

        /// <summary>
        /// What an unattended loop must supply before it is allowed to author anything.
        /// </summary>
        /// <remarks>
        /// Low-level delivery stays permissive so an operator at a console can
        /// still poke the host, but an agent driving it for hours cannot be
        /// trusted to have remembered any of this by convention. The world
        /// matters most: a <em>first</em> delivery planned against world A must
        /// not execute in world B merely because it is technically not a retry,
        /// and nothing but an explicit expected world catches that.
        /// </remarks>
        private static void AssertAutonomousContract(ToolSpec spec, JObject raw, JObject parameters,
            long? ifRevision)
        {
            if (raw.Value<string>("contract") != Autonomous) return;
            if (!spec.Mutating && !spec.ObservationBound) return;
            var missing = new List<string>();
            // Both kinds of operation are pinned to the world and the unit
            // convention they were planned in. A first delivery in the wrong
            // world is exactly as wrong as a retry there, and a scene whose units
            // were reinterpreted is a different scene however unchanged its
            // revision looks.
            if (String.IsNullOrEmpty(raw.Value<string>("expected_world")))
                missing.Add("expected_world");
            if (String.IsNullOrEmpty(raw.Value<string>("expected_coordinate_contract")))
                missing.Add("expected_coordinate_contract");
            if (ifRevision == null) missing.Add("if_revision");
            if (spec.Mutating)
            {
                // Only a mutation can be applied twice, so only a mutation needs
                // the identity that makes a redelivery recognisable.
                if (String.IsNullOrEmpty(raw.Value<string>("idempotency_key")))
                    missing.Add("idempotency_key");
                var attempt = raw["attempt"];
                if (attempt == null || attempt.Type != JTokenType.Integer || attempt.Value<int>() < 1)
                    missing.Add("attempt");
            }
            foreach (var channel in spec.Seeds ?? Array.Empty<string>())
            {
                var value = parameters[channel];
                if (value == null || value.Type == JTokenType.Null) missing.Add(channel);
            }
            if (missing.Count == 0) return;
            throw new RoboVisionException(
                "CONTRACT_VIOLATION",
                spec.Name + " was invoked under the autonomous contract without: "
                + String.Join(", ", missing.ToArray()),
                false,
                new JObject
                {
                    ["contract"] = Autonomous,
                    ["missing"] = new JArray(missing.ToArray()),
                    ["method"] = spec.Name
                });
        }

        /// <summary>Close the record for an operation that did not succeed.</summary>
        /// <remarks>
        /// Whether the key may be used again turns on evidence rather than on the
        /// fact of failure. If automatic recovery proved the pre-operation
        /// fingerprint was restored, the operation definitively did not apply, so
        /// the same key may be delivered again and execute — that is the useful
        /// outcome for a client retrying a failed call. Without that proof the
        /// honest answer is that nobody knows, and a retry is told so instead of
        /// being run a second time.
        /// </remarks>
        private void CloseFailed(JObject intent, string key, Exception error, JObject recovery)
        {
            if (intent == null) return;
            var recovered = recovery != null && (recovery.Value<bool?>("recovered") ?? false);
            var code = error is RoboVisionException rv ? rv.Code : error.GetType().Name;
            try
            {
                Ledger.WriteResult(intent.Value<long>("sequence"), new JObject
                {
                    ["outcome"] = recovered ? RoboVisionInvocations.ProvedNotApplied : "indeterminate",
                    ["error_code"] = code,
                    ["recovered"] = recovered,
                    ["post_revision"] = Revision
                });
            }
            catch (IOException ledgerError)
            {
                // A ledger that cannot be written is not a reason to swallow the
                // original error, which is what the caller is actually waiting for.
                // It is recorded, though: a durable record with a hole in it is
                // exactly what a recovering agent must not trust silently.
                NoteLedgerError("result", ledgerError);
            }
            if (key == null) return;
            if (recovered) Invocations.ProveNotApplied(key);
            else Invocations.Release(key);
        }
    }
}
