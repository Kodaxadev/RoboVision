using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor
{
    internal sealed class RoboVisionException : Exception
    {
        public readonly string Code;
        public readonly bool Retryable;
        // Deliberately shadows Exception.Data: this carries the structured
        // RoboVision error payload, not the base class's IDictionary.
        public new readonly JToken Data;

        public RoboVisionException(string code, string message, bool retryable = false, JToken data = null) : base(message)
        {
            Code = code;
            Retryable = retryable;
            Data = data;
        }
    }

    internal sealed partial class RoboVisionHost : IDisposable
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

            public JObject Describe(bool includeSchema = false)
            {
                var result = new JObject
                {
                    ["name"] = Name,
                    ["mutating"] = Mutating,
                    ["evidence"] = Evidence,
                    ["requires_ui"] = RequiresUi,
                    ["reads"] = Reads,
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

        public const string ProtocolVersion = "1.0";
        public const string HostVersion = "0.1.0";
        public const int DefaultPort = 9878;
        public static RoboVisionHost Instance { get; } = new RoboVisionHost();

        /// <summary>What a call guarantees about the scene state behind its answer.</summary>
        /// <remarks>
        /// A separate axis from Evidence, which says whether a call produces a
        /// durable artifact. scene.describe produces no artifact and still must
        /// not answer from a stale baseline, so the two cannot be one flag.
        /// Authoritative is the default: a tool that presents scene state and
        /// forgets to classify itself is correct and slow, never fast and wrong.
        /// </remarks>
        /// <summary>Which universe the state in a response came from.</summary>
        /// <remarks>
        /// Play mode is not a view of the authored scene. It instantiates the
        /// open scenes, gives their objects runtime addresses, and throws all of
        /// it away on exit. Reads are still answered — an agent may want to look
        /// at a running scene — but a response carrying runtime objects must say
        /// so, because the revision it also carries is the authored one and does
        /// not version anything the client is looking at.
        /// </remarks>
        public const string DomainAuthored = "authored";
        public const string DomainPlayRuntime = "play_runtime";

        public const string ReadsAuthoritative = "authoritative";
        public const string ReadsNotified = "notified";
        public const string ReadsIndependent = "independent";

        private readonly Dictionary<string, ToolSpec> _tools = new Dictionary<string, ToolSpec>(StringComparer.Ordinal);
        private RoboVisionServer _server;
        private readonly RoboVisionReconciler _reconciler;

        internal readonly RoboVisionTransactions Transactions;
        public bool Running => _server != null && _server.Running;
        public long Revision => _reconciler.Revision;
        /// <summary>This loaded RoboVision runtime; rotates on a domain or assembly reload.</summary>
        public string Bridge => _reconciler.Bridge;
        /// <summary>This editing context; rotates when a different one is opened.</summary>
        public string WorldIncarnation => _reconciler.WorldIncarnation;
        internal RoboVisionJournal Journal => _reconciler.Journal;
        /// <summary>The universe the editor is in right now.</summary>
        public static string StateDomain =>
            EditorApplication.isPlayingOrWillChangePlaymode ? DomainPlayRuntime : DomainAuthored;
        internal SceneRead CurrentRead => _reconciler.Current ?? _reconciler.Resync();
        public int Port => _server?.Port ?? DefaultPort;

        private RoboVisionHost()
        {
            Transactions = new RoboVisionTransactions(this);
            _reconciler = new RoboVisionReconciler(Transactions);
            RegisterSystemTools();
            RoboVisionSceneTools.Register(this);
            RoboVisionSerializedTools.Register(this);
            RoboVisionViewportTools.Register(this);
        }

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
            JObject paramsSchema = null)
        {
            if (_tools.ContainsKey(name)) throw new InvalidOperationException("Duplicate RoboVision tool: " + name);
            if (reads != ReadsAuthoritative && reads != ReadsNotified && reads != ReadsIndependent)
                throw new InvalidOperationException(name + ": unknown read consistency: " + reads);
            if (mutating && reads != ReadsAuthoritative)
                throw new InvalidOperationException(name + ": a mutating tool re-reads before it runs");
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
                ParamsSchema = paramsSchema ?? (documented?.ParamsSchema != null ? (JObject)documented.ParamsSchema.DeepClone() : null)
            };
        }

        public void Start(int port = DefaultPort)
        {
            if (Running) return;
            _server = new RoboVisionServer(port, Dispatch, Transactions.ClientDisconnected);
            _server.Start();
            _reconciler.MarkDirty();
            _reconciler.RefreshDirtyState();
            EditorApplication.update -= Update;
            EditorApplication.update += Update;
            EditorApplication.hierarchyChanged -= MarkDirty;
            EditorApplication.hierarchyChanged += MarkDirty;
            EditorApplication.projectChanged -= MarkDirty;
            EditorApplication.projectChanged += MarkDirty;
            Undo.undoRedoPerformed -= MarkDirty;
            Undo.undoRedoPerformed += MarkDirty;
            Undo.postprocessModifications -= OnPostprocessModifications;
            Undo.postprocessModifications += OnPostprocessModifications;
        }

        public void Stop()
        {
            EditorApplication.update -= Update;
            EditorApplication.hierarchyChanged -= MarkDirty;
            EditorApplication.projectChanged -= MarkDirty;
            Undo.undoRedoPerformed -= MarkDirty;
            Undo.postprocessModifications -= OnPostprocessModifications;
            _server?.Dispose();
            _server = null;
        }

        private void Update()
        {
            try { _server?.Poll(8); }
            catch (Exception ex) { UnityEngine.Debug.LogException(ex); }
        }

        private UndoPropertyModification[] OnPostprocessModifications(UndoPropertyModification[] modifications)
        {
            MarkDirty();
            return modifications;
        }

        /// <summary>Service the socket once from the caller's thread.</summary>
        /// <remarks>
        /// Normally EditorApplication.update drives this. A harness running under
        /// -executeMethod holds the editor loop for the whole call, so nothing
        /// would ever answer an external client while it waits. Such a driver
        /// pumps explicitly rather than sleeping and hoping.
        /// </remarks>
        internal void ServiceTransportOnce() => Update();

        /// <summary>Identifies the caller of the request being dispatched.</summary>
        /// <remarks>
        /// 0 means an in-process caller such as a menu action or a test; socket
        /// connections are numbered from 1. It exists so the concurrency policy
        /// can distinguish callers rather than treating the whole editor as one
        /// anonymous client.
        /// </remarks>
        internal const long LocalClientId = 0;

        internal long CurrentClientId { get; private set; } = LocalClientId;

        /// <summary>A notification arrived. It says something may have changed, not what.</summary>
        /// <remarks>
        /// Every editor notification funnels here and no further. ObjectChangeEvents
        /// publishes undoable changes to loaded objects once per frame, so it is not
        /// comprehensive, and broad events such as ChangeScene may carry no object
        /// information at all. Deciding what changed is reconciliation's job.
        /// </remarks>
        public void MarkDirty() => _reconciler.MarkDirty();

        internal bool DirtyForTests => _reconciler.Dirty;

        /// <summary>Force one authoritative reconciliation. Exposed for lifecycle callers.</summary>
        internal SceneRead Resync() => _reconciler.Resync();

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

        private void RegisterSystemTools()
        {
            AddTool(
                "system.ping",
                _ => new JObject { ["pong"] = true, ["host"] = "unity", ["revision"] = Revision },
                reads: ReadsNotified,
                stability: "beta");
            AddTool(
                "system.capabilities",
                CapabilityCatalog,
                reads: ReadsIndependent,
                stability: "beta");
            AddTool(
                "system.method",
                DescribeMethod,
                reads: ReadsIndependent,
                stability: "beta");
            AddTool(
                "system.hello",
                _ =>
                {
                    var capabilities = CompactCapabilities();
                    return new JObject
                    {
                        ["protocol"] = ProtocolVersion,
                        ["host"] = new JObject
                        {
                            ["name"] = "unity",
                            ["implementation"] = "robovision_unity",
                            ["version"] = HostVersion
                        },
                        ["editor"] = new JObject
                        {
                            ["name"] = "Unity",
                            ["version"] = Application.unityVersion,
                            ["playing"] = EditorApplication.isPlaying
                        },
                        ["revision"] = Revision,
                        ["bridge"] = Bridge,
                        ["world_incarnation"] = WorldIncarnation,
                        ["journal"] = Journal.State(),
                        ["capability_count"] = capabilities.Count,
                        ["capabilities"] = capabilities,
                        ["discovery"] = new JObject
                        {
                            ["search"] = "system.capabilities",
                            ["describe_exact"] = "system.method",
                            ["schemas_on_demand"] = true
                        },
                        ["transport"] = new JObject
                        {
                            ["kind"] = "tcp-jsonl",
                            ["bind"] = "127.0.0.1",
                            ["port"] = Port,
                            ["editor_thread_dispatch"] = true
                        },
                        ["security"] = new JObject
                        {
                            ["loopback_only"] = true,
                            ["arbitrary_code_enabled"] = false
                        },
                        ["transaction"] = new JObject
                        {
                            ["active"] = Transactions.Active,
                            // Surfaced so a client can discover a transaction
                            // another connection opened — including one whose
                            // owner disconnected and left it waiting — instead
                            // of only learning about it when a mutation is
                            // refused.
                            ["state"] = Transactions.ActiveState(),
                            ["your_client"] = CurrentClientId,
                            ["ownership"] = "a transaction belongs to the connection that opened it; "
                                + "mutations from other connections are refused with TRANSACTION_FOREIGN "
                                + "while it is active. If the owner disconnects the transaction is marked "
                                + "orphaned and any client may commit or roll it back.",
                            ["external_change_protection"] = true,
                            ["failed_operation_recovery"] = true
                        }
                    };
                },
                reads: ReadsNotified,
                stability: "beta");
        }

        public void Dispose() => Stop();
    }
}
