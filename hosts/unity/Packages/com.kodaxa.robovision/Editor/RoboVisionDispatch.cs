using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// One request in, one response out, with the policy that decides what it is worth.
    /// </summary>
    /// <remarks>
    /// Separate from the host for the same reason the Blender host has its own
    /// `dispatch.py`: everything here is policy that applies to every tool
    /// rather than to any one of them — which mode the editor has to be in, what
    /// a response's revision is worth, whether the answer describes authored
    /// state, whether the command moved anything, and what happens to a mutation
    /// that fails halfway. A handler that had to remember any of this would
    /// eventually forget.
    /// </remarks>
    internal sealed partial class RoboVisionHost
    {
        internal JObject Dispatch(JObject raw) => Dispatch(raw, LocalClientId);

        internal JObject Dispatch(JObject raw, long clientId)
        {
            CurrentClientId = clientId;
            var watch = Stopwatch.StartNew();
            var requestId = raw.Value<string>("id");
            RoboVisionTransactions.OperationCheckpoint checkpoint = null;
            SceneRead checkpointRead = null;
            try
            {
                if (raw.Value<string>("rv") != ProtocolVersion)
                    throw new RoboVisionException("PROTOCOL_MISMATCH", "expected protocol " + ProtocolVersion);
                // Checked before defaulting: coalescing first made this unreachable,
                // so a request with no id was accepted and answered as "invalid".
                if (String.IsNullOrWhiteSpace(requestId))
                    throw new RoboVisionException("INVALID_REQUEST", "id must be a non-empty string");
                var method = raw.Value<string>("method");
                if (String.IsNullOrWhiteSpace(method))
                    throw new RoboVisionException("INVALID_REQUEST", "method must be a non-empty string");
                var parameters = raw["params"] as JObject ?? new JObject();
                if (!_tools.TryGetValue(method, out var spec))
                    throw new RoboVisionException("UNKNOWN_METHOD", "unknown method: " + method);

                // Concurrency policy: a transaction belongs to the connection
                // that opened it. Another client's mutation would otherwise join
                // that transaction silently and be rolled back with it, so it is
                // refused rather than guessed at. Reads stay open to everyone,
                // and transaction control is allowed so an orphaned transaction
                // can be finished deliberately.
                if (spec.Mutating && !spec.TransactionControl && Transactions.Active
                    && Transactions.ActiveOwner != clientId)
                {
                    throw new RoboVisionException(
                        "TRANSACTION_FOREIGN",
                        "another client holds the active transaction",
                        true,
                        new JObject
                        {
                            ["active"] = Transactions.ActiveState(),
                            ["your_client"] = clientId
                        });
                }

                // Authoring is an Edit Mode operation. In play mode these tools
                // still ran, and the defect that produced was worse than a
                // refusal would have been: `object.create` really did create a
                // runtime GameObject, reconciliation correctly declined to make
                // a runtime read the authored baseline, so the response said
                // `outcome: noop` about a mutation that had visibly happened —
                // and the object then evaporated on exit. Measured before it was
                // changed. The same verb must not mean "author a scene object"
                // in one mode and "spawn something ephemeral" in the other; if
                // runtime manipulation is wanted it needs its own tool family
                // and its own state domain.
                if ((spec.Mutating || spec.TransactionControl)
                    && EditorApplication.isPlayingOrWillChangePlaymode)
                {
                    throw new RoboVisionException(
                        "PLAY_MODE_MUTATION_REFUSED",
                        "authoring mutations are Edit Mode operations; the editor is in play mode",
                        true,
                        new JObject
                        {
                            ["method"] = method,
                            ["playing"] = EditorApplication.isPlaying,
                            ["transitioning"] = EditorApplication.isPlayingOrWillChangePlaymode
                                && !EditorApplication.isPlaying
                        });
                }

                // Read consistency is a property of the tool, decided once here
                // rather than arranged by each handler. A call that presents scene
                // state re-reads first, or it can return current state stamped with
                // the revision of the state before it.
                if (spec.Mutating || spec.TransactionControl || spec.Reads == ReadsAuthoritative)
                    checkpointRead = _reconciler.Resync();
                else if (spec.Reads == ReadsNotified)
                    _reconciler.RefreshDirtyState();

                var revisionToken = raw["if_revision"];
                if (spec.Mutating && revisionToken != null && revisionToken.Type != JTokenType.Null)
                {
                    var expected = revisionToken.Value<long>();
                    if (expected != Revision)
                        throw new RoboVisionException(
                            "STALE_REVISION",
                            "scene revision changed",
                            true,
                            new JObject { ["expected"] = expected, ["actual"] = Revision });
                }

                if (spec.Mutating && !spec.TransactionControl)
                    checkpoint = Transactions.PrepareMutation(method, checkpointRead.Fingerprint);

                var result = spec.Handler(parameters);

                string outcome = null;
                if (spec.Mutating && (!spec.TransactionControl || method == "transaction.rollback"))
                {
                    // applied and noop are both success; they differ in whether the
                    // scene moved. A command that ran cleanly and left the state
                    // exactly as it found it does not advance the revision.
                    var accepted = _reconciler.AcceptOwnMutation(checkpointRead, requestId);
                    outcome = accepted.Moved ? "applied" : "noop";
                }

                watch.Stop();
                var response = new JObject
                {
                    ["rv"] = ProtocolVersion,
                    ["id"] = requestId ?? "invalid",
                    ["ok"] = true,
                    ["revision"] = Revision,
                    ["consistency"] = spec.Reads,
                    ["result"] = result,
                    ["state_domain"] = StateDomain,
                    ["timing_ms"] = Math.Round(watch.Elapsed.TotalMilliseconds, 3)
                };
                if (outcome != null) response["outcome"] = outcome;
                return response;
            }
            catch (RoboVisionException ex)
            {
                var recoveryFailure = TryRecoverFailedOperation(checkpoint, checkpointRead, requestId, ex, out var recovery);
                return ErrorResponse(requestId, watch, recoveryFailure ?? ex, recovery);
            }
            catch (Exception ex)
            {
                var recoveryFailure = TryRecoverFailedOperation(checkpoint, checkpointRead, requestId, ex, out var recovery);
                if (recoveryFailure != null)
                    return ErrorResponse(requestId, watch, recoveryFailure, recovery);
                UnityEngine.Debug.LogException(ex);
                watch.Stop();
                var error = new JObject
                {
                    ["code"] = "HOST_EXCEPTION",
                    ["message"] = ex.GetType().Name + ": host operation failed; see Unity Console",
                    ["retryable"] = false
                };
                if (recovery != null) error["data"] = new JObject { ["automatic_recovery"] = recovery };
                return new JObject
                {
                    ["rv"] = ProtocolVersion,
                    ["id"] = requestId ?? "invalid",
                    ["ok"] = false,
                    ["revision"] = Revision,
                    ["error"] = error,
                    ["timing_ms"] = Math.Round(watch.Elapsed.TotalMilliseconds, 3)
                };
            }
        }

        private RoboVisionException TryRecoverFailedOperation(
            RoboVisionTransactions.OperationCheckpoint checkpoint,
            SceneRead checkpointRead,
            string requestId,
            Exception original,
            out JObject recovery)
        {
            recovery = null;
            if (checkpoint == null) return null;
            try
            {
                recovery = Transactions.RecoverFailedMutation(checkpoint);
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

        private JObject ErrorResponse(string requestId, Stopwatch watch, RoboVisionException ex, JObject recovery)
        {
            watch.Stop();
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
            return new JObject
            {
                ["rv"] = ProtocolVersion,
                ["id"] = requestId ?? "invalid",
                ["ok"] = false,
                ["revision"] = Revision,
                ["error"] = error,
                ["timing_ms"] = Math.Round(watch.Elapsed.TotalMilliseconds, 3)
            };
        }
    }
}
