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
        public readonly JToken Data;

        public RoboVisionException(string code, string message, bool retryable = false, JToken data = null) : base(message)
        {
            Code = code;
            Retryable = retryable;
            Data = data;
        }
    }

    internal sealed class RoboVisionHost : IDisposable
    {
        internal sealed class ToolSpec
        {
            public string Name;
            public Func<JObject, JToken> Handler;
            public bool Mutating;
            public bool Evidence;
            public bool RequiresUi;
            public string Stability;
            public bool TransactionControl;

            public JObject Describe() => new JObject
            {
                ["name"] = Name,
                ["mutating"] = Mutating,
                ["evidence"] = Evidence,
                ["requires_ui"] = RequiresUi,
                ["stability"] = Stability
            };
        }

        public const string ProtocolVersion = "1.0";
        public const string HostVersion = "0.1.0";
        public const int DefaultPort = 9878;
        public static RoboVisionHost Instance { get; } = new RoboVisionHost();

        private readonly Dictionary<string, ToolSpec> _tools = new Dictionary<string, ToolSpec>(StringComparer.Ordinal);
        private RoboVisionServer _server;
        private bool _dirty = true;
        private string _fingerprint;
        private long _revision;

        internal readonly RoboVisionTransactions Transactions;
        public bool Running => _server != null && _server.Running;
        public long Revision => _revision;
        public int Port => _server?.Port ?? DefaultPort;

        private RoboVisionHost()
        {
            Transactions = new RoboVisionTransactions(this);
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
            string stability = "alpha",
            bool transactionControl = false)
        {
            if (_tools.ContainsKey(name)) throw new InvalidOperationException("Duplicate RoboVision tool: " + name);
            _tools[name] = new ToolSpec
            {
                Name = name,
                Handler = handler,
                Mutating = mutating,
                Evidence = evidence,
                RequiresUi = requiresUi,
                Stability = stability,
                TransactionControl = transactionControl
            };
        }

        public void Start(int port = DefaultPort)
        {
            if (Running) return;
            _server = new RoboVisionServer(port, Dispatch);
            _server.Start();
            _dirty = true;
            RefreshDirtyState();
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

        public void MarkDirty() => _dirty = true;

        private void RefreshDirtyState()
        {
            if (!_dirty) return;
            var current = RoboVisionSceneTools.ComputeFingerprint();
            if (_fingerprint != null && !String.Equals(_fingerprint, current, StringComparison.Ordinal))
            {
                if (Transactions.Active) Transactions.MarkExternalChange(current);
                _revision++;
            }
            _fingerprint = current;
            _dirty = false;
        }

        internal void AcceptOwnMutation()
        {
            var current = RoboVisionSceneTools.ComputeFingerprint();
            if (_fingerprint != null && !String.Equals(_fingerprint, current, StringComparison.Ordinal)) _revision++;
            _fingerprint = current;
            _dirty = false;
        }

        private void AcceptRecoveredState(string fingerprint)
        {
            _fingerprint = fingerprint;
            _dirty = false;
        }

        private JObject Dispatch(JObject raw)
        {
            var watch = Stopwatch.StartNew();
            var requestId = raw.Value<string>("id") ?? "invalid";
            RoboVisionTransactions.OperationCheckpoint checkpoint = null;
            try
            {
                RefreshDirtyState();
                if (raw.Value<string>("rv") != ProtocolVersion)
                    throw new RoboVisionException("PROTOCOL_MISMATCH", "expected protocol " + ProtocolVersion);
                if (String.IsNullOrWhiteSpace(requestId))
                    throw new RoboVisionException("INVALID_REQUEST", "id must be a non-empty string");
                var method = raw.Value<string>("method");
                if (String.IsNullOrWhiteSpace(method))
                    throw new RoboVisionException("INVALID_REQUEST", "method must be a non-empty string");
                var parameters = raw["params"] as JObject ?? new JObject();
                if (!_tools.TryGetValue(method, out var spec))
                    throw new RoboVisionException("UNKNOWN_METHOD", "unknown method: " + method);

                var revisionToken = raw["if_revision"];
                if (spec.Mutating && revisionToken != null && revisionToken.Type != JTokenType.Null)
                {
                    var expected = revisionToken.Value<long>();
                    if (expected != _revision)
                        throw new RoboVisionException(
                            "STALE_REVISION",
                            "scene revision changed",
                            true,
                            new JObject { ["expected"] = expected, ["actual"] = _revision });
                }

                if (spec.Mutating && !spec.TransactionControl)
                    checkpoint = Transactions.PrepareMutation(method);

                var result = spec.Handler(parameters);
                if (spec.Mutating && (!spec.TransactionControl || method == "transaction.rollback"))
                    AcceptOwnMutation();

                watch.Stop();
                return new JObject
                {
                    ["rv"] = ProtocolVersion,
                    ["id"] = requestId,
                    ["ok"] = true,
                    ["revision"] = _revision,
                    ["result"] = result,
                    ["timing_ms"] = Math.Round(watch.Elapsed.TotalMilliseconds, 3)
                };
            }
            catch (RoboVisionException ex)
            {
                var recoveryFailure = TryRecoverFailedOperation(checkpoint, ex, out var recovery);
                return ErrorResponse(requestId, watch, recoveryFailure ?? ex, recovery);
            }
            catch (Exception ex)
            {
                var recoveryFailure = TryRecoverFailedOperation(checkpoint, ex, out var recovery);
                if (recoveryFailure != null)
                    return ErrorResponse(requestId, watch, recoveryFailure, recovery);
                UnityEngine.Debug.LogException(ex);
                watch.Stop();
                var error = new JObject
                {
                    ["code"] = "HOST_EXCEPTION",
                    ["message"] = ex.GetType().Name + ": host operation failed; see Unity Console",
                    ["retryable"] = false
                };
                if (recovery != null) error["data"] = new JObject { ["automatic_recovery"] = recovery };
                return new JObject
                {
                    ["rv"] = ProtocolVersion,
                    ["id"] = requestId,
                    ["ok"] = false,
                    ["revision"] = _revision,
                    ["error"] = error,
                    ["timing_ms"] = Math.Round(watch.Elapsed.TotalMilliseconds, 3)
                };
            }
        }

        private RoboVisionException TryRecoverFailedOperation(
            RoboVisionTransactions.OperationCheckpoint checkpoint,
            Exception original,
            out JObject recovery)
        {
            recovery = null;
            if (checkpoint == null) return null;
            try
            {
                recovery = Transactions.RecoverFailedMutation(checkpoint);
                AcceptRecoveredState(checkpoint.Fingerprint);
                return null;
            }
            catch (RoboVisionException recoveryError)
            {
                var data = recoveryError.Data is JObject objectData ? (JObject)objectData.DeepClone() : new JObject();
                var originalData = new JObject
                {
                    ["type"] = original.GetType().Name,
                    ["message"] = original.Message
                };
                if (original is RoboVisionException rvOriginal)
                {
                    originalData["code"] = rvOriginal.Code;
                    if (rvOriginal.Data != null) originalData["data"] = rvOriginal.Data.DeepClone();
                }
                data["original_error"] = originalData;
                _dirty = true;
                return new RoboVisionException(recoveryError.Code, recoveryError.Message, recoveryError.Retryable, data);
            }
        }

        private JObject ErrorResponse(string requestId, Stopwatch watch, RoboVisionException ex, JObject recovery)
        {
            watch.Stop();
            var error = new JObject
            {
                ["code"] = ex.Code,
                ["message"] = ex.Message,
                ["retryable"] = ex.Retryable
            };
            if (recovery != null)
            {
                error["data"] = new JObject
                {
                    ["operation_error_data"] = ex.Data?.DeepClone(),
                    ["automatic_recovery"] = recovery
                };
            }
            else if (ex.Data != null)
            {
                error["data"] = ex.Data.DeepClone();
            }
            return new JObject
            {
                ["rv"] = ProtocolVersion,
                ["id"] = requestId,
                ["ok"] = false,
                ["revision"] = _revision,
                ["error"] = error,
                ["timing_ms"] = Math.Round(watch.Elapsed.TotalMilliseconds, 3)
            };
        }

        private void RegisterSystemTools()
        {
            AddTool(
                "system.ping",
                _ => new JObject { ["pong"] = true, ["host"] = "unity", ["revision"] = _revision },
                stability: "beta");
            AddTool(
                "system.capabilities",
                _ => new JObject { ["methods"] = new JArray(_tools.Values.OrderBy(t => t.Name).Select(t => t.Describe())) },
                stability: "beta");
            AddTool(
                "system.hello",
                _ => new JObject
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
                    ["revision"] = _revision,
                    ["capabilities"] = new JArray(_tools.Values.OrderBy(t => t.Name).Select(t => t.Describe())),
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
                        ["external_change_protection"] = true,
                        ["failed_operation_recovery"] = true
                    }
                },
                stability: "beta");
        }

        public void Dispose() => Stop();
    }
}
