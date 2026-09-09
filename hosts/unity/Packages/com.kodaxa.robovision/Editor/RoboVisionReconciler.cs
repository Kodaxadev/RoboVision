using System;
using Newtonsoft.Json.Linq;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>One authoritative read of the scene, and what it hashes to.</summary>
    internal sealed class SceneRead
    {
        public JObject State;
        public string Fingerprint;
        /// <summary>Which world this is, independent of what is in it.</summary>
        public string DocumentSignature;
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
        /// <summary>A different world is loaded; nothing was diffed across the boundary.</summary>
        public bool DocumentChanged;
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
    /// as truth would build a journal on a sensor. Notifications mark dirty; this
    /// decides what actually changed.
    /// </remarks>
    internal sealed class RoboVisionReconciler
    {
        internal const string Agent = RoboVisionJournal.SourceAgent;
        internal const string Editor = RoboVisionJournal.SourceEditor;

        private readonly RoboVisionTransactions _transactions;
        private SceneRead _last;
        private bool _dirty = true;
        private string _documentSignature;

        public RoboVisionReconciler(RoboVisionTransactions transactions)
        {
            _transactions = transactions;
            // The host is a static singleton, so a domain or assembly reload
            // destroys and rebuilds it. Minting here is therefore exactly the
            // contract's rule: the bridge identity rotates when the loaded
            // RoboVision code is replaced, and a client can tell that the code
            // it was talking to is gone.
            Bridge = "rvbridge:" + Guid.NewGuid();
            DocumentIncarnation = "rvdoc:" + Guid.NewGuid();
            Journal = new RoboVisionJournal(DocumentIncarnation);
        }

        public string Bridge { get; }
        public string DocumentIncarnation { get; private set; }
        public RoboVisionJournal Journal { get; }
        public long Revision { get; private set; }
        public bool Dirty => _dirty;
        public SceneRead Current => _last;
        public string Fingerprint => _last?.Fingerprint;

        /// <summary>A notification arrived. Deliberately does not read the scene.</summary>
        public void MarkDirty() => _dirty = true;

        public Reconciliation Reconcile(string source, string request = null, SceneRead baseline = null)
        {
            var current = RoboVisionSceneTools.CaptureRead();

            // Which world is loaded is established by reading it, not by
            // subscribing to scene-open callbacks. A signature change means the
            // previous baseline describes something that is no longer open, so
            // nothing may be diffed across the boundary and the journal restarts.
            if (_documentSignature != null
                && !String.Equals(_documentSignature, current.DocumentSignature, StringComparison.Ordinal))
            {
                DocumentIncarnation = "rvdoc:" + Guid.NewGuid();
                Journal.Rebind(DocumentIncarnation, "load");
                Revision = 0;
                _last = current;
                _documentSignature = current.DocumentSignature;
                _dirty = false;
                return new Reconciliation
                {
                    Read = current,
                    Moved = false,
                    Revision = Revision,
                    Source = source,
                    Request = request,
                    DocumentChanged = true
                };
            }
            _documentSignature = current.DocumentSignature;

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

            JObject diff = null;
            if (moved)
            {
                diff = JournalChange(baseline, current, source, request);
            }

            return new Reconciliation
            {
                Read = current,
                Moved = moved,
                Revision = Revision,
                Diff = diff,
                Source = source,
                Request = request
            };
        }

        /// <summary>
        /// Attribute a change, or admit that it cannot be attributed.
        /// </summary>
        /// <remarks>
        /// With the previous read in hand the diff says exactly which objects
        /// moved and the journal stays certain. Without it, or when the
        /// fingerprint moved with nothing object-level to show for it, the host
        /// genuinely cannot say what changed, and saying so is the only honest
        /// option — guessing would make the journal worth less than no journal.
        /// </remarks>
        private JObject JournalChange(SceneRead baseline, SceneRead current, string source, string request)
        {
            if (baseline == null)
            {
                Journal.LoseCertainty("the scene changed with no prior read to attribute it against", Revision);
                return null;
            }
            var diff = RoboVisionSceneTools.DiffReads(baseline, current);
            if (Journal.RecordDiff(diff, Revision, source, request) == 0)
                Journal.LoseCertainty("the fingerprint moved with no attributable object change", Revision);
            return diff;
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

        /// <summary>Take the scene as the agent left it.</summary>
        public Reconciliation AcceptOwnMutation(SceneRead before, string request)
        {
            return Reconcile(Agent, request, before);
        }

        /// <summary>
        /// A failed mutation was undone back to its checkpoint.
        /// </summary>
        /// <remarks>
        /// Nothing net changed, so this is deliberately quiet: no revision, no
        /// events. Recovery that <em>failed</em> does not come here — that
        /// residue is real and belongs to the request that caused it.
        /// </remarks>
        public void AcceptRecovered(SceneRead before)
        {
            _last = before;
            _dirty = false;
        }
    }
}
