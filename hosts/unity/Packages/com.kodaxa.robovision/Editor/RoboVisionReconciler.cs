using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>One authoritative read of the scene, and what it hashes to.</summary>
    internal sealed class SceneRead
    {
        public JObject State;
        public string Fingerprint;
        /// <summary>Which editing context this was read in, independent of its contents.</summary>
        public WorldContext World;
    }

    /// <summary>What one reconciliation established.</summary>
    internal sealed class Reconciliation
    {
        public SceneRead Read;
        /// <summary>Authored state moved. Control-plane changes alone do not set this.</summary>
        public bool Moved;
        public long Revision;
        /// <summary>Object-level created/deleted/changed. Null when nothing moved.</summary>
        public JObject Diff;
        public string Source;
        public string Request;
        /// <summary>A different editing context is open; nothing was diffed across the boundary.</summary>
        public bool WorldChanged;
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
        /// <summary>The most recent read, whatever mode the editor was in.</summary>
        private SceneRead _last;
        /// <summary>The most recent read of <em>authored</em> state.</summary>
        /// <remarks>
        /// Separate from the last read because play mode is not authored state.
        /// Entering play mode instantiates the open scenes and gives their
        /// objects runtime addresses, and leaving it throws all of that away, so
        /// a play mode read that became the baseline would report a scene full
        /// of changes on the way in and another on the way out — neither of
        /// which anyone authored. Measured, not theorised: a play mode round
        /// trip advanced the scene revision and cost the journal its certainty
        /// over a scene nobody touched.
        /// </remarks>
        private SceneRead _baseline;
        private bool _dirty = true;
        private WorldContext _world;
        /// <summary>Whether the world this bridge woke up in is one it can verify.</summary>
        private bool _worldResumed;

        public RoboVisionReconciler(RoboVisionTransactions transactions)
        {
            _transactions = transactions;
            // The host is a static singleton, so a domain or assembly reload
            // destroys and rebuilds it. Minting here is therefore exactly the
            // contract's rule: the bridge identity rotates when the loaded
            // RoboVision code is replaced, and a client can tell that the code
            // it was talking to is gone.
            Bridge = "rvbridge:" + Guid.NewGuid();
            // Provisional only. Which world this bridge woke up in cannot be
            // decided without reading the editor, and reading the editor from a
            // constructor that may run before the scenes are open would be
            // guessing. The first reconciliation settles it.
            WorldIncarnation = MintWorld();
            Journal = new RoboVisionJournal(WorldIncarnation);
        }

        public string Bridge { get; }
        public string WorldIncarnation { get; private set; }
        /// <summary>The world has been read out of the editor at least once.</summary>
        public bool WorldSettled => _world != null;
        /// <summary>That world is one this bridge could verify it woke up in.</summary>
        public bool WorldResumed => _worldResumed;
        public RoboVisionJournal Journal { get; }
        public long Revision { get; private set; }
        public bool Dirty => _dirty;
        public SceneRead Current => _last;
        public string Fingerprint => _last?.Fingerprint;

        private static string MintWorld()
        {
            return "rvworld:" + Guid.NewGuid();
        }

        /// <summary>A notification arrived. Deliberately does not read the scene.</summary>
        public void MarkDirty() => _dirty = true;

        public Reconciliation Reconcile(string source, string request = null, SceneRead baseline = null)
        {
            var current = RoboVisionSceneRead.CaptureRead();
            var playing = EditorApplication.isPlayingOrWillChangePlaymode;
            var worldChanged = false;

            // Which editing context is open is established by reading it, not by
            // subscribing to scene-open or stage callbacks: identity established
            // by having been told has the same weakness as change detection
            // established by having been told.
            if (_world == null) worldChanged = EstablishWorld(current, playing);
            else if (!playing)
            {
                if (!current.World.Continues(_world)) return ReplaceWorld(current, source, request);
                _world = current.World;
            }

            _last = current;
            _dirty = false;

            if (playing)
            {
                // Answer with what is loaded — an agent may legitimately want to
                // look at a running scene — and touch nothing that describes the
                // authored one. Play mode can also load and unload scenes of its
                // own, which is why the world is not re-examined here either.
                return new Reconciliation
                {
                    Read = current,
                    Moved = false,
                    Revision = Revision,
                    Source = source,
                    Request = request,
                    WorldChanged = worldChanged
                };
            }

            // The world is settled and what is loaded is the authored scene,
            // so an interrupted transaction can be judged. Idempotent, because
            // anything that consults a transaction judges it first: tying this to
            // one particular reconciliation left it unjudged whenever the world
            // happened to be established during a play mode transition.
            _transactions.EnsureJudged();

            baseline = baseline ?? _baseline;
            var known = baseline != null ? baseline.Fingerprint : null;
            var fingerprintMoved = known != null
                && !String.Equals(known, current.Fingerprint, StringComparison.Ordinal);

            JObject diff = null;
            List<JObject> upgrades = null;
            var authored = false;
            if (fingerprintMoved)
            {
                if (baseline.State != null)
                {
                    diff = RoboVisionSceneRead.DiffReads(baseline, current);
                    upgrades = ExtractIdentityUpgrades(diff);
                    authored = HasAuthoredChange(diff);
                }
                else
                {
                    // All that survived of the previous state is its hash — the
                    // bridge was rebuilt and only the fingerprint crossed with
                    // it. Something moved and nothing can say what, so the
                    // conservative reading is the honest one: treat it as an
                    // authored change and admit it cannot be attributed.
                    authored = true;
                }
            }

            // Contamination is judged on the fingerprint, not on whether the
            // change was authored. A transaction checkpointed a scene that no
            // longer hashes the same, and that is true however the difference
            // arose; softening it here would weaken a guarantee this pass has no
            // business touching.
            if (fingerprintMoved && source == Editor && _transactions.Active)
                _transactions.MarkExternalChange(current.Fingerprint);

            // The scene revision names authored state, not commands run and not
            // the host's own bookkeeping. An object that only became addressable
            // by a different name did not move, so the revision does not either.
            if (authored) Revision++;

            _baseline = current;

            if (fingerprintMoved) Record(baseline, diff, upgrades, source, request);
            Remember();

            return new Reconciliation
            {
                Read = current,
                Moved = authored,
                Revision = Revision,
                Diff = diff,
                Source = source,
                Request = request,
                WorldChanged = worldChanged
            };
        }

        // ------------------------------------------------------------ the world

        /// <summary>
        /// Decide, once per bridge, which world this one woke up in.
        /// </summary>
        /// <remarks>
        /// A rebuilt bridge is not a new world. A domain reload leaves the
        /// editor's scenes exactly where they were — measured, not assumed —
        /// so declaring the world replaced would cost a client every durable
        /// reference it holds for no reason but our own restart.
        ///
        /// What was stashed is never trusted on its own. The context is read
        /// back out of the editor and has to match exactly: overlap is enough
        /// for a change made while the host was watching, but nothing was
        /// watching across a reload.
        ///
        /// The fingerprint that crossed with it becomes the baseline, with no
        /// state behind it. That is deliberate: the comparison it enables is the
        /// only evidence there is about what happened while the bridge was gone,
        /// and dropping it — which an earlier version did whenever the editor
        /// was still leaving play mode at this moment — silently absorbed the
        /// difference into a fresh baseline.
        /// </remarks>
        /// <returns>True when this is a world the bridge has never seen.</returns>
        private bool EstablishWorld(SceneRead current, bool playing)
        {
            var remembered = WorldMemory.Recall();
            var resumed = remembered != null && current.World.Matches(remembered.Context);
            WorldIncarnation = resumed ? remembered.Incarnation : MintWorld();
            Revision = resumed ? remembered.Revision : 0;
            // The journal is new either way: its history did not survive, and a
            // cursor into it must stop resolving even where the world did.
            Journal.Rebind(WorldIncarnation, resumed ? "bridge_attached" : "load");
            _world = current.World;

            if (resumed && remembered.Fingerprint != null)
            {
                _baseline = new SceneRead
                {
                    Fingerprint = remembered.Fingerprint,
                    State = null,
                    World = current.World
                };
            }
            else if (!playing)
            {
                // A world with no remembered state starts here, with nothing
                // behind it to have moved.
                _baseline = current;
            }

            _worldResumed = resumed;
            if (!playing) Remember();
            return !resumed;
        }

        /// <summary>A different editing context is open; the previous history does not describe it.</summary>
        private Reconciliation ReplaceWorld(SceneRead current, string source, string request)
        {
            // A transaction describes the world it was opened in. Measured
            // before this line existed: after a Single-mode load the previous
            // transaction stayed active, accepted another mutation and
            // committed — collapsing undo groups in a universe its checkpoint
            // never described. It ends here, with a reason, and a later commit
            // or rollback of it is told so.
            _transactions.AbandonForWorld("world_replaced");
            WorldIncarnation = MintWorld();
            // Nothing was resumed into this one: it is a world this bridge is
            // seeing for the first time. Leaving the flag set would let the
            // durable operation records of a world that is gone be adopted into
            // the one that replaced it.
            _worldResumed = false;
            Journal.Rebind(WorldIncarnation, "load");
            Revision = 0;
            _world = current.World;
            _last = current;
            _baseline = current;
            _dirty = false;
            Remember();
            return new Reconciliation
            {
                Read = current,
                Moved = false,
                Revision = Revision,
                Source = source,
                Request = request,
                WorldChanged = true
            };
        }

        private void Remember()
        {
            WorldMemory.Remember(WorldIncarnation, _world, Revision,
                _baseline != null ? _baseline.Fingerprint : null);
        }

        // ------------------------------------------------------- what changed

        private static bool HasAuthoredChange(JObject diff)
        {
            return ((JArray)diff["created"]).Count > 0
                || ((JArray)diff["deleted"]).Count > 0
                || ((JArray)diff["changed"]).Count > 0
                || ((JArray)diff["scenes_loaded"]).Count > 0
                || ((JArray)diff["scenes_unloaded"]).Count > 0;
        }

        /// <summary>
        /// Tell an object that was re-addressed from one that was destroyed.
        /// </summary>
        /// <remarks>
        /// Saving an untitled scene is when a Unity object earns a durable
        /// GlobalObjectId and retires the session handle it had until then. The
        /// object diff can only say that one id vanished and another appeared,
        /// which reads as demolition; the host can do better, because the
        /// retired handle still resolves to the live object and that object can
        /// be asked what it is called now. Where that proof exists the pair is
        /// lifted out of the diff and reported as the upgrade it is. Where it
        /// does not, nothing is claimed and the delete and create stand.
        /// </remarks>
        private static List<JObject> ExtractIdentityUpgrades(JObject diff)
        {
            var created = (JArray)diff["created"];
            var deleted = (JArray)diff["deleted"];
            var upgrades = new List<JObject>();
            if (created.Count == 0 || deleted.Count == 0) return upgrades;

            var appeared = created.ToDictionary(e => e.Value<string>("id"), e => e);
            foreach (var entry in deleted.ToList())
            {
                var previousId = entry.Value<string>("id");
                if (previousId == null
                    || !previousId.StartsWith(RoboVisionSessionHandles.Prefix, StringComparison.Ordinal))
                    continue;
                var live = RoboVisionSessionHandles.Resolve(previousId);
                if (live == null) continue;
                var now = RoboVisionSceneRead.IdFor(live, out var persistent);
                if (!persistent || !appeared.ContainsKey(now)) continue;

                upgrades.Add(new JObject
                {
                    ["previous_id"] = previousId,
                    ["id"] = now,
                    ["reason"] = "the object was saved and earned a durable identity",
                    ["basis"] = "the retired session handle still resolves to the same live object"
                });
                deleted.Remove(entry);
                created.Remove(appeared[now]);
                appeared.Remove(now);
            }
            return upgrades;
        }

        /// <summary>
        /// Attribute a change, or admit that it cannot be attributed.
        /// </summary>
        /// <remarks>
        /// With the previous read in hand the diff says exactly which objects
        /// moved and the journal stays certain. Without it, or when the
        /// fingerprint moved with nothing to show for it, the host genuinely
        /// cannot say what changed, and saying so is the only honest option —
        /// guessing would make the journal worth less than no journal.
        /// </remarks>
        private void Record(SceneRead baseline, JObject diff, List<JObject> upgrades,
            string source, string request)
        {
            if (baseline == null || diff == null)
            {
                Journal.LoseCertainty(
                    baseline != null && baseline.State == null
                        ? "the bridge was rebuilt and the world no longer matches the state it left"
                        : "the scene changed with no prior read to attribute it against",
                    Revision);
                return;
            }

            var written = 0;
            foreach (var upgrade in upgrades)
            {
                Journal.RecordIdentityUpgrade(upgrade.Value<string>("previous_id"),
                    upgrade.Value<string>("id"), upgrade.Value<string>("reason"),
                    upgrade.Value<string>("basis"), Revision);
                written++;
            }
            written += Journal.RecordSceneMembership(diff, Revision, source, request);
            written += Journal.RecordDiff(diff, Revision, source, request);
            if (written == 0)
                Journal.LoseCertainty("the fingerprint moved with no attributable change", Revision);
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
            _baseline = before;
            _dirty = false;
        }
    }
}
