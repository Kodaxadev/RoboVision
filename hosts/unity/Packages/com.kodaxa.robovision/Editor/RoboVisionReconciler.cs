using System;
using Newtonsoft.Json.Linq;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>One authoritative read of the scene, and what it hashes to.</summary>
    internal sealed class SceneRead
    {
        public JObject State;
        public string Fingerprint;
    }

    /// <summary>What one reconciliation established.</summary>
    internal sealed class Reconciliation
    {
        public SceneRead Read;
        public bool Moved;
        public long Revision;
        /// <summary>Object-level created/deleted/changed. Null when nothing moved.</summary>
        public JObject Diff;
        public string Source;
        public string Request;
    }

    /// <summary>
    /// The one place that answers "what is the scene now?".
    /// </summary>
    /// <remarks>
    /// The dirty refresh, the pre-mutation resync and accepting the agent's own
    /// mutation were three near-identical bodies here, exactly as they were in
    /// the Blender host, and that is where a missed notification hides: a
    /// fingerprint change discovered by one of them advanced the revision and
    /// replaced the baseline while the other paths knew nothing about it.
    ///
    /// Unity's notifications make this more tempting to get wrong, not less.
    /// ObjectChangeEvents publishes undoable changes to loaded objects once per
    /// frame, so it is not comprehensive, and its broad events — ChangeScene
    /// among them — may carry no object information at all. Treating any of that
    /// as truth would build a journal on a sensor. Notifications mark dirty and
    /// may offer attribution hints; this decides what actually changed.
    /// </remarks>
    internal sealed class RoboVisionReconciler
    {
        internal const string Agent = "agent";
        internal const string Editor = "editor";

        private readonly RoboVisionTransactions _transactions;
        private SceneRead _last;
        private bool _dirty = true;

        public RoboVisionReconciler(RoboVisionTransactions transactions)
        {
            _transactions = transactions;
        }

        public long Revision { get; private set; }
        public bool Dirty => _dirty;
        public SceneRead Current => _last;
        public string Fingerprint => _last?.Fingerprint;

        /// <summary>A notification arrived. Deliberately does not read the scene.</summary>
        public void MarkDirty() => _dirty = true;

        public Reconciliation Reconcile(string source, string request = null, SceneRead baseline = null)
        {
            var current = RoboVisionSceneTools.CaptureRead();
            baseline = baseline ?? _last;
            var known = baseline != null ? baseline.Fingerprint : null;
            var moved = known != null && !String.Equals(known, current.Fingerprint, StringComparison.Ordinal);

            if (moved)
            {
                // The agent's own mutation is not an out-of-band edit, so it must
                // not contaminate a transaction it is part of.
                if (source == Editor && _transactions.Active)
                    _transactions.MarkExternalChange(current.Fingerprint);
                // Scene revision names authoritative scene state, not commands
                // run, so it advances only when that state actually moved.
                Revision++;
            }

            _last = current;
            _dirty = false;

            return new Reconciliation
            {
                Read = current,
                Moved = moved,
                Revision = Revision,
                Diff = moved && baseline != null ? RoboVisionSceneTools.DiffReads(baseline, current) : null,
                Source = source,
                Request = request
            };
        }

        /// <summary>Service a notification if one arrived, and nothing more.</summary>
        public void RefreshDirtyState()
        {
            if (_dirty) Reconcile(Editor);
        }

        /// <summary>
        /// Re-read before a mutation is allowed to run.
        /// </summary>
        /// <remarks>
        /// hierarchyChanged and postprocessModifications are notifications, not
        /// guarantees, and the transport dispatches up to eight requests per
        /// editor update, so a burst runs with no tick in between and the dirty
        /// flag can be clear while the scene has moved. Trusting it would let a
        /// stale if_revision pass and an out-of-band edit go unnoticed inside a
        /// transaction.
        /// </remarks>
        public SceneRead Resync() => Reconcile(Editor).Read;

        /// <summary>Take the scene as the agent left it. True if state actually moved.</summary>
        public Reconciliation AcceptOwnMutation(SceneRead before, string request)
        {
            return Reconcile(Agent, request, before);
        }

        /// <summary>
        /// A failed mutation was undone back to its checkpoint.
        /// </summary>
        /// <remarks>
        /// Nothing net changed, so this is deliberately quiet: no revision, no
        /// diff. Recovery that <em>failed</em> does not come here — that residue
        /// is real and belongs to the request that caused it.
        /// </remarks>
        public void AcceptRecovered(SceneRead before)
        {
            _last = before;
            _dirty = false;
        }

        /// <summary>Forget everything scoped to a world that is no longer loaded.</summary>
        public void Reset()
        {
            _last = null;
            _dirty = true;
            Revision = 0;
        }
    }
}
