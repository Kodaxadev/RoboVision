using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// What computation was requested, hashed so two of them can be compared.
    /// </summary>
    /// <remarks>
    /// Five identifiers answer five different questions and none is derived from
    /// another: the request id names a wire exchange, the idempotency key an
    /// intended side effect, the attempt a delivery of it, the recipe hash the
    /// computation asked for, and the seeds the stochastic branch taken. Two
    /// deliveries of one invocation share a recipe *and* a key; two deliberate
    /// executions of one computation share a recipe and must have different
    /// keys, or an agent running the same recipe in two candidate branches
    /// watches the second vanish as a network duplicate.
    ///
    /// The same contract as the Blender host's, and deliberately not the same
    /// frame: see CoordinateContract.
    /// </remarks>
    internal static class RoboVisionRecipe
    {
        internal const int RecipeSchema = 2;

        /// <summary>
        /// The convention Unity authors in, which is not Blender's.
        /// </summary>
        /// <remarks>
        /// Y up, Z forward, left-handed, one unit to one metre. Blender is Z up,
        /// -Y forward, right-handed. Pretending the two hosts share a frame would
        /// make every cross-editor claim quietly wrong, so each publishes its own
        /// and a future explicit conversion recipe connects them — with its own
        /// proof, rather than an assumption buried in an exporter.
        /// </remarks>
        internal const string CanonicalFrame = "rvframe:unity_y_up_left_handed_metres";

        // Never part of a recipe: these say which delivery this is, not what was
        // asked for. Hashing them would make every retry a different computation.
        private static readonly string[] TransportKeys =
        {
            "idempotency_key", "attempt", "request_id", "id", "rv", "bridge",
            "expected_world", "expected_coordinate_contract", "contract"
        };

        /// <summary>The one serialization a hash is taken over.</summary>
        /// <remarks>
        /// Boring on purpose: sorted keys, no whitespace. Anything that changes
        /// it changes every recipe hash ever recorded, which is why it is one
        /// function with a schema version rather than an inline serializer call.
        /// </remarks>
        internal static string Canonical(JToken value)
        {
            return Sort(value).ToString(Formatting.None);
        }

        private static JToken Sort(JToken value)
        {
            if (value is JObject source)
            {
                var sorted = new JObject();
                foreach (var property in source.Properties()
                             .OrderBy(p => p.Name, StringComparer.Ordinal))
                {
                    sorted[property.Name] = Sort(property.Value);
                }
                return sorted;
            }
            if (value is JArray array) return new JArray(array.Select(Sort));
            return value ?? JValue.CreateNull();
        }

        /// <summary>
        /// What was running, in enough detail to tell two executions apart.
        /// </summary>
        /// <remarks>
        /// A host version alone is only implementation identity if it is
        /// guaranteed to change whenever execution semantics change, and nothing
        /// guarantees that. The editor is part of the environment too: the same
        /// RoboVision build against two Unity versions is not the same
        /// computation. None of this proves identical output — only an output
        /// hash would — so it should be read as "the same environment asked for
        /// the same thing" and no more.
        /// </remarks>
        internal static JObject Environment()
        {
            return new JObject
            {
                ["robovision"] = RoboVisionHost.HostVersion,
                ["recipe_schema"] = RecipeSchema,
                ["editor"] = "unity",
                ["editor_version"] = Application.unityVersion,
                ["frame"] = CanonicalFrame,
                ["canonical_unit"] = "1 unity unit == 1 robovision metre",
                ["handedness"] = "left",
                ["up"] = "+Y",
                ["forward"] = "+Z"
            };
        }

        /// <summary>
        /// An identity for the axis and unit convention a world is edited in.
        /// </summary>
        /// <remarks>
        /// Pinnable by a request the way a world incarnation is, and derived from
        /// the convention rather than declared as a bare constant so that a
        /// change to any part of it changes the identity.
        ///
        /// Unlike Blender, Unity has no project-level unit scale: there is no
        /// equivalent of `scale_length` that a human can move between an agent's
        /// observation and its mutation, so within one project and one editor
        /// this value is stable. The pin still does real work — it is what stops
        /// a request planned against Blender's Z-up right-handed metre frame from
        /// executing here, which is exactly the mistake a cross-editor agent is
        /// positioned to make. Per-asset import scale is an asset property and
        /// belongs to lineage work, not to the world's contract.
        /// </remarks>
        internal static string CoordinateContract()
        {
            var body = Canonical(new JObject
            {
                ["frame"] = CanonicalFrame,
                ["handedness"] = "left",
                ["up"] = "+Y",
                ["forward"] = "+Z",
                ["canonical_unit"] = "1 unity unit == 1 robovision metre"
            });
            using (var sha = SHA256.Create())
            {
                var digest = sha.ComputeHash(Encoding.UTF8.GetBytes(body));
                return "rvcoord:" + BitConverter.ToString(digest).Replace("-", "").ToLowerInvariant()
                    .Substring(0, 16);
            }
        }

        /// <summary>What one unit means here, reported rather than assumed.</summary>
        internal static JObject Units()
        {
            return new JObject
            {
                ["canonical_unit"] = "1 unity unit == 1 robovision metre",
                ["project_unit_scale"] = 1.0,
                ["unit_scale_is_canonical"] = true,
                ["note"] = "Unity exposes no project-level unit scale; per-asset import scale is "
                           + "an asset property, not part of this world's coordinate contract."
            };
        }

        /// <summary>Hash the semantic inputs of one execution.</summary>
        internal static string Hash(
            string method,
            JObject parameters,
            string toolVersion,
            string determinism,
            JObject seeds = null,
            JObject targets = null,
            JObject inputs = null,
            string modelVersion = null)
        {
            var hashable = new JObject();
            foreach (var property in (parameters ?? new JObject()).Properties())
            {
                if (Array.IndexOf(TransportKeys, property.Name) >= 0) continue;
                hashable[property.Name] = property.Value.DeepClone();
            }
            var body = new JObject
            {
                ["schema"] = RecipeSchema,
                ["method"] = method,
                ["tool_version"] = toolVersion,
                ["determinism"] = determinism,
                ["environment"] = Environment(),
                ["params"] = hashable,
                ["seeds"] = seeds ?? new JObject(),
                ["targets"] = targets ?? new JObject(),
                ["inputs"] = inputs ?? new JObject()
            };
            if (modelVersion != null) body["model_version"] = modelVersion;
            using (var sha = SHA256.Create())
            {
                var digest = sha.ComputeHash(Encoding.UTF8.GetBytes(Canonical(body)));
                return "rvrecipe:" + BitConverter.ToString(digest).Replace("-", "").ToLowerInvariant();
            }
        }
    }
}
