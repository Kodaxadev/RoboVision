using System;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// The readiness report, assembled from state that was just read.
    /// </summary>
    /// <remarks>
    /// Not a ping, and not a dashboard. A responding socket proves the socket
    /// responds; a single green light would destroy exactly the distinctions the
    /// state model exists to preserve. The judgements live in
    /// RoboVisionHealthReadiness.cs; this is the identity block an Artist Loop
    /// pins against, the thin subsystem facts an autonomous recovery would act
    /// on, and the shape both are delivered in.
    ///
    /// Deliberately the same vocabulary and the same shape as the Blender host's
    /// <c>health.py</c>. A cross-editor agent that had to learn two readiness
    /// dialects would end up writing the host-specific branch this project exists
    /// to remove. Where the two hosts genuinely differ — Unity has a play-mode
    /// domain and no multi-pass perception, Blender cannot prove world continuity
    /// across an add-on reload — the difference is reported in a shared field
    /// rather than hidden behind a shared word.
    /// </remarks>
    internal sealed partial class RoboVisionHost
    {
        /// <summary>
        /// Compact journal facts, and what an uncertain one actually costs.
        /// </summary>
        /// <remarks>
        /// An uncertain journal means "take an authoritative observation", not
        /// "the editor is unhealthy". Those are different sentences, and an agent
        /// that read the second when the first was true would stop working for no
        /// reason.
        /// </remarks>
        private JObject JournalHealth()
        {
            var state = Journal.State();
            var certain = state.Value<bool>("certain");
            var incremental = Entry(certain ? HealthReady : HealthDegraded);
            if (certain)
            {
                incremental["basis"] = "journal_certain";
            }
            else
            {
                incremental["reason"] = "journal_uncertain";
                incremental["detail"] = state["uncertain_reason"];
                incremental["remedy"] = "take an authoritative observation to restore knowledge";
            }
            return new JObject
            {
                ["status"] = certain ? HealthReady : HealthDegraded,
                ["certain"] = certain,
                ["epoch"] = state["epoch"],
                ["journal_incarnation"] = state["journal_incarnation"],
                ["cursor"] = state["cursor"],
                ["uncertain_reason"] = state["uncertain_reason"],
                ["incremental_changes"] = incremental
            };
        }

        /// <summary>
        /// Only the facts an autonomous recovery would act on.
        /// </summary>
        /// <remarks>
        /// Not a retention policy, an index, a search or a replay API — and no
        /// probe record is written to test the filesystem, because a mutation
        /// whose intent cannot be durably recorded already fails before its side
        /// effect.
        /// </remarks>
        private JObject OperationsHealth()
        {
            var failure = LedgerError;
            return new JObject
            {
                ["status"] = failure != null ? HealthDegraded : HealthReady,
                ["ledger_world"] = Ledger.World,
                ["ledger_matches_current_world"] =
                    String.Equals(Ledger.World, WorldIncarnation, StringComparison.Ordinal),
                // Records are adopted only where world continuity was proved by
                // reading the editor back, so this says what actually happened
                // rather than that a file was found.
                ["resumed"] = WorldSettled && WorldResumed,
                ["resumption"] = !WorldSettled ? HealthUnknown
                    : WorldResumed ? "verified" : "not_verified",
                ["interrupted_invocations"] = Invocations.InterruptedCount(),
                ["last_ledger_io_error"] = failure
            };
        }

        /// <summary>
        /// What this very call proves, and nothing beyond it.
        /// </summary>
        /// <remarks>
        /// No watchdog and no invented metric. A successful <c>system.health</c>
        /// is strong evidence that request delivery and editor-thread execution
        /// worked for this call, and that is the whole claim.
        /// </remarks>
        private JObject ExecutorHealth()
        {
            return new JObject
            {
                ["status"] = HealthReady,
                ["basis"] = "this_request_was_delivered_and_executed_on_the_editor_thread",
                ["transport"] = new JObject
                {
                    ["kind"] = "tcp-jsonl",
                    ["listening"] = Running,
                    ["port"] = Running ? (JToken)Port : JValue.CreateNull()
                },
                // Unity's transport drains a bounded budget per editor update and
                // keeps no depth metric. Manufacturing one to fill a field would
                // be the opposite of what this operation is for.
                ["queue_depth"] = HealthNotApplicable
            };
        }

        private JObject HealthIdentity(SceneRead read)
        {
            return new JObject
            {
                ["protocol"] = ProtocolVersion,
                ["host"] = new JObject
                {
                    ["name"] = "unity",
                    ["implementation"] = "robovision_unity",
                    ["version"] = HostVersion
                },
                ["editor"] = new JObject
                {
                    ["name"] = "Unity",
                    ["version"] = Application.unityVersion,
                    ["background"] = Application.isBatchMode,
                    ["playing"] = EditorApplication.isPlaying,
                    ["transitioning_play_mode"] =
                        EditorApplication.isPlayingOrWillChangePlaymode
                        && !EditorApplication.isPlaying
                },
                ["bridge"] = Bridge,
                ["world_incarnation"] = WorldIncarnation,
                // Unity can prove world continuity across a domain reload by
                // reading the editor back, so unlike Blender this is a real claim
                // rather than a field that has to say "not applicable".
                ["world_resumed"] = WorldSettled ? (JToken)WorldResumed : JValue.CreateNull(),
                ["world_resumption"] = !WorldSettled ? HealthUnknown
                    : WorldResumed ? "verified" : "not_verified",
                ["document"] = null,
                ["revision"] = Revision,
                ["fingerprint"] = read != null ? read.Fingerprint : null,
                ["state_domain"] = StateDomain,
                ["coordinate_contract"] = RoboVisionRecipe.CoordinateContract(),
                ["units"] = RoboVisionRecipe.Units(),
                ["journal_cursor"] = Journal.Cursor(),
                ["journal_epoch"] = Journal.Epoch,
                ["journal_certain"] = Journal.Certain,
                ["your_client"] = CurrentClientId
            };
        }

        /// <summary>The whole readiness report.</summary>
        internal JObject HealthReport()
        {
            var read = CurrentRead;
            var state = Transactions.ActiveState();
            var situation = TransactionSituation(state, CurrentClientId);

            // This response exists, so the scene was read authoritatively to
            // produce it. Not an inference: dispatch resyncs before an
            // authoritative handler runs, so a scene that could not be read would
            // have failed the call rather than reached this line.
            var semantic = Entry(HealthReady);
            semantic["basis"] = "authoritative_resync_succeeded";
            semantic["fingerprint"] = read != null ? read.Fingerprint : null;

            var visual = VisualObservation();

            // Verification follows observation until Deterministic Asset Truth
            // gives it something of its own to prove. Saying so beats inventing a
            // geometry validator here and calling the invention evidence.
            var semanticVerify = Entry(semantic.Value<string>("status"));
            semanticVerify["basis"] = "follows_semantic_observation";
            semanticVerify["validators"] = "not_implemented";

            var visualVerify = Entry(visual.Value<string>("status"));
            if (visual.Value<string>("status") == HealthReady)
                visualVerify["basis"] = "follows_visual_observation";
            else
                visualVerify["reason"] = visual["reason"];
            visualVerify["metrics"] = "not_implemented";

            var readyFor = new JObject
            {
                ["semantic_observation"] = semantic,
                ["visual_observation"] = visual,
                ["begin_correction"] = BeginCorrection(situation, read),
                ["mutate"] = Mutate(situation),
                ["semantic_verify"] = semanticVerify,
                ["visual_verify"] = visualVerify,
                ["finish_or_recover"] = FinishOrRecover(situation, state,
                    Transactions.MostRecentlyFinished())
            };

            var overall = HealthReady;
            foreach (var property in readyFor.Properties())
            {
                var status = ((JObject)property.Value).Value<string>("status");
                if (HealthSeverity(status) > HealthSeverity(overall)) overall = status;
            }

            return new JObject
            {
                ["status"] = overall,
                ["status_note"] = "a summary of ready_for; every entry below is independent and "
                                  + "no subsystem may be inferred from this value",
                ["identity"] = HealthIdentity(read),
                ["ready_for"] = readyFor,
                ["subsystems"] = new JObject
                {
                    ["journal"] = JournalHealth(),
                    ["transaction"] = new JObject
                    {
                        ["status"] = ((JObject)readyFor["finish_or_recover"]).Value<string>("status"),
                        ["situation"] = situation,
                        ["state"] = state
                    },
                    ["operations"] = OperationsHealth(),
                    ["executor"] = ExecutorHealth(),
                    ["perception"] = new JObject
                    {
                        ["status"] = visual.Value<string>("status"),
                        // Reported as it is, not as the Blender host's is. Unity
                        // implements a single SceneView camera capture and no
                        // aligned multi-pass bundle, and a field claiming
                        // otherwise would be exactly the evidence this operation
                        // must not manufacture.
                        ["multi_pass"] = "not_implemented",
                        ["methods"] = new JArray("viewport.capture"),
                        ["requires_interactive_view"] = true
                    }
                }
            };
        }
    }
}
