using System;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace Kodaxa.RoboVision.Editor
{
    internal sealed class RoboVisionTransactions
    {
        private sealed class Transaction
        {
            public string Id;
            public string Label;
            public int UndoGroup;
            public string BeginFingerprint;
        }

        private readonly RoboVisionHost _host;
        private Transaction _active;

        public bool Active => _active != null;
        public RoboVisionTransactions(RoboVisionHost host) { _host = host; }

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
            return new JObject { ["transaction"] = id, ["label"] = label, ["begin_fingerprint"] = fingerprint, ["undo_group"] = group };
        }

        public JToken Commit(JObject parameters)
        {
            var tx = Require(parameters);
            Undo.FlushUndoRecordObjects();
            Undo.CollapseUndoOperations(tx.UndoGroup);
            var finalFingerprint = RoboVisionSceneTools.ComputeFingerprint();
            _active = null;
            return new JObject
            {
                ["transaction"] = tx.Id, ["committed"] = true,
                ["begin_fingerprint"] = tx.BeginFingerprint, ["final_fingerprint"] = finalFingerprint
            };
        }

        public JToken Rollback(JObject parameters)
        {
            var tx = Require(parameters);
            Undo.FlushUndoRecordObjects();
            Undo.RevertAllDownToGroup(tx.UndoGroup);
            var actual = RoboVisionSceneTools.ComputeFingerprint();
            if (!String.Equals(actual, tx.BeginFingerprint, StringComparison.Ordinal))
                throw new RoboVisionException("ROLLBACK_INCOMPLETE", "Unity Undo did not restore the transaction begin fingerprint",
                    data: new JObject { ["transaction"] = tx.Id, ["expected_fingerprint"] = tx.BeginFingerprint, ["actual_fingerprint"] = actual });
            _active = null;
            return new JObject { ["transaction"] = tx.Id, ["rolled_back"] = true, ["fingerprint"] = actual };
        }

        private Transaction Require(JObject parameters)
        {
            if (_active == null) throw new RoboVisionException("NO_TRANSACTION", "no transaction is active");
            var id = parameters.Value<string>("transaction");
            if (String.IsNullOrWhiteSpace(id)) throw new RoboVisionException("INVALID_PARAMS", "transaction is required");
            if (!String.Equals(id, _active.Id, StringComparison.Ordinal))
                throw new RoboVisionException("INVALID_PARAMS", "transaction id does not match active transaction",
                    data: new JObject { ["active"] = _active.Id });
            return _active;
        }
    }
}
