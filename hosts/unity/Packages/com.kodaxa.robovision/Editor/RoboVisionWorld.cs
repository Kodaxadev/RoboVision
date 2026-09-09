using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine.SceneManagement;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// Which editing context is open, as distinct from what is inside it.
    /// </summary>
    /// <remarks>
    /// The previous signature was the set of loaded scene handles, and it
    /// conflated the two questions. Opening a second scene additively changes
    /// what the main stage contains; it does not mean the editor entered a
    /// different editing context, and Unity's own Stage model says so — the
    /// main stage <em>is</em> the currently open normal scenes. Rotating the
    /// world there threw away the client's cursor, its revision and its
    /// snapshots for an ordinary edit, which was measured before it was
    /// changed: an empty additive scene open refused the client's cursor with
    /// a stale-world refusal and reset the revision from 2 to 0.
    ///
    /// A context has two parts and both are read from the editor rather than
    /// subscribed to:
    ///
    /// - the <b>stage</b>, which is the main stage or one specific Prefab
    ///   Stage. Changing it is entering a different editing context even when
    ///   nothing was unloaded: probing showed the main stage's scene stays
    ///   loaded behind an open Prefab Stage with its handle unchanged, so this
    ///   is not detectable from the loaded scene set at all
    /// - the <b>members</b>, the loaded scenes of that stage. Continuity needs
    ///   only that they overlap: adding or removing one is an edit inside the
    ///   same context, while a Single-mode replacement leaves nothing of the
    ///   previous universe loaded and is a different world
    /// </remarks>
    internal sealed class WorldContext
    {
        internal const string MainStage = "main";

        private WorldContext(string stage, IEnumerable<string> members)
        {
            Stage = stage;
            Members = new SortedSet<string>(members, StringComparer.Ordinal);
        }

        internal string Stage { get; }
        internal SortedSet<string> Members { get; }

        /// <summary>Read the context out of the editor.</summary>
        internal static WorldContext Read()
        {
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null && stage.scene.IsValid())
            {
                // The asset path names which prefab is being edited; the preview
                // scene handle separates one opening of it from the next, since
                // closing and reopening the same prefab builds a new preview
                // scene and every handle into the old one is void.
                return new WorldContext("prefab:" + stage.assetPath,
                    new[] { stage.scene.handle.ToString() });
            }

            var members = new List<string>();
            for (var i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (scene.isLoaded) members.Add(scene.handle.ToString());
            }
            return new WorldContext(MainStage, members);
        }

        /// <summary>
        /// Is this the same editing context the previous read was in?
        /// </summary>
        /// <remarks>
        /// Overlap, not equality. An additive open or close changes the member
        /// set while the context continues, and the journal should report what
        /// appeared or disappeared rather than declare the world replaced. A
        /// Single-mode load leaves nothing of the previous set loaded, so the
        /// overlap is empty and the previous history describes a universe that
        /// is gone.
        /// </remarks>
        internal bool Continues(WorldContext previous)
        {
            if (previous == null) return false;
            if (!String.Equals(Stage, previous.Stage, StringComparison.Ordinal)) return false;
            return Members.Overlaps(previous.Members);
        }

        /// <summary>Exactly the same context, member for member.</summary>
        /// <remarks>
        /// Used only where continuity has to be <em>proved</em> rather than
        /// inferred: after a domain reload nothing may have moved while
        /// RoboVision was not running, so overlap is not enough.
        /// </remarks>
        internal bool Matches(WorldContext other)
        {
            return other != null
                && String.Equals(Stage, other.Stage, StringComparison.Ordinal)
                && Members.SetEquals(other.Members);
        }

        internal JObject ToJson()
        {
            return new JObject
            {
                ["stage"] = Stage,
                ["members"] = new JArray(Members.Cast<object>().ToArray())
            };
        }

        internal static WorldContext FromJson(JObject json)
        {
            if (json == null) return null;
            var stage = json.Value<string>("stage");
            if (String.IsNullOrEmpty(stage)) return null;
            var members = (json["members"] as JArray) ?? new JArray();
            return new WorldContext(stage, members.Select(m => m.Value<string>()));
        }

        public override string ToString()
        {
            return Stage + "[" + String.Join("+", Members.ToArray()) + "]";
        }
    }

    /// <summary>What a rebuilt bridge is allowed to remember about the world it woke up in.</summary>
    /// <remarks>
    /// A domain or assembly reload destroys every static in the package but
    /// leaves the editor's scenes exactly where they were — measured, not
    /// assumed: the loaded scene handles were identical either side of a play
    /// mode round trip, and SessionState carried a string across it. So the
    /// bridge is genuinely new while the world may genuinely be the same one,
    /// and declaring the world replaced would be a lie that costs the client
    /// every durable reference it holds.
    ///
    /// SessionState is exactly scoped for this: it survives assembly reloads
    /// and is cleared when the editor process exits, which is the boundary at
    /// which a new world identity is correct anyway.
    ///
    /// Nothing here is trusted on its own. What was stashed is only adopted if
    /// the context read back out of the editor matches it exactly.
    /// </remarks>
    internal static class WorldMemory
    {
        private const string Key = "RoboVision.World";

        internal sealed class Remembered
        {
            public string Incarnation;
            public WorldContext Context;
            public long Revision;
            public string Fingerprint;
        }

        internal static void Remember(string incarnation, WorldContext context, long revision,
            string fingerprint)
        {
            if (incarnation == null || context == null) return;
            var payload = new JObject
            {
                ["incarnation"] = incarnation,
                ["context"] = context.ToJson(),
                ["revision"] = revision,
                ["fingerprint"] = fingerprint
            };
            SessionState.SetString(Key, payload.ToString(Newtonsoft.Json.Formatting.None));
        }

        internal static Remembered Recall()
        {
            var raw = SessionState.GetString(Key, null);
            if (String.IsNullOrEmpty(raw)) return null;
            try
            {
                var payload = JObject.Parse(raw);
                var context = WorldContext.FromJson(payload["context"] as JObject);
                var incarnation = payload.Value<string>("incarnation");
                if (context == null || String.IsNullOrEmpty(incarnation)) return null;
                return new Remembered
                {
                    Incarnation = incarnation,
                    Context = context,
                    Revision = payload.Value<long>("revision"),
                    Fingerprint = payload.Value<string>("fingerprint")
                };
            }
            catch (Exception)
            {
                // Unreadable stash is the same as no stash: mint a new world.
                return null;
            }
        }

        internal static void Forget()
        {
            SessionState.EraseString(Key);
        }
    }
}
