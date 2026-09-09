using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// A change journal that never lets a cheap answer masquerade as a proof.
    /// </summary>
    /// <remarks>
    /// The same contract as the Blender host's, deliberately: an agent driving
    /// both editors should not have to learn two histories. Events name stable
    /// ids and are attributed to the agent's own request or to the editor; when
    /// the host cannot say what changed it says so and stops claiming certainty;
    /// certainty is an epoch and losing it is sticky; the journal is scoped to
    /// one world incarnation and resets with it.
    ///
    /// It is a polling optimisation. Anything that has to be proved still
    /// compares fingerprints.
    /// </remarks>
    internal sealed class RoboVisionJournal
    {
        /// <summary>Events one epoch retains before the oldest are dropped.</summary>
        internal const int RetainedEvents = 512;
        internal const string CursorPrefix = "rvcursor";

        internal const string ObjectCreated = "OBJECT_CREATED";
        internal const string ObjectDeleted = "OBJECT_DELETED";
        internal const string ObjectChanged = "OBJECT_CHANGED";
        internal const string SceneLoaded = "SCENE_LOADED";
        internal const string SceneUnloaded = "SCENE_UNLOADED";
        internal const string IdentityUpgraded = "IDENTITY_UPGRADED";
        internal const string ResyncRequired = "RESYNC_REQUIRED";
        internal const string WorldOpened = "WORLD_OPENED";
        internal const string BridgeAttached = "BRIDGE_ATTACHED";

        internal const string SourceAgent = "agent";
        internal const string SourceEditor = "editor";
        internal const string SourceHost = "host";

        private readonly LinkedList<JObject> _events = new LinkedList<JObject>();
        private string _uncertainReason;

        internal RoboVisionJournal(string worldIncarnation)
        {
            WorldIncarnation = worldIncarnation;
            JournalIncarnation = MintIncarnation();
        }

        private static string MintIncarnation()
        {
            return "rvjournal:" + Guid.NewGuid();
        }

        /// <summary>Which editing context this history belongs to.</summary>
        internal string WorldIncarnation { get; private set; }

        /// <summary>
        /// Which run of the journal this is, inside that world.
        /// </summary>
        /// <remarks>
        /// A rebuilt bridge can wake up in the world it left — the editor's
        /// scenes are still open and the host can verify it — but its history
        /// is gone. Without a separate identity for the journal itself, a
        /// cursor from before the reload would name a world that is genuinely
        /// current and an epoch and sequence that both restarted at the same
        /// numbers, and would resolve against a history it has never seen.
        /// </remarks>
        internal string JournalIncarnation { get; private set; }
        internal long Epoch { get; private set; } = 1;
        internal bool Certain { get; private set; } = true;
        internal long Sequence { get; private set; }

        // ------------------------------------------------------------ cursors

        /// <summary>
        /// The client's position, bound to the world it was issued in.
        /// </summary>
        /// <remarks>
        /// A bare sequence number is not a position. Sequence restarts at a new
        /// certainty epoch, at a new journal and at a new world, so the same
        /// integer names different moments in different histories — and every
        /// one of those counters starts at 1, so no counter separates them
        /// either. Only the two incarnations do.
        /// </remarks>
        internal string Cursor()
        {
            return CursorPrefix + ":" + Tail(JournalIncarnation) + ":" + Tail(WorldIncarnation)
                + ":" + Epoch + ":" + Sequence;
        }

        // Incarnations are "<kind>:<guid>"; the kind is dropped so a cursor
        // stays five colon-separated fields and parses unambiguously.
        private static string Tail(string incarnation)
        {
            var split = incarnation.IndexOf(':');
            return split >= 0 ? incarnation.Substring(split + 1) : incarnation;
        }

        /// <summary>Every cursor refusal says where to resume from.</summary>
        private RoboVisionException Refuse(string code, string message, JToken cursor,
            bool retryable = false, JObject extra = null)
        {
            var data = extra ?? new JObject();
            data["cursor"] = cursor ?? JValue.CreateNull();
            data["current_cursor"] = Cursor();
            return new RoboVisionException(code, message, retryable, data);
        }

        /// <summary>
        /// Check a cursor against this journal.
        /// </summary>
        /// <remarks>
        /// What this establishes: the cursor is well formed, names the world
        /// that is open now and the journal that is running in it, names the
        /// current certainty epoch, and falls inside the retained range. What
        /// it does not establish is issuance —
        /// nothing is signed. That is deliberate for a read-only journal: a
        /// forged cursor reads events its caller could already read, while the
        /// checks that matter are about staleness, which a forger has no reason
        /// to fake.
        /// </remarks>
        private long ParseCursor(JToken cursorToken)
        {
            if (cursorToken == null || cursorToken.Type != JTokenType.String)
                throw Refuse("INVALID_PARAMS", "cursor must be a string", cursorToken);
            var cursor = cursorToken.Value<string>();
            if (String.IsNullOrEmpty(cursor))
                throw Refuse("INVALID_PARAMS", "cursor must be a string", cursorToken);

            var parts = cursor.Split(':');
            if (parts.Length != 5 || parts[0] != CursorPrefix)
                throw Refuse("INVALID_PARAMS",
                    "cursor must look like " + CursorPrefix + ":<journal>:<world>:<epoch>:<sequence>",
                    cursorToken);

            if (!Int64.TryParse(parts[3], out var epoch) || !Int64.TryParse(parts[4], out var sequence))
                throw Refuse("INVALID_PARAMS", "cursor epoch and sequence must be integers", cursorToken);
            if (epoch < 1 || sequence < 0)
                throw Refuse("INVALID_PARAMS", "cursor epoch and sequence are out of range", cursorToken);

            // Order matters, outermost scope first. Every counter in a cursor
            // restarts at 1 in a new world and in a new journal, so a matching
            // epoch proves nothing until both scopes are settled — and the two
            // scopes fail for different reasons a client must act on
            // differently, so they are separate codes rather than one.
            if (!String.Equals(parts[2], Tail(WorldIncarnation), StringComparison.Ordinal))
                throw Refuse("STALE_WORLD",
                    "that cursor names an editing context that is no longer open",
                    cursorToken, true,
                    new JObject { ["current_world_incarnation"] = WorldIncarnation });
            if (!String.Equals(parts[1], Tail(JournalIncarnation), StringComparison.Ordinal))
                throw Refuse("JOURNAL_REPLACED",
                    "the world is still open but this journal did not write that cursor; "
                    + "its history was rebuilt and is gone",
                    cursorToken, true,
                    new JObject { ["current_journal_incarnation"] = JournalIncarnation });
            if (epoch != Epoch)
                throw Refuse("EPOCH_SUPERSEDED",
                    "the journal has opened a new certainty epoch since that cursor was current",
                    cursorToken, true,
                    new JObject { ["your_epoch"] = epoch, ["current_epoch"] = Epoch });
            if (sequence > Sequence)
                throw Refuse("INVALID_PARAMS",
                    "that cursor is ahead of the journal; no such position exists yet", cursorToken);
            return sequence;
        }

        // ------------------------------------------------------------ writing

        private void Append(string type, long revision, string source,
            IEnumerable<string> ids = null, string request = null, JObject detail = null)
        {
            Sequence++;
            var evt = new JObject
            {
                ["sequence"] = Sequence,
                ["epoch"] = Epoch,
                ["revision"] = revision,
                ["type"] = type,
                ["ids"] = new JArray((ids ?? Enumerable.Empty<string>()).OrderBy(x => x, StringComparer.Ordinal)),
                ["source"] = source
            };
            if (request != null) evt["request"] = request;
            if (detail != null) evt["detail"] = detail;
            _events.AddLast(evt);
            while (_events.Count > RetainedEvents) _events.RemoveFirst();
        }

        /// <summary>
        /// Turn a reconciliation diff into attributed events.
        /// </summary>
        /// <remarks>
        /// The agent's own mutations and changes discovered at reconciliation
        /// both come through here, so attribution is the only thing that differs
        /// between them and the two paths cannot drift apart.
        /// </remarks>
        internal int RecordDiff(JObject diff, long revision, string source, string request = null)
        {
            var written = 0;
            foreach (var entry in (JArray)diff["created"])
            {
                Append(ObjectCreated, revision, source, new[] { entry.Value<string>("id") }, request);
                written++;
            }
            foreach (var entry in (JArray)diff["deleted"])
            {
                Append(ObjectDeleted, revision, source, new[] { entry.Value<string>("id") }, request);
                written++;
            }
            foreach (var entry in (JArray)diff["changed"])
            {
                Append(ObjectChanged, revision, source, new[] { entry.Value<string>("id") }, request);
                written++;
            }
            return written;
        }

        /// <summary>
        /// A scene joined or left the world without the world being replaced.
        /// </summary>
        /// <remarks>
        /// Opening a scene additively moves the fingerprint — the world now
        /// contains a container it did not before — while producing no
        /// object-level difference at all when that scene is empty. Without an
        /// event for it the host would see state move with nothing to attribute
        /// it to and would lose certainty over an ordinary, fully understood
        /// editor action.
        /// </remarks>
        internal int RecordSceneMembership(JObject diff, long revision, string source, string request)
        {
            var written = 0;
            foreach (var entry in (JArray)diff["scenes_loaded"])
            {
                Append(SceneLoaded, revision, source, null, request, (JObject)entry.DeepClone());
                written++;
            }
            foreach (var entry in (JArray)diff["scenes_unloaded"])
            {
                Append(SceneUnloaded, revision, source, null, request, (JObject)entry.DeepClone());
                written++;
            }
            return written;
        }

        /// <summary>
        /// The same object, addressable by a name it did not have before.
        /// </summary>
        /// <remarks>
        /// Saving an untitled scene is when a Unity object earns a durable
        /// GlobalObjectId, retiring the session handle it had until then. The
        /// object is not created and nothing is destroyed, and reporting it as
        /// a delete and a create — which is what the object diff alone says —
        /// tells the agent its world was demolished and rebuilt.
        ///
        /// The basis is carried with the event because this claim is only worth
        /// anything if the host can say how it knows.
        /// </remarks>
        internal void RecordIdentityUpgrade(string previousId, string id, string reason, string basis,
            long revision)
        {
            Append(IdentityUpgraded, revision, SourceHost, new[] { previousId, id }, null,
                new JObject
                {
                    ["previous_id"] = previousId,
                    ["id"] = id,
                    ["reason"] = reason,
                    ["basis"] = basis
                });
        }

        /// <summary>
        /// Record that the host cannot account for a change.
        /// </summary>
        /// <remarks>
        /// Sticky on purpose. A later notification that happens to look clean
        /// says nothing about the change that was missed, so only an
        /// authoritative snapshot may restore trust.
        /// </remarks>
        internal void LoseCertainty(string reason, long revision)
        {
            if (!Certain) return;
            Certain = false;
            _uncertainReason = reason;
            Append(ResyncRequired, revision, SourceHost, null, null, new JObject { ["reason"] = reason });
        }

        /// <summary>A full read of the scene. Opens a new epoch if certainty was lost.</summary>
        internal bool AuthoritativeSnapshot()
        {
            if (Certain) return false;
            Epoch++;
            Certain = true;
            _uncertainReason = null;
            Sequence = 0;
            _events.Clear();
            return true;
        }

        /// <summary>
        /// Start again: a different world, or the same world with no history.
        /// </summary>
        /// <remarks>
        /// A world replacement and a rebuilt bridge both reach here. The
        /// journal incarnation rotates in both cases because the history is
        /// gone in both cases, which is what a client's cursor depends on; the
        /// world incarnation is passed in because only the reconciler can say
        /// whether the editing context itself survived.
        /// </remarks>
        internal void Rebind(string worldIncarnation, string reason)
        {
            WorldIncarnation = worldIncarnation;
            JournalIncarnation = MintIncarnation();
            Epoch = 1;
            Certain = true;
            _uncertainReason = null;
            Sequence = 0;
            _events.Clear();
            Append(reason == "load" ? WorldOpened : BridgeAttached, 0, SourceHost, null, null,
                new JObject
                {
                    ["world_incarnation"] = worldIncarnation,
                    ["journal_incarnation"] = JournalIncarnation,
                    ["reason"] = reason
                });
        }

        // ------------------------------------------------------------ reading

        internal JObject State()
        {
            return new JObject
            {
                ["epoch"] = Epoch,
                ["certain"] = Certain,
                ["sequence"] = Sequence,
                ["cursor"] = Cursor(),
                ["world_incarnation"] = WorldIncarnation,
                ["journal_incarnation"] = JournalIncarnation,
                ["uncertain_reason"] = _uncertainReason,
                ["retained"] = _events.Count
            };
        }

        /// <summary>
        /// Where the journal is now, for a client that has no position yet.
        /// </summary>
        /// <remarks>
        /// Deliberately returns no events. A client that never had a position
        /// cannot tell a complete history from a truncated one, and an
        /// incomplete list read as complete is worse than no list.
        /// </remarks>
        internal JObject Bootstrap()
        {
            var result = State();
            result["bootstrap"] = true;
            result["events"] = new JArray();
            result["has_more"] = false;
            return result;
        }

        internal JObject ChangesSince(JToken cursor)
        {
            var after = ParseCursor(cursor);
            var oldest = _events.Count > 0 ? _events.First.Value.Value<long>("sequence") : Sequence;
            // `after` is exclusive, so asking for everything after the last event
            // a client already has is answerable even at the retention edge.
            if (after < oldest - 1)
                throw Refuse("SEQUENCE_TOO_OLD",
                    "the journal no longer retains that far back; take an authoritative snapshot",
                    cursor, true,
                    new JObject { ["oldest_retained"] = oldest, ["epoch"] = Epoch });

            var result = State();
            result["bootstrap"] = false;
            result["events"] = new JArray(_events.Where(e => e.Value<long>("sequence") > after)
                .Select(e => e.DeepClone()));
            result["has_more"] = false;
            return result;
        }
    }
}
