using System;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// Reclaiming a transaction whose owner, or whose bridge, went away.
    /// </summary>
    /// <remarks>
    /// Ownership decides who may act on a transaction; this decides what is
    /// left to act on. They are separate files because they answer to different
    /// evidence: one to the connection making the request, the other to what
    /// actually survived an interruption — which was measured, in both
    /// directions, before any of it was written.
    /// </remarks>
    internal sealed partial class RoboVisionTransactions
    {
        /// <summary>
        /// Take ownership of an orphaned transaction by proving you opened it.
        /// </summary>
        /// <remarks>
        /// Every refusal here leaves everything exactly as it was — owner,
        /// state, verifier, scene, revision and journal — because an adoption
        /// attempt that changes something is a way to attack a transaction
        /// without ever passing the check. The token is rotated on success so a
        /// leaked one cannot be replayed, and the replacement goes only to the
        /// client that just proved it.
        ///
        /// What this replaces was weaker than it looked: any client could
        /// commit or roll back an orphan once the owner's socket dropped. A
        /// dropped TCP connection is not authorization.
        /// </remarks>
        public JToken Adopt(JObject parameters, long clientId)
        {
            EnsureJudged();
            var id = parameters.Value<string>("transaction");
            var offered = parameters.Value<string>("recovery_token");
            if (String.IsNullOrWhiteSpace(id) || String.IsNullOrWhiteSpace(offered))
                throw Refused("transaction and recovery_token are required");
            if (_current == null || !String.Equals(_current.Id, id, StringComparison.Ordinal))
                throw Refused("no such transaction is open in this world");
            if (_current.State != StateOrphaned)
                throw Refused("that transaction is not waiting to be adopted");
            if (_current.Recovery == null || !_current.Recovery.Matches(offered))
                throw Refused("the recovery token does not prove ownership of that transaction");

            _current.OwnerClientId = clientId;
            _current.State = StateActive;
            _current.Reason = null;
            // Rotation is what stops a leaked secret being a permanent key, and
            // it is also where a lost reply does the most damage: the old secret
            // is dead the moment this returns. A client that supplies the
            // verifier for its next secret already holds the replacement, so
            // losing this reply costs it nothing.
            string rotated = null;
            var nextVerifier = parameters.Value<string>("next_recovery_verifier");
            _current.Recovery = String.IsNullOrEmpty(nextVerifier)
                ? RoboVisionRecoveryToken.Mint(out rotated)
                : RoboVisionRecoveryToken.Precommit(nextVerifier);
            // Counted after the swap, so the number a client reads back is the
            // generation of the credential that is now current.
            _current.RecoveryGeneration++;
            Persist();

            var state = ActiveState();
            state["adopted"] = true;
            state["recovery_precommitted"] = _current.Recovery.Precommitted;
            if (rotated != null)
            {
                state["recovery_token"] = rotated;
                state["recovery_token_note"] =
                    "Rotated by this adoption; the previous token no longer works. Send "
                    + "next_recovery_verifier with adopt so a lost reply cannot strand the transaction.";
            }
            return state;
        }

        /// <summary>
        /// One refusal for every failed adoption, saying nothing a guesser could use.
        /// </summary>
        /// <remarks>
        /// The messages differ enough to debug an honest mistake and not enough
        /// to distinguish "wrong token" from "wrong transaction" by timing or by
        /// payload — and none of them touches state, so a refusal is not a move.
        /// </remarks>
        private static RoboVisionException Refused(string message)
        {
            return new RoboVisionException("TRANSACTION_ADOPTION_REFUSED", message, false,
                new JObject { ["adopted"] = false });
        }

        /// <summary>Give up an interrupted transaction that cannot be proved either way.</summary>
        public JToken Discard(JObject parameters, long clientId)
        {
            EnsureJudged();
            var id = parameters.Value<string>("transaction");
            if (String.IsNullOrWhiteSpace(id))
                throw new RoboVisionException("INVALID_PARAMS", "transaction is required");
            var stale = Stale(id);
            if (stale != null) throw stale;
            if (_current == null || !String.Equals(_current.Id, id, StringComparison.Ordinal))
                throw new RoboVisionException("NO_TRANSACTION", "no transaction is open");
            // An owned, healthy transaction is its owner's to discard. An
            // orphaned or uncertain one has no owner left to ask.
            if (_current.State == StateActive && _current.OwnerClientId != clientId)
                throw Foreign(_current, clientId);
            var from = _current.State;
            var reason = _current.Reason ?? "discarded_by_client";
            Finish(StateAbandoned, reason);
            return new JObject
            {
                ["transaction"] = id,
                ["state"] = StateAbandoned,
                ["discarded_from"] = from,
                ["reason"] = reason
            };
        }

        private void Persist()
        {
            if (_current == null) { TransactionMemory.Forget(); return; }
            TransactionMemory.Remember(new JObject
            {
                ["id"] = _current.Id,
                ["label"] = _current.Label,
                ["world"] = _current.World,
                ["begin_revision"] = _current.BeginRevision,
                ["begin_fingerprint"] = _current.BeginFingerprint,
                ["undo_group"] = _current.UndoGroup,
                ["session_scoped"] = _current.SessionScoped,
                ["external_change"] = _current.ExternalChangeFingerprint,
                ["handle"] = _current.Handle,
                ["recovery_generation"] = _current.RecoveryGeneration,
                ["verifier"] = _current.Recovery != null ? _current.Recovery.Verifier : null,
                ["precommitted"] = _current.Recovery != null && _current.Recovery.Precommitted
            });
        }

        /// <summary>
        /// Judge an interrupted transaction the first time anything asks about one.
        /// </summary>
        /// <remarks>
        /// This used to happen at a chosen point in reconciliation, and that was
        /// fragile in a way a test caught: a reload that establishes the world
        /// during a play mode transition consumed the moment, and the
        /// transaction was never restored or reported at all. Doing it wherever
        /// a transaction is first consulted has no such moment to miss — it needs
        /// only that the world has been read, which every one of those paths has
        /// already ensured.
        /// </remarks>
        internal void EnsureJudged()
        {
            if (_judged || !_host.WorldSettled) return;
            _judged = true;
            OnWorldEstablished(_host.WorldIncarnation, _host.WorldResumed);
        }

        /// <summary>
        /// Decide what an interrupted transaction is worth after the bridge was rebuilt.
        /// </summary>
        /// <remarks>
        /// Both halves of this were measured rather than assumed. Unity's undo
        /// information survives a domain reload: reverting to the transaction's
        /// group afterwards really did restore the begin fingerprint, so an
        /// interrupted transaction in a saved scene is genuinely recoverable and
        /// comes back orphaned, waiting for its owner's token.
        ///
        /// In an unsaved scene it does not. Every object is re-addressed by the
        /// reload, so the checkpoint names identities that no longer exist:
        /// undo removed the right objects and the begin fingerprint still could
        /// not be reproduced. Restoring that as rollback-capable would promise a
        /// proof the mechanism cannot deliver, so it comes back recovery-uncertain
        /// and can only be discarded.
        ///
        /// A process restart reaches neither branch: SessionState dies with the
        /// process, so there is nothing to restore and nothing that could be
        /// mistaken for a live transaction.
        /// </remarks>
        public void OnWorldEstablished(string world, bool resumed)
        {
            var stashed = TransactionMemory.Recall();
            if (stashed == null) return;
            var id = stashed.Value<string>("id");
            if (id == null) { TransactionMemory.Forget(); return; }

            if (!resumed || stashed.Value<string>("world") != world)
            {
                // Whatever it described is not what is open. Recorded as ended
                // so a client asking about it is told why, not that it never was.
                Remember(id, StateAbandoned, stashed.Value<string>("world"), "world_replaced");
                TransactionMemory.Forget();
                return;
            }

            var sessionScoped = stashed.Value<bool>("session_scoped");
            _current = new Transaction
            {
                Id = id,
                Label = stashed.Value<string>("label"),
                World = world,
                BeginRevision = stashed.Value<long>("begin_revision"),
                BeginFingerprint = stashed.Value<string>("begin_fingerprint"),
                UndoGroup = stashed.Value<int>("undo_group"),
                SessionScoped = sessionScoped,
                ExternalChangeFingerprint = stashed.Value<string>("external_change"),
                // No connection owns it: the socket that did is gone with the
                // domain, and only the recovery token can put that right.
                OwnerClientId = -1,
                State = sessionScoped ? StateRecoveryUncertain : StateOrphaned,
                Reason = sessionScoped ? "checkpoint_identities_lost" : "bridge_reloaded",
                Handle = stashed.Value<string>("handle"),
                RecoveryGeneration = stashed.Value<int>("recovery_generation"),
                Recovery = RoboVisionRecoveryToken.FromStored(
                    stashed.Value<string>("verifier"), stashed.Value<bool>("precommitted"))
            };
        }
    }

    /// <summary>What survives a domain reload about an interrupted transaction.</summary>
    /// <remarks>
    /// SessionState, for the same reason the world uses it: it survives an
    /// assembly reload and dies with the process, which is exactly the boundary
    /// beyond which nothing here should be believed. Only the verifier is
    /// stashed, never a token.
    /// </remarks>
    internal static class TransactionMemory
    {
        private const string Key = "RoboVision.Transaction";

        internal static void Remember(JObject record)
        {
            SessionState.SetString(Key, record.ToString(Newtonsoft.Json.Formatting.None));
        }

        internal static JObject Recall()
        {
            var raw = SessionState.GetString(Key, null);
            if (String.IsNullOrEmpty(raw)) return null;
            try { return JObject.Parse(raw); }
            catch (Exception) { return null; }
        }

        internal static void Forget() => SessionState.EraseString(Key);
    }
}
