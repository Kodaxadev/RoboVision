using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// Whether an external agent can discover Unity's pins and act on them.
    /// </summary>
    /// <remarks>
    /// The EditMode suite proves what the host does. It cannot prove what the
    /// host <em>publishes</em>, because an in-process test can call
    /// <c>RoboVisionRecipe.CoordinateContract()</c> directly — so Unity enforced
    /// a coordinate contract it never put in <c>system.hello</c>, and every
    /// in-editor autonomous test kept passing while an external model had no way
    /// to construct a pinned call at all.
    ///
    /// This gate closes that hole the only way it can be closed: a separate
    /// process, over the real socket, running the same host-agnostic claims that
    /// run against Blender, through the same public Python client. Nothing in
    /// <c>tests/public_flow.py</c> knows which editor answered, which is the
    /// whole point of running it twice.
    /// </remarks>
    public static class RoboVisionHealthGate
    {
        private static string ReportPath =>
            Environment.GetEnvironmentVariable("ROBOVISION_HEALTH_REPORT")
            ?? Path.Combine(Path.GetTempPath(), "robovision-unity-health.json");

        private static int FreePort()
        {
            var probe = new TcpListener(IPAddress.Loopback, 0);
            probe.Start();
            var port = ((IPEndPoint)probe.LocalEndpoint).Port;
            probe.Stop();
            return port;
        }

        public static void Run()
        {
            var findings = new JObject();
            var port = FreePort();
            try
            {
                RoboVisionHost.Instance.Start(port);
                findings["port"] = port;

                var package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(
                    typeof(RoboVisionHost).Assembly);
                if (package == null || String.IsNullOrEmpty(package.resolvedPath))
                    throw new InvalidOperationException("PackageInfo did not report the RoboVision package");
                var repoRoot = Path.GetFullPath(Path.Combine(package.resolvedPath, "..", "..", "..", ".."));
                var gate = Path.Combine(repoRoot, "tests", "unity", "public_flow_gate.py");
                if (!File.Exists(gate))
                    throw new FileNotFoundException("the Python gate was not found", gate);

                findings["python"] = DriveGate(repoRoot, gate, port, out var report);
                if (report != null) findings["gate"] = report;
            }
            catch (Exception ex)
            {
                findings["error"] = ex.GetType().Name + ": " + ex.Message;
            }
            finally
            {
                try { RoboVisionHost.Instance.Stop(); } catch (Exception) { }
            }

            var passed = findings["gate"] != null
                         && findings["gate"].Value<bool?>("ok") == true
                         && findings["error"] == null;
            findings["result"] = passed ? "ok" : "failed";
            File.WriteAllText(ReportPath, findings.ToString(Formatting.Indented));
            UnityEngine.Debug.Log("ROBOVISION_HEALTH " + findings.ToString(Formatting.None));
            EditorApplication.Exit(passed ? 0 : 1);
        }

        /// <summary>Run the Python gate, pumping the transport while it talks.</summary>
        /// <remarks>
        /// -executeMethod holds the editor loop for the whole call, so nothing
        /// would ever answer the external client while it waits. The host is
        /// serviced explicitly rather than by sleeping and hoping.
        /// </remarks>
        private static string DriveGate(string repoRoot, string gate, int port, out JObject report)
        {
            report = null;
            var reportPath = Path.Combine(Path.GetTempPath(), "robovision-health-gate.json");
            if (File.Exists(reportPath)) File.Delete(reportPath);

            var info = new ProcessStartInfo
            {
                FileName = Environment.GetEnvironmentVariable("ROBOVISION_PYTHON") ?? "python",
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                WorkingDirectory = repoRoot
            };
            info.ArgumentList.Add(gate);
            info.ArgumentList.Add(repoRoot);
            info.ArgumentList.Add(port.ToString());
            info.ArgumentList.Add(reportPath);

            using (var process = Process.Start(info))
            {
                var deadline = DateTime.UtcNow.AddSeconds(180);
                while (!process.HasExited && DateTime.UtcNow < deadline)
                {
                    RoboVisionHost.Instance.ServiceTransportOnce();
                    System.Threading.Thread.Sleep(2);
                }
                if (!process.HasExited)
                {
                    process.Kill();
                    return "the python gate timed out";
                }
                var stdout = process.StandardOutput.ReadToEnd();
                var stderr = process.StandardError.ReadToEnd();
                if (File.Exists(reportPath))
                {
                    try { report = JObject.Parse(File.ReadAllText(reportPath)); }
                    catch (JsonException ex) { return "unreadable gate report: " + ex.Message; }
                }
                return "exit " + process.ExitCode + " " + Trim(stdout) + Trim(stderr);
            }
        }

        private static string Trim(string text)
        {
            if (String.IsNullOrEmpty(text)) return "";
            text = text.Replace("\r", " ").Replace("\n", " ");
            return text.Length > 600 ? text.Substring(0, 600) : text;
        }
    }
}
