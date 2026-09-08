using System;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace Kodaxa.RoboVision.Editor
{
    internal sealed class RoboVisionTransactions
    {
        internal sealed class OperationCheckpoint
        {
            public int UndoGroup;
            public string Fingerprint;
            public string Label;
        }

        private sealed class Transaction
        {
            public string Id;
            public string Label;
            public int UndoGroup;
            public string BeginFingerprint;
            public string ExternalChangeFingerprint;
        }

        private Transaction _active;

        public bool Active => _active != null;
        public RoboVisionTransactions(RoboVisionHost _host) { }

        public void MarkExternalChange(string fingerprint)
        {
            if (_active == null) return;
            if (_active.ExternalChangeFingerprint == null) _active.ExternalChangeFingerprint = fingerprint;
        }

        public OperationCheckpoint PrepareMutation(string label)
        {
            var fingerprint = RoboVisionSceneTools.ComputeFingerprint();
            Undo.IncrementCurrentGroup();
            var group = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("RoboVision OP " + label);
            return new OperationCheckpoint { UndoGroup = group, Fingerprint = fingerprint, Label = label };
        }

        public JObject RecoverFailedMutation(OperationCheckpoint checkpoint)
        {
            var current = RoboVisionSceneTools.ComputeFingerprint();
            if (String.Equals(current, checkpoint.Fingerprint, StringComparison.Ordinal))
                return new JObject
                {
                    ["recovered"] = true,
                    ["undo_group"] = checkpoint.UndoGroup,
                    ["fingerprint"] = checkpoint.Fingerprint,
                    ["undo_performed"] = false
                };

            Undo.FlushUndoRecordObjects();
            Undo.RevertAllDownToGroup(checkpoint.UndoGroup);
            current = RoboVisionSceneTools.ComputeFingerprint();
            if (!String.Equals(current, checkpoint.Fingerprint, StringComparison.Ordinal))
                throw new RoboVisionException(
                    "MUTATION_RECOVERY_INCOMPLETE",
                    "failed Unity operation changed editor state and Undo did not restore the pre-operation fingerprint",
                    data: new JObject
                    {
                        ["operation"] = checkpoint.Label,
                        ["undo_group"] = checkpoint.UndoGroup,
                        ["expected_fingerprint"] = checkpoint.Fingerprint,
                        ["actual_fingerprint"] = current
                    });

            return new JObject
            {
                ["recovered"] = true,
                ["undo_group"] = checkpoint.UndoGroup,
                ["fingerprint"] = checkpoint.Fingerprint,
                ["undo_performed"] = true
            };
        }

        public JToken Begin(JObject parameters)
        {
            if (_active != null)
                throw new RoboVisionException("TRANSACTION_ACTIVE", "a transaction is already active",
                    data: new JObject { ["transaction"] = _active.Id });

            var id = parameters.Value<string>("transaction") ?? ("tx:" + Guid.NewGuid());
            var label = parameters.Value<string>("label") ?? "agent edit";
            var fingerprint = RoboVisionSceneTools.ComputeFingerprint();
            Undo.IncrementCurrentGroup();
            var group = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("RoboVision " + label);
            _active = new Transaction { Id = id, Label = label, UndoGroup = group, BeginFingerprint = fingerprint };
            return new JObject
            {
                ["transaction"] = id,
                ["label"] = label,
                ["begin_fingerprint"] = fingerprint,
                ["undo_group"] = group,
                ["contaminated"] = false
            };
        }

        public JToken Commit(JObject parameters)
        {
            var tx = Require(parameters);
            AssertSafeOrForced(tx, parameters, "commit");
            Undo.FlushUndoRecordObjects();
            Undo.CollapseUndoOperations(tx.UndoGroup);
            var finalFingerprint = RoboVisionSceneTools.ComputeFingerprint();
            _active = null;
            return new JObject
            {
                ["transaction"] = tx.Id,
                ["committed"] = true,
                ["begin_fingerprint"] = tx.BeginFingerprint,
                ["final_fingerprint"] = finalFingerprint,
                ["forced_after_external_change"] = tx.ExternalChangeFingerprint != null
            };
        }

        public JToken Rollback(JObject parameters)
        {
            var tx = Require(parameters);
            AssertSafeOrForced(tx, parameters, "rollback");
            Undo.FlushUndoRecordObjects();
            Undo.RevertAllDownToGroup(tx.UndoGroup);
            var actual = RoboVisionSceneTools.ComputeFingerprint();
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
            _active = null;
            return new JObject
            {
                ["transaction"] = tx.Id,
                ["rolled_back"] = true,
                ["fingerprint"] = actual,
                ["forced_after_external_change"] = tx.ExternalChangeFingerprint != null
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

        private Transaction Require(JObject parameters)
        {
            if (_active == null)
                throw new RoboVisionException("NO_TRANSACTION", "no transaction is active");
            var id = parameters.Value<string>("transaction");
            if (String.IsNullOrWhiteSpace(id))
                throw new RoboVisionException("INVALID_PARAMS", "transaction is required");
            if (!String.Equals(id, _active.Id, StringComparison.Ordinal))
                throw new RoboVisionException(
                    "INVALID_PARAMS",
                    "transaction id does not match active transaction",
                    data: new JObject { ["active"] = _active.Id });
            return _active;
        }
    }
}
