using System;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEditor.SceneManagement;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// Drives the Unity host through the same dispatch entry point the transport uses.
    /// </summary>
    /// <remarks>
    /// Requests are protocol-shaped rather than direct method calls so the tests
    /// exercise validation, revision checking, checkpointing and error shaping
    /// instead of stepping over them.
    /// </remarks>
    internal sealed class Harness
    {
        private readonly string _label;
        private int _serial;

        internal Harness(string label)
        {
            _label = label;
            Host = RoboVisionHost.Instance;
        }

        internal RoboVisionHost Host { get; }

        internal long Revision => Host.Revision;

        internal JObject Call(
            string method,
            JObject parameters = null,
            long? ifRevision = null,
            bool ok = true,
            string code = null)
        {
            _serial++;
            var request = new JObject
            {
                ["rv"] = "1.0",
                ["id"] = _label + "-" + _serial,
                ["method"] = method,
                ["params"] = parameters ?? new JObject()
            };
            if (ifRevision.HasValue) request["if_revision"] = ifRevision.Value;

            var response = Host.Dispatch(request);
            var actualOk = response.Value<bool>("ok");
            if (ok)
            {
                Assert.That(actualOk, Is.True, method + " failed: " + response.ToString(Newtonsoft.Json.Formatting.None));
            }
            else
            {
                Assert.That(actualOk, Is.False, method + " unexpectedly succeeded: " + response.ToString(Newtonsoft.Json.Formatting.None));
                if (code != null)
                {
                    var actualCode = response["error"]?.Value<string>("code");
                    Assert.That(actualCode, Is.EqualTo(code),
                        method + " expected " + code + " but returned " + actualCode + ": "
                        + response.ToString(Newtonsoft.Json.Formatting.None));
                }
            }
            return response;
        }

        internal JObject Result(string method, JObject parameters = null, long? ifRevision = null)
        {
            return (JObject)Call(method, parameters, ifRevision)["result"];
        }

        internal string Fingerprint()
        {
            return Result("scene.snapshot").Value<string>("fingerprint");
        }

        internal string CreateObject(string name, params float[] position)
        {
            var parameters = new JObject { ["name"] = name };
            if (position != null && position.Length == 3)
                parameters["local_position"] = new JArray(position[0], position[1], position[2]);
            return Result("object.create", parameters).Value<string>("id");
        }

        internal string ActiveTransaction { get; private set; }

        internal string BeginTransaction(string label)
        {
            var id = Result("transaction.begin", new JObject { ["label"] = label }).Value<string>("transaction");
            ActiveTransaction = id;
            return id;
        }

        internal JObject EndTransaction(string method, string id, bool force = false)
        {
            var parameters = new JObject { ["transaction"] = id };
            if (force) parameters["force"] = true;
            var result = Result(method, parameters);
            ActiveTransaction = null;
            return result;
        }

        /// <summary>
        /// Abandon a transaction a failing test left open, through the real API.
        /// </summary>
        /// <remarks>
        /// The host is a singleton, so an open transaction would otherwise leak
        /// into the next test as TRANSACTION_ACTIVE. force is used because the
        /// point here is teardown, not a claim about restoration; a failed
        /// rollback is ignored on purpose so it cannot mask the original failure.
        /// </remarks>
        internal void ResetTransactions()
        {
            if (ActiveTransaction == null) return;
            var id = ActiveTransaction;
            ActiveTransaction = null;
            _serial++;
            try
            {
                Host.Dispatch(new JObject
                {
                    ["rv"] = "1.0",
                    ["id"] = _label + "-teardown-" + _serial,
                    ["method"] = "transaction.rollback",
                    ["params"] = new JObject { ["transaction"] = id, ["force"] = true }
                });
            }
            catch (Exception)
            {
                // Teardown must not turn a test failure into an error.
            }
        }

        /// <summary>A deterministic, empty single scene.</summary>
        internal static void FreshScene()
        {
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        }
    }
}
