using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// Gate 4 across a real process boundary, driven by -executeMethod.
    /// </summary>
    /// <remarks>
    /// A domain reload rebuilds the scripting domain inside one editor process.
    /// An editor restart is a different lifecycle event: the process dies, the
    /// package is resolved again from scratch, the listener is torn down by the
    /// OS rather than by our own shutdown handler, and every static is gone
    /// because the memory is gone. Those are not the same guarantee, so they are
    /// proven separately.
    ///
    /// Phase1 runs in the first editor and records what it built. Phase2 runs in
    /// a second, freshly launched editor on the same project and checks the
    /// contract against that record. The shell harness confirms the first
    /// process actually exited in between.
    /// </remarks>
    public static class RoboVisionRestartGate
    {
        private const string SceneDirectory = "Assets/RoboVisionRestart";
        private const string ScenePath = SceneDirectory + "/Restart.unity";

        private static string StatePath =>
            Environment.GetEnvironmentVariable("ROBOVISION_RESTART_STATE")
            ?? Path.Combine(Path.GetTempPath(), "robovision-restart-state.json");

        private static int FreePort()
        {
            var probe = new TcpListener(IPAddress.Loopback, 0);
            probe.Start();
            var port = ((IPEndPoint)probe.LocalEndpoint).Port;
            probe.Stop();
            return port;
        }

        private static JObject Call(string method, JObject parameters = null)
        {
            return RoboVisionHost.Instance.Dispatch(new JObject
            {
                ["rv"] = "1.0",
                ["id"] = "restart-" + Guid.NewGuid().ToString("N").Substring(0, 8),
                ["method"] = method,
                ["params"] = parameters ?? new JObject()
            });
        }

        private static JObject Ok(string method, JObject parameters = null)
        {
            var response = Call(method, parameters);
            if (!response.Value<bool>("ok"))
                throw new Exception(method + " failed: " + response.ToString(Formatting.None));
            return (JObject)response["result"];
        }

        public static void Phase1()
        {
            var report = new JObject();
            try
            {
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                if (!Directory.Exists(SceneDirectory)) Directory.CreateDirectory(SceneDirectory);
                AssetDatabase.Refresh();

                // Deterministic content so the second editor can compare like with like.
                var alpha = Ok("object.create", new JObject
                {
                    ["name"] = "RestartAlpha",
                    ["local_position"] = new JArray(1f, 2f, 3f)
                }).Value<string>("id");
                var beta = Ok("object.create", new JObject { ["name"] = "RestartBeta" }).Value<string>("id");
                Ok("component.add", new JObject { ["object"] = beta, ["type"] = "UnityEngine.BoxCollider" });

                report["session_handles"] = new JArray(alpha, beta);

                if (!EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), ScenePath))
                    throw new Exception("the scene did not save");
                AssetDatabase.SaveAssets();

                // Saving upgrades identity; the durable form is what must survive.
                var described = Ok("scene.describe");
                var objects = described["scenes"].SelectMany(s => s["objects"]).ToList();
                report["durable"] = new JObject(
                    objects.Select(o => new JProperty(o.Value<string>("name"), o.Value<string>("id"))));
                report["persistent_flags"] = new JObject(
                    objects.Select(o => new JProperty(o.Value<string>("name"), o.Value<bool>("identity_persistent"))));

                report["fingerprint"] = Ok("scene.snapshot").Value<string>("fingerprint");
                report["scene_path"] = ScenePath;

                var port = FreePort();
                if (RoboVisionHost.Instance.Running) RoboVisionHost.Instance.Stop();
                RoboVisionHost.Instance.Start(port);
                report["port"] = port;
                report["listening"] = RoboVisionHost.Instance.Running;
                report["editor_version"] = Application.unityVersion;
                report["phase1"] = "ok";
            }
            catch (Exception ex)
            {
                report["phase1"] = "failed";
                report["error"] = ex.GetType().Name + ": " + ex.Message;
            }

            File.WriteAllText(StatePath, report.ToString(Formatting.Indented));
            UnityEngine.Debug.Log("ROBOVISION_RESTART_PHASE1 " + report.ToString(Formatting.None));
            EditorApplication.Exit(report.Value<string>("phase1") == "ok" ? 0 : 1);
        }

        public static void Phase2()
        {
            var findings = new JObject();
            var failures = new JArray();
            void Check(string name, bool condition, string detail)
            {
                findings[name] = condition;
                if (!condition) failures.Add(name + ": " + detail);
            }

            try
            {
                var state = JObject.Parse(File.ReadAllText(StatePath));
                if (state.Value<string>("phase1") != "ok")
                    throw new Exception("phase 1 did not succeed: " + state.ToString(Formatting.None));

                // 1. The package must resolve and load again from scratch.
                var package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(RoboVisionHost).Assembly);
                Check("package_resolved", package != null && package.name == "com.kodaxa.robovision",
                    "PackageInfo did not report the RoboVision package");

                // 2. A brand new process must not have inherited a listener.
                var port = state.Value<int>("port");
                Check("port_free_before_start", !RoboVisionHost.Instance.Running,
                    "the host was already running before this process started it");

                EditorSceneManager.OpenScene(state.Value<string>("scene_path"), OpenSceneMode.Single);

                // 3. Durable references must still address the same objects.
                var durable = (JObject)state["durable"];
                var resolvedAll = true;
                var detail = "";
                foreach (var entry in durable.Properties())
                {
                    var response = Call("object.inspect", new JObject { ["object"] = entry.Value.ToString() });
                    if (!response.Value<bool>("ok")
                        || response["result"].Value<string>("name") != entry.Name)
                    {
                        resolvedAll = false;
                        detail += entry.Name + " -> " + response.ToString(Formatting.None) + "; ";
                    }
                }
                Check("durable_ids_resolve", resolvedAll, detail);

                // 4. Session handles from the dead process must fail cleanly.
                var staleClean = true;
                var staleDetail = "";
                foreach (var handle in state["session_handles"].Select(h => h.ToString()))
                {
                    var response = Call("object.inspect", new JObject { ["object"] = handle });
                    var code = response.Value<bool>("ok") ? "RESOLVED" : response["error"].Value<string>("code");
                    if (code != "NOT_FOUND")
                    {
                        staleClean = false;
                        staleDetail += handle + " -> " + code + "; ";
                    }
                }
                Check("stale_session_handles_not_found", staleClean, staleDetail);

                // 5. The scene must be semantically what phase 1 saved.
                var fingerprint = Ok("scene.snapshot").Value<string>("fingerprint");
                findings["fingerprint_before"] = state.Value<string>("fingerprint");
                findings["fingerprint_after"] = fingerprint;
                Check("fingerprint_equivalent", fingerprint == state.Value<string>("fingerprint"),
                    "fingerprint changed across the restart");

                // 6. The port the dead editor held must be bindable again.
                RoboVisionHost.Instance.Start(port);
                Check("rebinds_previous_port", RoboVisionHost.Instance.Running,
                    "the new process could not bind the port the old one used");

                // 7. The shipped Python client must be able to reconnect.
                // Recorded once: reporting the same fact as both a finding and a
                // check made the harness print eight PASS lines for seven
                // checks, and an inflated count is exactly the kind of evidence
                // drift this gate exists to prevent.
                var pythonReconnected = PythonReconnect(package, port, out var pythonDetail);
                Check("python_client_reconnects", pythonReconnected, pythonDetail);

                RoboVisionHost.Instance.Stop();
                findings["phase2"] = failures.Count == 0 ? "ok" : "failed";
            }
            catch (Exception ex)
            {
                findings["phase2"] = "failed";
                failures.Add(ex.GetType().Name + ": " + ex.Message);
            }

            findings["failures"] = failures;
            File.WriteAllText(StatePath + ".phase2.json", findings.ToString(Formatting.Indented));
            UnityEngine.Debug.Log("ROBOVISION_RESTART_PHASE2 " + findings.ToString(Formatting.None));
            EditorApplication.Exit(failures.Count == 0 ? 0 : 1);
        }

        private static bool PythonReconnect(UnityEditor.PackageManager.PackageInfo package, int port, out string detail)
        {
            detail = "";
            if (package == null || string.IsNullOrEmpty(package.resolvedPath))
            {
                detail = "package path unavailable";
                return false;
            }
            var repoRoot = Path.GetFullPath(Path.Combine(package.resolvedPath, "..", "..", "..", ".."));
            if (!File.Exists(Path.Combine(repoRoot, "robovision", "client.py")))
            {
                detail = "python client not found under " + repoRoot;
                return false;
            }

            var script =
                "import json,sys\n" +
                "sys.path.insert(0, sys.argv[1])\n" +
                "from robovision.client import RoboVisionClient\n" +
                "with RoboVisionClient('127.0.0.1', int(sys.argv[2]), timeout=30.0) as c:\n" +
                "    hello = c.call('system.hello')\n" +
                "    print('RESULT ' + json.dumps({'editor': hello['result']['editor']['name']}))\n";
            var scriptPath = Path.Combine(Path.GetTempPath(), "robovision_restart_client.py");
            File.WriteAllText(scriptPath, script);

            var info = new ProcessStartInfo
            {
                FileName = Environment.GetEnvironmentVariable("ROBOVISION_PYTHON") ?? "python",
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            };
            info.ArgumentList.Add(scriptPath);
            info.ArgumentList.Add(repoRoot);
            info.ArgumentList.Add(port.ToString());

            try
            {
                using (var process = Process.Start(info))
                {
                    // The host answers from the editor thread, so pump the
                    // transport while the external client is talking to it.
                    var deadline = DateTime.UtcNow.AddSeconds(60);
                    while (!process.HasExited && DateTime.UtcNow < deadline)
                    {
                        RoboVisionHost.Instance.ServiceTransportOnce();
                        System.Threading.Thread.Sleep(5);
                    }
                    var stdout = process.StandardOutput.ReadToEnd();
                    var stderr = process.StandardError.ReadToEnd();
                    if (!process.HasExited) { process.Kill(); detail = "python client timed out"; return false; }
                    if (process.ExitCode != 0) { detail = "python exited " + process.ExitCode + ": " + stderr; return false; }
                    var marker = stdout.IndexOf("RESULT ", StringComparison.Ordinal);
                    if (marker < 0) { detail = "no result: " + stdout + stderr; return false; }
                    var payload = JObject.Parse(stdout.Substring(marker + "RESULT ".Length).Trim());
                    if (payload.Value<string>("editor") != "Unity") { detail = "unexpected editor: " + payload; return false; }
                    return true;
                }
            }
            catch (Exception ex)
            {
                detail = ex.GetType().Name + ": " + ex.Message;
                return false;
            }
        }
    }
}
