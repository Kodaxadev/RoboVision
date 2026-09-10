using System;
using System.Collections.Generic;
using System.Diagnostics;
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
        public const string ProtocolVersion = "1.0";
        public const string HostVersion = "0.1.0";
        public const int DefaultPort = 9878;
        public static RoboVisionHost Instance { get; } = new RoboVisionHost();

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

        private RoboVisionServer _server;
        private readonly RoboVisionReconciler _reconciler;

        internal readonly RoboVisionTransactions Transactions;
        public bool Running => _server != null && _server.Running;
        public long Revision => _reconciler.Revision;
        /// <summary>This loaded RoboVision runtime; rotates on a domain or assembly reload.</summary>
        public string Bridge => _reconciler.Bridge;
        /// <summary>This editing context; rotates when a different one is opened.</summary>
        public string WorldIncarnation => _reconciler.WorldIncarnation;
        internal bool WorldSettled => _reconciler.WorldSettled;
        internal bool WorldResumed => _reconciler.WorldResumed;
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

        /// <summary>The listening transport, for the gates that have to break it.</summary>
        /// <remarks>
        /// Exposed so the package's own test assembly can arm the fault seams;
        /// nothing in the Editor assembly reads this, and a project that does not
        /// list the tests as testables never compiles the code that does.
        /// </remarks>
        internal RoboVisionServer Transport => _server;

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

        private void RegisterSystemTools()
        {
            AddTool(
                "system.ping",
                _ => new JObject { ["pong"] = true, ["host"] = "unity", ["revision"] = Revision },
                reads: ReadsNotified,
                stability: "beta");
            // Health is the one system call that pays for an authoritative read,
            // and the payment is the point: its successful execution proves the
            // request arrived and ran on the editor thread, and its resync proves
            // the editing context is readable and that the world and revision it
            // reports belong to the state it just read. It authors nothing to
            // establish any of that.
            AddTool(
                "system.health",
                _ => HealthReport(),
                reads: ReadsAuthoritative,
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
                        // Which universe the state in a response came from. Play
                        // mode is not a view of the authored scene, and a client
                        // driving both editors reads this field in both rather
                        // than inferring it from which host answered.
                        ["state_domain"] = StateDomain,
                        // The convention this world is authored in, published so
                        // an external agent can pin the value it is *required* to
                        // pin. Unity enforced this contract internally while
                        // publishing nothing, which left a strict autonomous
                        // invocation constructible only by a caller that could
                        // reach into the package for the constant.
                        ["coordinate_contract"] = RoboVisionRecipe.CoordinateContract(),
                        ["units"] = RoboVisionRecipe.Units(),
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
                            // Structured facts rather than policy prose. The
                            // paragraph that used to sit here still said any
                            // client may finish an orphan, which stopped being
                            // true when adoption started requiring the recovery
                            // credential — a sentence drifting out of step with
                            // the code enforcing it is exactly what two
                            // machine-readable statements cannot do.
                            ["ownership"] = "connection",
                            ["orphan_requires_adoption"] = true,
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
