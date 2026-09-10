using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace Kodaxa.RoboVision.Editor
{
    /// <summary>
    /// What the host can do, and what each of those things costs to repeat.
    /// </summary>
    /// <remarks>
    /// Split out of the host because it answers a different question. The host
    /// owns a running bridge; this owns the declarations dispatch reads before
    /// it runs anything — how fresh an answer has to be, whether the authored
    /// scene moves, whether a second delivery is free, what randomness has to be
    /// recorded, and what a duplicate is answered with.
    /// </remarks>
    internal sealed partial class RoboVisionHost
    {
        internal sealed class ToolSpec
        {
            public string Name;
            public Func<JObject, JToken> Handler;
            public bool Mutating;
            public bool Evidence;
            public bool RequiresUi;
            /// <summary>What this call's answer is worth: see RoboVisionHost.Reads*.</summary>
            public string Reads;
            public string Stability;
            public bool TransactionControl;
            public string Summary;
            public string[] Tags;
            public JObject ParamsSchema;
            /// <summary>Named randomness channels this tool consumes.</summary>
            /// <remarks>
            /// Named rather than one seed because the useful precedent separates
            /// them: a text-to-reference-to-3D pipeline wants its image, geometry
            /// and texture branches independently reproducible, and one seed for
            /// all three makes "same shape, different texture" impossible to ask
            /// for.
            /// </remarks>
            public string[] Seeds;
            public string Determinism;
            /// <summary>Repeating this is not free, whether or not the scene moves.</summary>
            /// <remarks>
            /// Mutations are the obvious case; transaction control, and later
            /// artifact export and external generation, are the ones that would
            /// be missed by asking only "does the revision advance?".
            /// </remarks>
            public bool SideEffecting;
            public string DuplicatePolicy;
            /// <summary>This call's meaning depends on the state it was planned against.</summary>
            /// <remarks>
            /// Separate from Mutating and from SideEffecting on purpose: a future
            /// external generation request is side-effecting and expensive to
            /// repeat without being bound to the current authored revision at
            /// all, while transaction.begin binds a checkpoint to a scene it never
            /// changes.
            /// </remarks>
            public bool ObservationBound;

            public JObject Describe(bool includeSchema = false)
            {
                var result = new JObject
                {
                    ["name"] = Name,
                    ["mutating"] = Mutating,
                    ["evidence"] = Evidence,
                    ["requires_ui"] = RequiresUi,
                    ["reads"] = Reads,
                    ["determinism"] = Determinism,
                    ["seeds"] = new JArray(Seeds ?? Array.Empty<string>()),
                    ["side_effecting"] = SideEffecting,
                    ["duplicate_policy"] = DuplicatePolicy,
                    ["observation_bound"] = ObservationBound,
                    ["stability"] = Stability,
                    ["summary"] = Summary ?? String.Empty,
                    ["tags"] = new JArray(Tags ?? Array.Empty<string>())
                };
                if (includeSchema)
                {
                    result["params_schema"] = ParamsSchema != null
                        ? ParamsSchema.DeepClone()
                        : new JObject
                        {
                            ["type"] = "object",
                            ["additionalProperties"] = true,
                            ["description"] = "This method has not yet published a strict parameter schema."
                        };
                }
                return result;
            }
        }

        /// <summary>What a call guarantees about the scene state behind its answer.</summary>
        /// <remarks>
        /// A separate axis from Evidence, which says whether a call produces a
        /// durable artifact. scene.describe produces no artifact and still must
        /// not answer from a stale baseline, so the two cannot be one flag.
        /// Authoritative is the default: a tool that presents scene state and
        /// forgets to classify itself is correct and slow, never fast and wrong.
        /// </remarks>
        public const string ReadsAuthoritative = "authoritative";
        public const string ReadsNotified = "notified";
        public const string ReadsIndependent = "independent";
        /// <summary>No tool was resolved, so no class of answer applies.</summary>
        /// <remarks>
        /// Reported on failures that happen before a method is known — a
        /// protocol mismatch, a missing id, an unknown method. Claiming
        /// `independent` there would be almost right and occasionally wrong;
        /// this says what is actually the case.
        /// </remarks>
        public const string ReadsUnknown = "unknown";

        /// <summary>How reproducible an operation is, which is not whether it takes a seed.</summary>
        /// <remarks>
        /// A seed makes a stochastic computation repeatable; it does not by
        /// itself make the result reproducible, because the thing consuming it
        /// may not be under our control. The proof is ultimately the output hash,
        /// and a recipe must never claim a determinism its backend does not
        /// provide.
        ///
        /// Exact: same inputs, same tool version, same canonical output. Seeded:
        /// a seed is required and expected to reproduce the result. External or
        /// unproven: seeds and versions are recorded, and bit-identical output is
        /// not guaranteed by the system actually doing the work.
        /// </remarks>
        public const string DeterminismExact = "exact";
        public const string DeterminismSeeded = "seeded";
        public const string DeterminismExternal = "external_or_unproven";

        /// <summary>How a redelivery of a side-effecting operation is resolved.</summary>
        /// <remarks>
        /// Every such operation must have one, decided deliberately: what must
        /// never happen is a verb whose duplicate behaviour is whatever its
        /// handler happens to do. That is how a redelivered delete came to report
        /// NOT_FOUND for work that had succeeded.
        ///
        /// Replay returns the stored result of the original execution. Terminal
        /// state lets the operation's own state machine give a definitive answer
        /// about what happened, without repeating the effect. Indeterminate means
        /// the truth genuinely cannot be recovered, and the caller is told so
        /// rather than given a guess.
        ///
        /// Written for what is coming as much as for what exists: export, bake,
        /// file write, asset import and external generation are where a duplicate
        /// execution stops being cheap.
        /// </remarks>
        public const string DuplicateReplay = "replay";
        public const string DuplicateTerminalState = "terminal_state";
        public const string DuplicateIndeterminate = "indeterminate";

        private readonly Dictionary<string, ToolSpec> _tools =
            new Dictionary<string, ToolSpec>(StringComparer.Ordinal);

        /// <summary>
        /// Publish one tool, with everything the dispatch policy needs to know about it.
        /// </summary>
        /// <remarks>
        /// Mutating, side-effecting and observation-bound are three orthogonal
        /// questions, and they are checked here rather than trusted: a tool whose
        /// declaration contradicts itself fails to register, instead of quietly
        /// taking the permissive default and being discovered at three in the
        /// morning.
        /// </remarks>
        public void AddTool(
            string name,
            Func<JObject, JToken> handler,
            bool mutating = false,
            bool evidence = false,
            bool requiresUi = false,
            string reads = ReadsAuthoritative,
            string stability = "alpha",
            bool transactionControl = false,
            string summary = null,
            string[] tags = null,
            JObject paramsSchema = null,
            string[] seeds = null,
            string determinism = DeterminismExact,
            bool? sideEffecting = null,
            string duplicatePolicy = null,
            bool observationBound = false)
        {
            if (_tools.ContainsKey(name)) throw new InvalidOperationException("Duplicate RoboVision tool: " + name);
            if (reads != ReadsAuthoritative && reads != ReadsNotified && reads != ReadsIndependent)
                throw new InvalidOperationException(name + ": unknown read consistency: " + reads);
            if (mutating && reads != ReadsAuthoritative)
                throw new InvalidOperationException(name + ": a mutating tool re-reads before it runs");

            // A mutating tool is side-effecting by definition; anything else has
            // to say so, because "does not advance the scene revision" is not the
            // same claim as "safe to execute twice".
            var resolvedSideEffecting = sideEffecting ?? mutating;
            var resolvedPolicy = duplicatePolicy;
            if (resolvedSideEffecting && resolvedPolicy == null && mutating)
                resolvedPolicy = DuplicateReplay;
            if (resolvedSideEffecting && resolvedPolicy == null)
                throw new InvalidOperationException(
                    name + ": a side-effecting tool must declare how a duplicate delivery is resolved");
            if (resolvedPolicy != null && resolvedPolicy != DuplicateReplay
                && resolvedPolicy != DuplicateTerminalState && resolvedPolicy != DuplicateIndeterminate)
                throw new InvalidOperationException(name + ": unknown duplicate policy: " + resolvedPolicy);
            if (resolvedPolicy != null && !resolvedSideEffecting)
                throw new InvalidOperationException(name + ": only a side-effecting tool resolves duplicates");

            var resolvedSeeds = seeds ?? Array.Empty<string>();
            if (determinism != DeterminismExact && determinism != DeterminismSeeded
                && determinism != DeterminismExternal)
                throw new InvalidOperationException(name + ": unknown determinism: " + determinism);
            // The two halves of the declaration have to agree, or the metadata is
            // decoration. A seeded tool with no channels cannot be reproduced,
            // and channels on a tool claiming exactness are randomness nobody
            // records.
            if (determinism == DeterminismSeeded && resolvedSeeds.Length == 0)
                throw new InvalidOperationException(
                    name + ": a seeded tool must name the randomness channels it consumes");
            if (resolvedSeeds.Length > 0 && determinism == DeterminismExact)
                throw new InvalidOperationException(
                    name + ": a tool that consumes seeds is not exact; declare seeded or external_or_unproven");

            var documented = RoboVisionToolDocs.Get(name);
            _tools[name] = new ToolSpec
            {
                Name = name,
                Handler = handler,
                Mutating = mutating,
                Evidence = evidence,
                RequiresUi = requiresUi,
                Reads = reads,
                Stability = stability,
                TransactionControl = transactionControl,
                Summary = summary ?? documented?.Summary ?? String.Empty,
                Tags = tags ?? documented?.Tags ?? Array.Empty<string>(),
                ParamsSchema = paramsSchema ?? (documented?.ParamsSchema != null ? (JObject)documented.ParamsSchema.DeepClone() : null),
                Seeds = resolvedSeeds,
                Determinism = determinism,
                SideEffecting = resolvedSideEffecting,
                DuplicatePolicy = resolvedPolicy,
                ObservationBound = observationBound
            };
        }

        // internal so the package's EditMode tests can drive the host through the
        // same entry point the transport uses, rather than a test-only shim.
        private JObject CapabilityCatalog(JObject parameters)
        {
            var queryToken = parameters["query"];
            var prefixToken = parameters["prefix"];
            if (queryToken != null && queryToken.Type != JTokenType.String)
                throw new RoboVisionException("INVALID_PARAMS", "query must be a string");
            if (prefixToken != null && prefixToken.Type != JTokenType.String)
                throw new RoboVisionException("INVALID_PARAMS", "prefix must be a string");
            var query = parameters.Value<string>("query") ?? String.Empty;
            var prefix = parameters.Value<string>("prefix") ?? String.Empty;
            var includeSchema = parameters.Value<bool?>("include_schema") ?? false;
            var offset = Math.Max(0, parameters.Value<int?>("offset") ?? 0);
            var limit = Math.Max(1, Math.Min(500, parameters.Value<int?>("limit") ?? 100));

            var requiredTags = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (parameters["tags"] != null)
            {
                if (!(parameters["tags"] is JArray tagArray) || tagArray.Any(token => token.Type != JTokenType.String))
                    throw new RoboVisionException("INVALID_PARAMS", "tags must be an array of strings");
                foreach (var tag in tagArray.Values<string>()) requiredTags.Add(tag);
            }

            IEnumerable<ToolSpec> matches = _tools.Values.OrderBy(tool => tool.Name, StringComparer.Ordinal);
            if (!String.IsNullOrWhiteSpace(prefix))
                matches = matches.Where(tool => tool.Name.StartsWith(prefix, StringComparison.OrdinalIgnoreCase));
            if (requiredTags.Count > 0)
                matches = matches.Where(tool => requiredTags.IsSubsetOf(new HashSet<string>(tool.Tags ?? Array.Empty<string>(), StringComparer.OrdinalIgnoreCase)));
            if (!String.IsNullOrWhiteSpace(query))
            {
                matches = matches.Where(tool =>
                    tool.Name.IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0 ||
                    (tool.Summary ?? String.Empty).IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0 ||
                    (tool.Tags ?? Array.Empty<string>()).Any(tag => tag.IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0));
            }

            var list = matches.ToList();
            var page = list.Skip(offset).Take(limit).Select(tool => tool.Describe(includeSchema));
            return new JObject
            {
                ["total"] = list.Count,
                ["offset"] = offset,
                ["limit"] = limit,
                ["has_more"] = offset + Math.Min(limit, Math.Max(0, list.Count - offset)) < list.Count,
                ["methods"] = new JArray(page)
            };
        }

        /// <summary>Is this method published? Used where a tool is registered once, lazily.</summary>
        internal bool HasTool(string name) => _tools.ContainsKey(name);

        private JObject DescribeMethod(JObject parameters)
        {
            var method = parameters.Value<string>("method");
            if (String.IsNullOrWhiteSpace(method))
                throw new RoboVisionException("INVALID_PARAMS", "method is required");
            if (!_tools.TryGetValue(method, out var spec))
                throw new RoboVisionException("UNKNOWN_METHOD", "unknown method: " + method);
            return spec.Describe(true);
        }

        private JArray CompactCapabilities()
        {
            return new JArray(_tools.Values.OrderBy(tool => tool.Name, StringComparer.Ordinal).Select(tool => tool.Describe(false)));
        }
    }
}
