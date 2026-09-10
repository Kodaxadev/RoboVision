using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// An append-only record of what was attempted, written before it is attempted.
    /// </summary>
    /// <remarks>
    /// One post-operation entry would be enough only if crashes happened at
    /// convenient moments. A process can die after the editor changed and before
    /// anything was written, so there are two record types and the first is
    /// durable *before* any side effect: OP_INTENT says what is about to be done
    /// and to what state, OP_RESULT says what happened and links back to it.
    ///
    /// An intent with no terminal record is therefore evidence of an interrupted
    /// operation — the difference between "this may have half-happened" and
    /// re-running it because nothing remembered the first time.
    ///
    /// Deliberately thin. Not replay, not a retention policy, not a query
    /// language: the smallest durable spine that idempotency needs now and the
    /// artist loop's experiment trace will need later, built once so the two
    /// cannot come to disagree about what happened.
    /// </remarks>
    internal sealed class RoboVisionLedger
    {
        internal const string Intent = "OP_INTENT";
        internal const string Result = "OP_RESULT";
        internal const int LedgerSchema = 1;

        private readonly string _path;

        private RoboVisionLedger(string world, string path)
        {
            World = world;
            _path = path;
        }

        internal string World { get; }
        internal long Sequence { get; private set; }
        internal string Path => _path;

        /// <summary>
        /// One file per world incarnation.
        /// </summary>
        /// <remarks>
        /// Keyed by world rather than by process because that is the scope a
        /// logical operation belongs to: a bridge can be rebuilt while the world
        /// it was editing is verifiably still open, and those are still that
        /// world's operations. Under Library/, which Unity projects do not track
        /// and which survives a domain reload but is expected to be disposable.
        /// </remarks>
        internal static RoboVisionLedger Open(string world)
        {
            var root = System.Environment.GetEnvironmentVariable("ROBOVISION_LEDGER_DIR");
            if (String.IsNullOrEmpty(root))
                root = System.IO.Path.Combine(Directory.GetCurrentDirectory(), "Library", "RoboVision", "ledger");
            Directory.CreateDirectory(root);
            var split = world.IndexOf(':');
            var tail = split >= 0 ? world.Substring(split + 1) : world;
            return new RoboVisionLedger(world, System.IO.Path.Combine(root, tail + ".jsonl"));
        }

        /// <summary>Write one record and make it survive the process.</summary>
        /// <remarks>
        /// Flushed to disk before returning, because a record still in a buffer
        /// when the editor dies proves nothing, and what this file can prove
        /// about a crash is its entire value.
        /// </remarks>
        private JObject Append(JObject record)
        {
            Sequence++;
            record["schema"] = LedgerSchema;
            record["sequence"] = Sequence;
            record["world"] = World;
            var line = RoboVisionRecipe.Canonical(record);
            using (var stream = new FileStream(_path, FileMode.Append, FileAccess.Write, FileShare.Read))
            using (var writer = new StreamWriter(stream))
            {
                writer.Write(line);
                writer.Write('\n');
                writer.Flush();
                stream.Flush(true);
            }
            return record;
        }

        internal JObject WriteIntent(JObject fields)
        {
            fields["type"] = Intent;
            return Append(fields);
        }

        internal JObject WriteResult(long intentSequence, JObject fields)
        {
            fields["type"] = Result;
            fields["intent"] = intentSequence;
            return Append(fields);
        }

        internal IEnumerable<JObject> Read()
        {
            if (!File.Exists(_path)) yield break;
            foreach (var line in File.ReadLines(_path))
            {
                if (String.IsNullOrWhiteSpace(line)) continue;
                JObject record = null;
                try { record = JObject.Parse(line); }
                catch (JsonException)
                {
                    // A torn final line is what a crash mid-write looks like. It
                    // is not a reason to discard everything written before it.
                    continue;
                }
                if (record != null) yield return record;
            }
        }

        /// <summary>
        /// Rebuild what this world already knows, after the bridge was replaced.
        /// </summary>
        /// <remarks>
        /// Returns the terminal record for each idempotency key and the bare
        /// intent for any operation that never reached one, which is exactly the
        /// evidence an interrupted operation leaves behind.
        ///
        /// This is never evidence that the world is the same. World continuity is
        /// established by reading the editor, and only then may this be adopted —
        /// a ledger file proving its own relevance would be circular.
        /// </remarks>
        internal Dictionary<string, JObject> Resume()
        {
            var intents = new Dictionary<long, JObject>();
            var byKey = new Dictionary<string, JObject>(StringComparer.Ordinal);
            long highest = 0;
            foreach (var record in Read())
            {
                highest = Math.Max(highest, record.Value<long>("sequence"));
                var type = record.Value<string>("type");
                if (type == Intent)
                {
                    intents[record.Value<long>("sequence")] = record;
                    var key = record.Value<string>("idempotency_key");
                    if (!String.IsNullOrEmpty(key))
                        byKey[key] = new JObject { ["intent"] = record, ["result"] = null };
                }
                else if (type == Result)
                {
                    if (!intents.TryGetValue(record.Value<long>("intent"), out var origin)) continue;
                    var key = origin.Value<string>("idempotency_key");
                    if (!String.IsNullOrEmpty(key) && byKey.ContainsKey(key))
                        byKey[key]["result"] = record;
                }
            }
            Sequence = highest;
            return byKey;
        }
    }
}
