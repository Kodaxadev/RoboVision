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
    /// one document incarnation and resets with it.
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
        internal const string ResyncRequired = "RESYNC_REQUIRED";
        internal const string DocumentOpened = "DOCUMENT_OPENED";
        internal const string BridgeAttached = "BRIDGE_ATTACHED";

        internal const string SourceAgent = "agent";
        internal const string SourceEditor = "editor";
        internal const string SourceHost = "host";

        private readonly LinkedList<JObject> _events = new LinkedList<JObject>();
        private string _uncertainReason;

        internal RoboVisionJournal(string documentIncarnation)
        {
            DocumentIncarnation = documentIncarnation;
        }

        internal string DocumentIncarnation { get; private set; }
        internal long Epoch { get; private set; } = 1;
        internal bool Certain { get; private set; } = true;
        internal long Sequence { get; private set; }

        // ------------------------------------------------------------ cursors

        /// <summary>
        /// The client's position, bound to the world it was issued in.
        /// </summary>
        /// <remarks>
        /// A bare sequence number is not a position. Sequence restarts at a new
        /// certainty epoch and at a new document incarnation, so the same
        /// integer names different moments in different worlds — and both epoch
        /// counters start at 1, so an epoch alone does not separate them either.
        /// </remarks>
        internal string Cursor()
        {
            return CursorPrefix + ":" + CursorDocument + ":" + Epoch + ":" + Sequence;
        }

        // The incarnation is "rvdoc:<guid>"; the prefix is dropped so a cursor
        // stays four colon-separated fields and parses unambiguously.
        private string CursorDocument
        {
            get
            {
                var split = DocumentIncarnation.IndexOf(':');
                return split >= 0 ? DocumentIncarnation.Substring(split + 1) : DocumentIncarnation;
            }
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
        /// What this establishes: the cursor is well formed, names the document
        /// incarnation loaded now, names the current certainty epoch, and falls
        /// inside the retained range. What it does not establish is issuance —
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
            if (parts.Length != 4 || parts[0] != CursorPrefix)
                throw Refuse("INVALID_PARAMS",
                    "cursor must look like " + CursorPrefix + ":<document>:<epoch>:<sequence>", cursorToken);

            if (!Int64.TryParse(parts[2], out var epoch) || !Int64.TryParse(parts[3], out var sequence))
                throw Refuse("INVALID_PARAMS", "cursor epoch and sequence must be integers", cursorToken);
            if (epoch < 1 || sequence < 0)
                throw Refuse("INVALID_PARAMS", "cursor epoch and sequence are out of range", cursorToken);

            // Order matters. The document is checked first because epoch numbers
            // collide across documents — both start at 1 — so an epoch that
            // matches proves nothing until the world it belongs to is settled.
            if (!String.Equals(parts[1], CursorDocument, StringComparison.Ordinal))
                throw Refuse("STALE_DOCUMENT",
                    "that cursor names a document incarnation that is no longer loaded",
                    cursorToken, true,
                    new JObject { ["current_document_incarnation"] = DocumentIncarnation });
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
        /// Start again under a new document identity.
        /// </summary>
        /// <remarks>
        /// A scene swap and a rebuilt bridge both reach here: in each case the
        /// journal now describes a world the previous history does not belong
        /// to, and cursors from it must stop resolving.
        /// </remarks>
        internal void Rebind(string documentIncarnation, string reason)
        {
            DocumentIncarnation = documentIncarnation;
            Epoch = 1;
            Certain = true;
            _uncertainReason = null;
            Sequence = 0;
            _events.Clear();
            Append(reason == "load" ? DocumentOpened : BridgeAttached, 0, SourceHost, null, null,
                new JObject { ["document_incarnation"] = documentIncarnation, ["reason"] = reason });
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
                ["document_incarnation"] = DocumentIncarnation,
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
