using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// Who holds a transaction, which world it belongs to, and what it may still do.
    /// </summary>
    /// <remarks>
    /// A transaction used to be a label, an undo group and a fingerprint, with
    /// no idea which world it was opened in. Measured before this was changed:
    /// begin a transaction, replace the scene Single-mode, and the transaction
    /// stayed <c>active</c> in <c>system.hello</c> with a begin fingerprint from
    /// a world that no longer existed — a further mutation succeeded, and
    /// <c>transaction.commit</c> <em>succeeded</em>, collapsing undo groups in a
    /// universe the checkpoint never described. The same happened entering a
    /// Prefab Stage, leaving one, and swapping one for another.
    ///
    /// So identity is scoped to the world now, and lifecycle is an explicit
    /// state with a reason rather than a pair of booleans. A transaction whose
    /// world is gone is abandoned, not silently portable, and a later commit or
    /// rollback of it is told what happened rather than that it never existed.
    ///
    /// Reclaiming an interrupted one lives in RoboVisionTransactionRecovery.cs;
    /// ownership decides who may act, recovery decides what survived.
    /// </remarks>
    internal sealed partial class RoboVisionTransactions
    {
        internal const string StateActive = "active";
        internal const string StateOrphaned = "orphaned";
        internal const string StateAbandoned = "abandoned";
        internal const string StateCommitted = "committed";
        internal const string StateRolledBack = "rolled_back";
        internal const string StateRecoveryUncertain = "recovery_uncertain";

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
        }

        private readonly RoboVisionHost _host;
        private Transaction _current;
        /// <summary>Whether an interrupted transaction has been judged; see EnsureJudged.</summary>
        private bool _judged;
        // What happened to transactions that ended. Small on purpose: it exists
        // so a late commit or rollback is answered with the truth instead of
        // "no transaction is active", which reads like the client invented an id.
        private readonly Dictionary<string, JObject> _finished =
            new Dictionary<string, JObject>(StringComparer.Ordinal);
        private readonly Queue<string> _finishedOrder = new Queue<string>();

        public RoboVisionTransactions(RoboVisionHost host)
        {
            _host = host;
        }

        /// <summary>A transaction is open and can still act on the scene.</summary>
        public bool Active
        {
            get
            {
                EnsureJudged();
                return _current != null
                    && (_current.State == StateActive || _current.State == StateOrphaned);
            }
        }

        public long ActiveOwner => _current != null ? _current.OwnerClientId : -1;
        public bool ActiveOwnerDisconnected => _current != null && _current.State == StateOrphaned;
        public string ActiveId => Active ? _current.Id : null;

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
                ["verified_rollback_available"] = _current.State != StateRecoveryUncertain
            };
            if (_current.Reason != null) state["reason"] = _current.Reason;
            return state;
        }

        // ------------------------------------------------------------ lifecycle

        /// <summary>A connection went away. The transaction waits for its owner to prove itself.</summary>
        public void ClientDisconnected(long clientId)
        {
            if (_current == null || _current.State != StateActive) return;
            if (_current.OwnerClientId != clientId) return;
            _current.State = StateOrphaned;
            _current.Reason = "owner_disconnected";
            Persist();
        }

        /// <summary>The world this transaction described is gone.</summary>
        public string AbandonForWorld(string reason)
        {
            if (_current == null) return null;
            var id = _current.Id;
            Finish(StateAbandoned, reason);
            return id;
        }

        public void MarkExternalChange(string fingerprint)
        {
            if (!Active) return;
            if (_current.ExternalChangeFingerprint == null)
            {
                _current.ExternalChangeFingerprint = fingerprint;
                Persist();
            }
        }

        private void Finish(string state, string reason)
        {
            if (_current == null) return;
            Remember(_current.Id, state, _current.World, reason);
            _current = null;
            TransactionMemory.Forget();
        }

        private void Remember(string id, string state, string world, string reason)
        {
            var record = new JObject
            {
                ["transaction"] = id,
                ["state"] = state,
                ["world_incarnation"] = world
            };
            if (reason != null) record["reason"] = reason;
            _finished[id] = record;
            _finishedOrder.Enqueue(id);
            while (_finishedOrder.Count > 32) _finished.Remove(_finishedOrder.Dequeue());
        }

        // -------------------------------------------------------------- begin

        public JToken Begin(JObject parameters, long ownerClientId)
        {
            EnsureJudged();
            if (_current != null)
                throw new RoboVisionException("TRANSACTION_ACTIVE", "a transaction is already active",
                    data: new JObject { ["active"] = ActiveState() });

            var read = _host.CurrentRead;
            var world = _host.WorldIncarnation;
            var label = parameters.Value<string>("label") ?? "agent edit";
            var recovery = RoboVisionRecoveryToken.Mint(out var secret);

            _current = new Transaction
            {
                // Self-scoping: the world is in the id, so a transaction from a
                // replaced world is recognisably not this one even before its
                // record is consulted, and a client's own label can never be
                // mistaken for the host's identity for it.
                Id = "rvtx:" + Tail(world) + ":" + Guid.NewGuid().ToString("N"),
                Label = label,
                World = world,
                BeginRevision = _host.Revision,
                BeginFingerprint = read.Fingerprint,
                UndoGroup = RoboVisionUndo.OpenGroup(label),
                SessionScoped = HasSessionScopedIds(read),
                OwnerClientId = ownerClientId,
                Recovery = recovery
            };
            Persist();

            return new JObject
            {
                ["transaction"] = _current.Id,
                ["label"] = label,
                ["state"] = _current.State,
                ["world_incarnation"] = world,
                ["begin_revision"] = _current.BeginRevision,
                ["begin_fingerprint"] = _current.BeginFingerprint,
                ["undo_group"] = _current.UndoGroup,
                ["owner_client"] = ownerClientId,
                ["contaminated"] = false,
                // Returned exactly once. There is no call that gives it back.
                ["recovery_token"] = secret,
                ["recovery_token_note"] =
                    "Store this. It is the only way to reclaim this transaction after a disconnect "
                    + "or a domain reload, and the host keeps only a hash of it."
            };
        }

        private static string Tail(string incarnation)
        {
            if (String.IsNullOrEmpty(incarnation)) return "unknown";
            var split = incarnation.IndexOf(':');
            if (split < 0) return incarnation;
            var tail = incarnation.Substring(split + 1);
            return tail.Length > 8 ? tail.Substring(0, 8) : tail;
        }

        /// <summary>Does this checkpoint depend on identities that die with the domain?</summary>
        private static bool HasSessionScopedIds(SceneRead read)
        {
            if (read == null || read.State == null) return false;
            return ((JArray)read.State["scenes"]).SelectMany(scene => (JArray)scene["objects"])
                .Any(o => (o.Value<string>("id") ?? "")
                    .StartsWith(RoboVisionSessionHandles.Prefix, StringComparison.Ordinal));
        }

        // ---------------------------------------------------- commit/rollback

        public JToken Commit(JObject parameters, long clientId)
        {
            var tx = Require(parameters, clientId);
            AssertSafeOrForced(tx, parameters, "commit");
            RoboVisionUndo.Collapse(tx.UndoGroup);
            var finalFingerprint = RoboVisionSceneRead.ComputeFingerprint();
            var contaminated = tx.ExternalChangeFingerprint != null;
            var id = tx.Id;
            var begin = tx.BeginFingerprint;
            Finish(StateCommitted, null);
            return new JObject
            {
                ["transaction"] = id,
                ["committed"] = true,
                ["state"] = StateCommitted,
                ["begin_fingerprint"] = begin,
                ["final_fingerprint"] = finalFingerprint,
                ["forced_after_external_change"] = contaminated
            };
        }

        public JToken Rollback(JObject parameters, long clientId)
        {
            var tx = Require(parameters, clientId);
            AssertSafeOrForced(tx, parameters, "rollback");
            var actual = RoboVisionUndo.RevertTo(tx.UndoGroup);
            if (!String.Equals(actual, tx.BeginFingerprint, StringComparison.Ordinal))
                throw new RoboVisionException(
                    "ROLLBACK_INCOMPLETE",
                    "Unity Undo did not restore the transaction begin fingerprint",
                    data: new JObject
                    {
                        ["transaction"] = tx.Id,
                        ["expected_fingerprint"] = tx.BeginFingerprint,
                        ["actual_fingerprint"] = actual
                    });
            var contaminated = tx.ExternalChangeFingerprint != null;
            var id = tx.Id;
            Finish(StateRolledBack, null);
            return new JObject
            {
                ["transaction"] = id,
                ["rolled_back"] = true,
                ["state"] = StateRolledBack,
                ["fingerprint"] = actual,
                ["forced_after_external_change"] = contaminated
            };
        }

        private static void AssertSafeOrForced(Transaction tx, JObject parameters, string action)
        {
            if (tx.ExternalChangeFingerprint == null || (parameters.Value<bool?>("force") ?? false)) return;
            throw new RoboVisionException(
                "TRANSACTION_CONTAMINATED",
                "an out-of-band editor change occurred during the RoboVision transaction; refusing automatic " + action,
                data: new JObject
                {
                    ["transaction"] = tx.Id,
                    ["begin_fingerprint"] = tx.BeginFingerprint,
                    ["external_change_fingerprint"] = tx.ExternalChangeFingerprint,
                    ["force_parameter"] = "Set force=true only if overwriting/including the external edit is intentional."
                });
        }

        /// <summary>
        /// What this client is allowed to do with the transaction it named.
        /// </summary>
        /// <remarks>
        /// Ownership and contamination are separate questions and are answered
        /// in that order. Ownership says who is authorised to act; contamination
        /// says whether acting can still claim what it will affect. An adopted
        /// owner can be refused for contamination, and an unauthorised client
        /// never reaches the point where `force` would mean anything.
        /// </remarks>
        private Transaction Require(JObject parameters, long clientId)
        {
            EnsureJudged();
            var id = parameters.Value<string>("transaction");
            if (String.IsNullOrWhiteSpace(id))
                throw new RoboVisionException("INVALID_PARAMS", "transaction is required");
            var stale = Stale(id);
            if (stale != null) throw stale;
            if (_current == null)
                throw new RoboVisionException("NO_TRANSACTION", "no transaction is open");
            if (!String.Equals(id, _current.Id, StringComparison.Ordinal))
                throw new RoboVisionException(
                    "INVALID_PARAMS",
                    "transaction id does not match the open transaction",
                    data: new JObject { ["open"] = _current.Id });
            if (_current.State == StateRecoveryUncertain)
                throw new RoboVisionException(
                    "TRANSACTION_RECOVERY_UNCERTAIN",
                    "that transaction was interrupted and its checkpoint can no longer be reproduced",
                    false,
                    new JObject
                    {
                        ["transaction"] = _current.Id,
                        ["reason"] = _current.Reason,
                        ["remedy"] = "transaction.discard is the only outcome this can honestly offer."
                    });
            if (_current.State == StateOrphaned)
                throw new RoboVisionException(
                    "TRANSACTION_ORPHANED",
                    "that transaction is waiting to be adopted by the client that opened it",
                    true,
                    new JObject { ["transaction"] = _current.Id, ["reason"] = _current.Reason });
            if (_current.OwnerClientId != clientId) throw Foreign(_current, clientId);
            return _current;
        }

        private static RoboVisionException Foreign(Transaction tx, long clientId)
        {
            return new RoboVisionException(
                "TRANSACTION_FOREIGN",
                "another client holds the open transaction",
                true,
                new JObject { ["transaction"] = tx.Id, ["your_client"] = clientId });
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
