using System;
using System.Diagnostics;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// What a caller is told, including what was put back when a call failed.
    /// </summary>
    /// <remarks>
    /// The envelope has the same shape whether the operation worked or not, and
    /// a failed mutation is undone before anyone is told about it — so the two
    /// belong together: the recovery decides half of what the failure envelope
    /// says, and a client's next move depends on being able to tell "nothing
    /// happened" from "something did and could not be put back".
    /// </remarks>
    internal sealed partial class RoboVisionHost
    {
        private RoboVisionException TryRecoverFailedOperation(
            RoboVisionUndo.OperationCheckpoint checkpoint,
            SceneRead checkpointRead,
            string requestId,
            Exception original,
            out JObject recovery)
        {
            recovery = null;
            if (checkpoint == null) return null;
            try
            {
                recovery = RoboVisionUndo.RecoverFailedMutation(checkpoint);
                _reconciler.AcceptRecovered(checkpointRead);
                return null;
            }
            catch (RoboVisionException recoveryError)
            {
                var data = recoveryError.Data is JObject objectData ? (JObject)objectData.DeepClone() : new JObject();
                var originalData = new JObject
                {
                    ["type"] = original.GetType().Name,
                    ["message"] = original.Message
                };
                if (original is RoboVisionException rvOriginal)
                {
                    originalData["code"] = rvOriginal.Code;
                    if (rvOriginal.Data != null) originalData["data"] = rvOriginal.Data.DeepClone();
                }
                data["original_error"] = originalData;
                // The mutation left something behind that could not be undone. That
                // residue is ours, so it is reconciled and attributed to the request
                // that caused it, rather than left for the next refresh to blame on
                // a human edit.
                _reconciler.Reconcile(RoboVisionReconciler.Agent, requestId, checkpointRead);
                return new RoboVisionException(recoveryError.Code, recoveryError.Message, recoveryError.Retryable, data);
            }
        }

        /// <summary>
        /// The envelope, which is the same shape whether the call worked or not.
        /// </summary>
        /// <remarks>
        /// PROTOCOL.md said every response carries `state_domain` and
        /// `consistency`; measured, no error response on either host carried
        /// either of them. A failure still happened in a state domain — the
        /// editor was playing or it was not — and, once a method resolves, still
        /// went through a tool with a declared consistency class. Before that
        /// point there is no class to report, and `unknown` says so rather than
        /// claiming the strongest one.
        /// </remarks>
        private JObject Envelope(string requestId, Stopwatch watch, string consistency)
        {
            watch.Stop();
            return new JObject
            {
                ["rv"] = ProtocolVersion,
                ["id"] = requestId ?? "invalid",
                ["revision"] = Revision,
                ["consistency"] = consistency ?? ReadsUnknown,
                ["state_domain"] = StateDomain,
                ["timing_ms"] = Math.Round(watch.Elapsed.TotalMilliseconds, 3)
            };
        }

        private JObject ErrorResponse(string requestId, Stopwatch watch, RoboVisionException ex, JObject recovery,
            string consistency)
        {
            var response = Envelope(requestId, watch, consistency);
            var error = new JObject
            {
                ["code"] = ex.Code,
                ["message"] = ex.Message,
                ["retryable"] = ex.Retryable
            };
            if (recovery != null)
            {
                error["data"] = new JObject
                {
                    ["operation_error_data"] = ex.Data?.DeepClone(),
                    ["automatic_recovery"] = recovery
                };
            }
            else if (ex.Data != null)
            {
                error["data"] = ex.Data.DeepClone();
            }
            response["ok"] = false;
            response["error"] = error;
            return response;
        }
    }
}
