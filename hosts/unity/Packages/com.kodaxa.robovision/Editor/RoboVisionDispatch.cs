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
            RoboVisionUndo.OperationCheckpoint checkpoint = null;
            SceneRead checkpointRead = null;
            // Visible to the failure paths below: a reservation must never be
            // left looking like an operation that is still running.
            JObject intent = null;
            string key = null;
            // What the answer would have been worth, for a failure that happens
            // after the tool is known. Before that it stays unknown rather than
            // being reported as the strongest class.
            string consistency = null;
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
                consistency = spec.Reads;

                // Which world is open is read out of the editor before anything
                // is compared against it: the incarnation a rebuilt bridge starts
                // with is a placeholder until something looks.
                EnsureWorldSettled();

                // A retry that names the world it was planned against can never
                // be reinterpreted in a different one. Checked before anything
                // else, because every answer below would otherwise be about the
                // wrong world.
                var expectedWorld = raw.Value<string>("expected_world");
                if (!String.IsNullOrEmpty(expectedWorld)
                    && !String.Equals(expectedWorld, WorldIncarnation, StringComparison.Ordinal))
                {
                    throw new RoboVisionException(
                        "STALE_WORLD",
                        "that request was planned against an editing context that is no longer open",
                        true,
                        new JObject
                        {
                            ["expected_world"] = expectedWorld,
                            ["current_world_incarnation"] = WorldIncarnation
                        });
                }

                var revisionToken = raw["if_revision"];
                long? ifRevision = null;
                if (revisionToken != null && revisionToken.Type != JTokenType.Null)
                {
                    if (revisionToken.Type != JTokenType.Integer || revisionToken.Value<long>() < 0)
                        throw new RoboVisionException(
                            "INVALID_REQUEST", "if_revision must be a non-negative integer");
                    ifRevision = revisionToken.Value<long>();
                }

                AssertAutonomousContract(spec, raw, parameters, ifRevision);

                // The unit and axis convention is pinned the same way the world
                // is, and for the same reason: what a unit means can be changed
                // between an agent's observation and its mutation without moving
                // either the world incarnation or the scene revision. Across two
                // editors it is stronger than that — Unity is Y up and
                // left-handed, Blender is Z up and right-handed, so a request
                // planned in one frame is refused here rather than silently
                // executed in the other.
                var expectedContract = raw.Value<string>("expected_coordinate_contract");
                if (!String.IsNullOrEmpty(expectedContract)
                    && !String.Equals(expectedContract, RoboVisionRecipe.CoordinateContract(),
                        StringComparison.Ordinal))
                {
                    throw new RoboVisionException(
                        "COORDINATE_CONTRACT_CHANGED",
                        "the unit or axis convention changed since this operation was planned",
                        true,
                        new JObject
                        {
                            ["expected_coordinate_contract"] = expectedContract,
                            ["current_coordinate_contract"] = RoboVisionRecipe.CoordinateContract(),
                            ["units"] = RoboVisionRecipe.Units()
                        });
                }

                // Randomness and identity are settled before any side effect, so
                // a lost reply leaves a client able to describe exactly what it
                // asked for.
                var seeds = SeedsFor(spec, parameters);
                var recipe = spec.Mutating ? RecipeFor(spec, parameters, seeds) : null;
                key = raw.Value<string>("idempotency_key");
                if (String.IsNullOrEmpty(key)) key = null;
                var attemptToken = raw["attempt"];
                var attempt = attemptToken != null && attemptToken.Type == JTokenType.Integer
                              && attemptToken.Value<int>() > 0
                    ? attemptToken.Value<int>()
                    : 1;

                // Concurrency policy: a transaction belongs to the connection
                // that opened it. Another client's mutation would otherwise join
                // that transaction silently and be rolled back with it, so it is
                // refused rather than guessed at. Reads stay open to everyone.
                //
                // Transaction control is no longer waved through. It used to be,
                // so that an orphan could be "finished deliberately" by anyone
                // who could reach the port — which treated a dropped TCP
                // connection as authorization. Those methods now check the
                // recovery token themselves, and an orphan is refused here to
                // everybody, including whoever happens to hold the old owner's
                // connection id.
                if (spec.Mutating && !spec.TransactionControl && Transactions.Active)
                {
                    if (Transactions.ActiveOwnerDisconnected)
                        throw new RoboVisionException(
                            "TRANSACTION_ORPHANED",
                            "the open transaction is waiting to be adopted before it can be continued",
                            true,
                            new JObject
                            {
                                ["active"] = Transactions.ActiveState(),
                                ["remedy"] = "transaction.adopt with the recovery token issued at begin"
                            });
                    if (Transactions.ActiveOwner != clientId)
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

                // Is this delivery a duplicate? Asked after the authoritative
                // read, so a replay reports the revision of the world as it is
                // now, and before any checkpoint is taken, so a duplicate costs
                // nothing.
                //
                // Only for tools that declared they resolve duplicates by
                // replaying. A tool whose policy is terminal_state answers from
                // its own state machine, and short-circuiting that here would
                // make the declaration a lie: a second rollback would be handed
                // the first one's stored result instead of being told the
                // transaction is finished.
                var replays = key != null && spec.DuplicatePolicy == DuplicateReplay;
                if (replays)
                {
                    var replay = Invocations.Check(key, recipe, attempt, WorldIncarnation);
                    if (replay != null)
                    {
                        var replayed = Envelope(requestId, watch, consistency);
                        replayed["ok"] = true;
                        foreach (var property in replay.Properties())
                            replayed[property.Name] = property.Value;
                        return replayed;
                    }
                }

                // Checked for observation-bound operations too, and before the
                // handler runs, so transaction.begin cannot take its checkpoint
                // from a scene that moved after the caller planned against it.
                if ((spec.Mutating || spec.ObservationBound) && ifRevision != null
                    && ifRevision.Value != Revision)
                {
                    throw new RoboVisionException(
                        "STALE_REVISION",
                        "scene revision changed",
                        true,
                        new JObject { ["expected"] = ifRevision.Value, ["actual"] = Revision });
                }

                // Durable before the side effect, because a crash between
                // changing Unity and recording that it changed is exactly the
                // case one post-operation entry cannot describe.
                if (spec.Mutating)
                {
                    intent = Ledger.WriteIntent(new JObject
                    {
                        ["request_id"] = requestId,
                        ["idempotency_key"] = key,
                        ["attempt"] = attempt,
                        ["method"] = method,
                        ["recipe_hash"] = recipe,
                        ["params_hash"] = RoboVisionRecipe.Hash(
                            method, parameters, HostVersion, spec.Determinism),
                        ["seeds"] = seeds,
                        ["determinism"] = spec.Determinism,
                        ["frame"] = RoboVisionRecipe.CanonicalFrame,
                        ["transaction"] = Transactions.ActiveId,
                        ["state_domain"] = StateDomain,
                        ["pre_revision"] = Revision,
                        ["pre_fingerprint"] = checkpointRead != null ? checkpointRead.Fingerprint : null,
                        ["tool_version"] = HostVersion
                    });
                    if (replays)
                        Invocations.Reserve(key, recipe, WorldIncarnation,
                            intent.Value<string>("transaction"), intent.Value<long>("sequence"));
                }

                if (spec.Mutating && !spec.TransactionControl)
                    checkpoint = RoboVisionUndo.PrepareMutation(method, checkpointRead.Fingerprint);

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

                var response = Envelope(requestId, watch, spec.Reads);
                response["ok"] = true;
                response["result"] = result;
                if (outcome != null) response["outcome"] = outcome;

                if (intent != null)
                {
                    var postFingerprint = CurrentRead != null ? CurrentRead.Fingerprint : null;
                    Ledger.WriteResult(intent.Value<long>("sequence"), new JObject
                    {
                        ["outcome"] = outcome ?? "completed",
                        ["post_revision"] = Revision,
                        ["post_fingerprint"] = postFingerprint,
                        ["journal_cursor"] = Journal.Cursor(),
                        ["response"] = new JObject
                        {
                            ["result"] = result != null ? result.DeepClone() : null,
                            ["outcome"] = outcome
                        }
                    });
                    if (replays)
                    {
                        Invocations.Complete(
                            key,
                            new JObject
                            {
                                ["result"] = result != null ? result.DeepClone() : null,
                                ["outcome"] = outcome
                            },
                            new JObject
                            {
                                ["request_id"] = requestId,
                                ["world_incarnation"] = WorldIncarnation,
                                ["recipe_hash"] = recipe,
                                ["pre_revision"] = intent["pre_revision"],
                                ["pre_fingerprint"] = intent["pre_fingerprint"],
                                ["post_revision"] = Revision,
                                ["post_fingerprint"] = postFingerprint,
                                ["outcome"] = outcome,
                                ["transaction"] = intent["transaction"]
                            });
                    }
                }
                return response;
            }
            catch (RoboVisionException ex)
            {
                var recoveryFailure = TryRecoverFailedOperation(checkpoint, checkpointRead, requestId, ex, out var recovery);
                CloseFailed(intent, key, recoveryFailure ?? (Exception)ex, recovery);
                return ErrorResponse(requestId, watch, recoveryFailure ?? ex, recovery, consistency);
            }
            catch (Exception ex)
            {
                var recoveryFailure = TryRecoverFailedOperation(checkpoint, checkpointRead, requestId, ex, out var recovery);
                // Closed before either return. Leaving early when recovery itself
                // failed left the invocation reserved for the life of the bridge,
                // and every later delivery of that key was answered IN_PROGRESS
                // about an operation that had long since stopped running.
                CloseFailed(intent, key, recoveryFailure ?? ex, recovery);
                if (recoveryFailure != null)
                    return ErrorResponse(requestId, watch, recoveryFailure, recovery, consistency);
                UnityEngine.Debug.LogException(ex);
                var error = new JObject
                {
                    ["code"] = "HOST_EXCEPTION",
                    ["message"] = ex.GetType().Name + ": host operation failed; see Unity Console",
                    ["retryable"] = false
                };
                if (recovery != null) error["data"] = new JObject { ["automatic_recovery"] = recovery };
                var response = Envelope(requestId, watch, consistency);
                response["ok"] = false;
                response["error"] = error;
                return response;
            }
        }
    }
}
