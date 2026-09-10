using System;
using Newtonsoft.Json.Linq;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// What a client may know about a transaction, and what became of it.
    /// </summary>
    /// <remarks>
    /// Split from the lifecycle because it answers a different question and must
    /// be safe to ask at any time. Nothing here opens, finishes or mutates
    /// anything: a caller whose acknowledgement was lost has to be able to learn
    /// the outcome by reading, and an operation that had to be repeated to
    /// discover what it did the first time would not be a transaction.
    ///
    /// Nothing here ever carries the recovery secret or its verifier. The handle
    /// and the generation are deliberately non-secret — they let a client
    /// recognise which credential is current without any of them proving
    /// anything.
    /// </remarks>
    internal sealed partial class RoboVisionTransactions
    {
        private sealed class Transaction
        {
            public string Id;
            /// <summary>The client's correlation string. Never identity.</summary>
            public string Label;
            public string World;
            public long BeginRevision;
            public string BeginFingerprint;
            public int UndoGroup;
            /// <summary>The checkpoint names objects that only this domain can address.</summary>
            public bool SessionScoped;
            public string ExternalChangeFingerprint;
            public long OwnerClientId;
            public string State = StateActive;
            public string Reason;
            public RoboVisionRecoveryToken Recovery;
            /// <summary>The client's own non-secret name for the begin that opened this.</summary>
            /// <remarks>
            /// It grants nothing. It exists so a client whose begin
            /// acknowledgement was lost can recognise which transaction its
            /// precommitted secret belongs to, without the host id ever becoming
            /// client-authoritative.
            /// </remarks>
            public string Handle;
            /// <summary>Which credential is current, counted rather than named.</summary>
            /// <remarks>
            /// After an adoption whose reply was lost, this is what tells a
            /// client whether the rotation happened — an ambiguous
            /// acknowledgement becomes a deterministic lookup. Non-secret by
            /// construction: it identifies no verifier and authenticates nothing.
            /// </remarks>
            public int RecoveryGeneration;
        }

        /// <summary>Everything a client may know about the transaction, and nothing more.</summary>
        /// <remarks>
        /// Deliberately without the recovery token or its verifier. This is what
        /// <c>system.hello</c> reports and what health will consume later, so it
        /// says who holds the transaction and whether a verified rollback is
        /// available — it never grants the authority to use one.
        /// </remarks>
        public JObject ActiveState()
        {
            EnsureJudged();
            if (_current == null) return null;
            var state = new JObject
            {
                ["transaction"] = _current.Id,
                ["label"] = _current.Label,
                ["state"] = _current.State,
                ["world_incarnation"] = _current.World,
                ["owner_client"] = _current.OwnerClientId,
                ["owner_disconnected"] = _current.State == StateOrphaned,
                ["begin_revision"] = _current.BeginRevision,
                ["begin_fingerprint"] = _current.BeginFingerprint,
                ["contaminated"] = _current.ExternalChangeFingerprint != null,
                ["adoption_required"] = _current.State == StateOrphaned,
                ["recovery_handle"] = _current.Handle,
                ["recovery_generation"] = _current.RecoveryGeneration,
                ["verified_rollback_available"] = _current.State != StateRecoveryUncertain
            };
            if (_current.Reason != null) state["reason"] = _current.Reason;
            return state;
        }

        /// <summary>
        /// What became of a transaction, without touching it.
        /// </summary>
        /// <remarks>
        /// A caller whose commit acknowledgement was lost needs to learn the
        /// outcome without repeating the operation to discover it. Reading is the
        /// whole point: this never opens, finishes or mutates anything.
        /// </remarks>
        public JToken Status(JObject parameters)
        {
            EnsureJudged();
            var id = parameters.Value<string>("transaction");
            var active = ActiveState();
            if (String.IsNullOrEmpty(id))
                return new JObject { ["active"] = active, ["finished"] = null };
            if (active != null && String.Equals(active.Value<string>("transaction"), id,
                    StringComparison.Ordinal))
                return new JObject { ["active"] = active, ["finished"] = null };
            if (!_finished.TryGetValue(id, out var record))
                throw new RoboVisionException(
                    "NOT_FOUND",
                    "no open or recently finished transaction has that id",
                    false,
                    new JObject { ["transaction"] = id });
            return new JObject { ["active"] = null, ["finished"] = record.DeepClone() };
        }

        private void Finish(string state, string reason, JObject evidence = null)
        {
            if (_current == null) return;
            var id = _current.Id;
            Remember(id, state, _current.World, reason, evidence,
                _current.Handle, _current.RecoveryGeneration);
            _current = null;
            TransactionMemory.Forget();
            // The operations recorded inside it keep their tombstones and learn
            // what became of the transaction. Discarding those records with the
            // transaction reopens the hole this exists to close: operation
            // executes, reply is lost, transaction rolls back, client retries —
            // and with the key gone it executes again, against a scene where the
            // first one was undone.
            _host.Invocations.NoteTransactionOutcome(id, state);
        }

        private void Remember(string id, string state, string world, string reason,
            JObject evidence = null, string handle = null, int generation = 0)
        {
            var record = new JObject
            {
                ["transaction"] = id,
                ["state"] = state,
                ["world_incarnation"] = world,
                ["recovery_handle"] = handle,
                ["recovery_generation"] = generation
            };
            if (reason != null) record["reason"] = reason;
            if (evidence != null)
                foreach (var property in evidence.Properties())
                    record[property.Name] = property.Value.DeepClone();
            _finished[id] = record;
            _finishedOrder.Enqueue(id);
            while (_finishedOrder.Count > 32) _finished.Remove(_finishedOrder.Dequeue());
        }

        /// <summary>A transaction that ended is told what ended it.</summary>
        private RoboVisionException Stale(string id)
        {
            if (id == null || !_finished.TryGetValue(id, out var record)) return null;
            var state = record.Value<string>("state");
            var code = state == StateAbandoned ? "TRANSACTION_ABANDONED" : "TRANSACTION_FINISHED";
            return new RoboVisionException(code,
                "that transaction is no longer open: " + state
                + (record["reason"] != null ? " (" + record.Value<string>("reason") + ")" : ""),
                false, (JObject)record.DeepClone());
        }
    }
}
