using System;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// The five questions an Artist Loop asks before it acts, answered independently.
    /// </summary>
    /// <remarks>
    /// Separate from the report that assembles them because these are the
    /// judgements and that is the shape. Each one is decided from state the host
    /// already maintains and none of them perturbs anything: no test write, no
    /// render, no Undo, no synthetic edit. A readiness bought by performing the
    /// action it describes would be evidence about the past, not the present.
    ///
    /// Collapsing any two of these would lose exactly what the state model was
    /// built to preserve. Play mode blocks authoring while reads answer
    /// perfectly; an orphaned transaction blocks a new correction while
    /// observation is fine; a batch editor cannot capture and can still author.
    ///
    /// And the rule that decides each answer: a capability is <c>ready</c> only
    /// when the mechanisms its own contract requires are presently available. A
    /// correction is transactional by definition — observe, propose, observe
    /// again, then either commit or roll back with proof — so an editor that
    /// cannot prove a rollback has not got a degraded correction capability, it
    /// has one whose reject branch does not exist, and that is <c>blocked</c>.
    /// Unity can prove one headlessly, which is measured rather than assumed; the
    /// Blender host cannot in background, and says so in the same field.
    /// </remarks>
    internal sealed partial class RoboVisionHost
    {
        // The smallest vocabulary that distinguishes reality. Five values on
        // purpose: `unknown` and `not_applicable` are different claims, and
        // collapsing either into `degraded` reports a guess as a measurement.
        internal const string HealthReady = "ready";
        internal const string HealthDegraded = "degraded";
        internal const string HealthBlocked = "blocked";
        internal const string HealthUnknown = "unknown";
        internal const string HealthNotApplicable = "not_applicable";

        // What a transaction situation is, named once so the host, the client
        // library and the tests cannot drift into three vocabularies. The same
        // strings the Blender host publishes.
        internal const string SituationNone = "none";
        internal const string SituationOwned = "active_owned_by_this_connection";
        internal const string SituationForeign = "active_foreign";
        internal const string SituationOrphaned = "orphaned_adoption_required";
        internal const string SituationContaminated = "contaminated";
        internal const string SituationRecoveryUncertain = "recovery_uncertain";

        internal static int HealthSeverity(string status)
        {
            if (status == HealthBlocked) return 3;
            if (status == HealthUnknown) return 2;
            if (status == HealthDegraded) return 1;
            return 0;
        }

        private static JObject Entry(string status) => new JObject { ["status"] = status };

        /// <summary>
        /// Which of the transaction state machine's outcomes this connection is in.
        /// </summary>
        /// <remarks>
        /// The state machine is reused rather than reinterpreted: the input is the
        /// same non-secret <c>ActiveState()</c> view <c>system.hello</c> publishes.
        /// Nothing here ever claims the caller possesses a recovery credential —
        /// the host cannot know that, and saying it could is the one lie that
        /// would make recovery unsafe.
        /// </remarks>
        private static string TransactionSituation(JObject state, long clientId)
        {
            if (state == null) return SituationNone;
            var lifecycle = state.Value<string>("state");
            if (lifecycle == RoboVisionTransactions.StateOrphaned) return SituationOrphaned;
            if (lifecycle != RoboVisionTransactions.StateActive) return SituationRecoveryUncertain;
            if (state.Value<long>("owner_client") != clientId) return SituationForeign;
            if (state.Value<bool?>("contaminated") ?? false) return SituationContaminated;
            return SituationOwned;
        }

        /// <summary>Whether the implemented visual path is structurally usable right now.</summary>
        /// <remarks>
        /// Asked, never exercised. Only what the existing capture implementation
        /// can actually do is reported: it copies a SceneView camera, and a batch
        /// editor has no SceneView to copy. "Supported but this context cannot"
        /// and "not implemented" are answered separately — the first is here, the
        /// second in the perception subsystem, because an agent that could not
        /// tell them apart would either give up or keep retrying forever.
        /// </remarks>
        private static JObject VisualObservation()
        {
            var entry = Entry(HealthBlocked);
            if (Application.isBatchMode)
            {
                entry["reason"] = "batch_mode";
                entry["detail"] = "a batch-mode editor has no SceneView to copy a camera from";
                return entry;
            }
            foreach (SceneView candidate in SceneView.sceneViews)
            {
                if (candidate == null || candidate.camera == null) continue;
                var ready = Entry(HealthReady);
                ready["basis"] = "scene_view_camera_available";
                return ready;
            }
            entry["reason"] = "interactive_view_unavailable";
            entry["detail"] = "no open SceneView reports a camera";
            return entry;
        }

        /// <summary>Whether a rollback can be <em>proved</em>, not merely attempted.</summary>
        /// <remarks>
        /// Unity's Undo groups restore the begin fingerprint in a batch editor and
        /// across a domain reload of a saved scene, both measured rather than
        /// assumed, so the mechanism a correction's reject branch needs is present
        /// here. It is still asked as a question rather than hard-coded true: this
        /// is where a future limitation belongs, and the Blender host answers the
        /// same question with a `false` a client reads in the same field.
        ///
        /// The exception is a transaction whose checkpoint identities were lost to
        /// a reload — but that is an existing transaction's problem, reported by
        /// finish_or_recover, not a reason a fresh correction cannot start.
        /// </remarks>
        private static bool VerifiedRollbackAvailable(out string reason)
        {
            reason = null;
            return true;
        }

        /// <summary>Whether a new planned correction could be carried out at all.</summary>
        /// <remarks>
        /// Ready needs all of it: the authored editing state is appropriate, the
        /// world is settled, the coordinate contract is known, the scene is
        /// readable, no existing transaction makes a begin impossible — and a
        /// rollback can be proved. When one of those is absent the blocking state
        /// is named exactly rather than reported as a generic refusal.
        ///
        /// The last of them is why a missing verified rollback is `blocked` rather
        /// than `degraded`. A correction is transactional: observe, propose a
        /// candidate, observe again, then accept and commit <em>or</em> reject and
        /// roll back with proof. A begin would still succeed without it and the
        /// checkpoint would be real; what would be missing is the branch that
        /// makes proposing a candidate safe, and a capability with no reject
        /// branch is not a weaker version of this one.
        /// </remarks>
        private JObject BeginCorrection(string situation, SceneRead read)
        {
            var entry = Entry(HealthBlocked);
            if (!WorldSettled)
            {
                entry["reason"] = "world_not_settled";
                return entry;
            }
            if (EditorApplication.isPlayingOrWillChangePlaymode)
            {
                // A checkpoint is an authoring operation, and authoring is an Edit
                // Mode operation. The scene is still perfectly readable.
                entry["reason"] = "play_mode";
                entry["transitioning"] = !EditorApplication.isPlaying;
                return entry;
            }
            if (situation != SituationNone)
            {
                entry["reason"] = "transaction_" + situation;
                return entry;
            }
            if (!VerifiedRollbackAvailable(out var rollbackReason))
            {
                entry["reason"] = rollbackReason;
                entry["verified_rollback"] = false;
                entry["detail"] = "a correction must be able to be rejected and provably undone; "
                                  + "the begin itself would still succeed";
                return entry;
            }
            var ready = Entry(HealthReady);
            ready["basis"] = "scene_readable_and_no_transaction_open";
            ready["verified_rollback"] = true;
            ready["begin_fingerprint"] = read != null ? read.Fingerprint : null;
            return ready;
        }

        /// <summary>
        /// Whether the authored mutation path is presently eligible to be attempted.
        /// </summary>
        /// <remarks>
        /// Not a promise that any particular operation will succeed — only that
        /// nothing in the current state would refuse it before its own contract is
        /// consulted.
        /// </remarks>
        private JObject Mutate(string situation)
        {
            var entry = Entry(HealthBlocked);
            if (!WorldSettled)
            {
                entry["reason"] = "world_not_settled";
                return entry;
            }
            if (EditorApplication.isPlayingOrWillChangePlaymode)
            {
                entry["reason"] = "play_mode";
                entry["transitioning"] = !EditorApplication.isPlaying;
                entry["detail"] = "authoring mutations are Edit Mode operations; reads still answer";
                return entry;
            }
            if (situation == SituationForeign || situation == SituationOrphaned
                || situation == SituationRecoveryUncertain)
            {
                entry["reason"] = "transaction_" + situation;
                return entry;
            }
            var ready = Entry(HealthReady);
            ready["basis"] = situation == SituationOwned || situation == SituationContaminated
                ? "transaction_owned_by_this_connection"
                : "no_transaction_open";
            return ready;
        }

        /// <summary>
        /// The transaction situation, reported without granting any authority over it.
        /// </summary>
        /// <remarks>
        /// Deliberately never says the caller can recover. Only the client library
        /// knows whether it still holds the credential, and a host that implied it
        /// did would be handing out exactly the reassurance a stranger needs.
        /// </remarks>
        private static JObject FinishOrRecover(string situation, JObject state, JObject finished)
        {
            if (situation == SituationNone)
            {
                var none = Entry(HealthNotApplicable);
                none["situation"] = SituationNone;
                if (finished != null) none["recently_finished"] = finished;
                return none;
            }
            var uncertain = situation == SituationContaminated
                            || situation == SituationRecoveryUncertain;
            var entry = Entry(uncertain ? HealthDegraded : HealthReady);
            entry["situation"] = situation;
            entry["transaction"] = state;
            entry["verified_rollback_available"] =
                state != null && (state.Value<bool?>("verified_rollback_available") ?? false);
            entry["note"] = "the host cannot know whether any caller still holds the "
                            + "recovery credential";
            return entry;
        }
    }
}
