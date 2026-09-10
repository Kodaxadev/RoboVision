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
    /// What survives an acknowledgement that never arrives, proved through the public client.
    /// </summary>
    /// <remarks>
    /// Every recovery guarantee this project makes is about one window: the host
    /// has applied a request and its response has not reached the caller.
    /// Reaching that state by editing private state afterwards would assert the
    /// edit rather than the behaviour, so the transport has two deliberate seams
    /// — drop a response after the handler ran, or drop a request before it does
    /// — and this gate arms them from outside over the real socket.
    ///
    /// The driver is the Python <c>HostSession</c>, unchanged, the same object
    /// the MCP adapter holds. That is the point of running it here rather than
    /// writing a C# equivalent: the public client is meant to be host-agnostic,
    /// and this is where that claim is either true against Unity or it is not.
    ///
    /// The arming tool is defined in this assembly, which a consuming project
    /// never compiles: a shipped editor has no way to make the host stop
    /// answering.
    /// </remarks>
    public static class RoboVisionAckLossGate
    {
        private static string ReportPath =>
            Environment.GetEnvironmentVariable("ROBOVISION_ACKLOSS_REPORT")
            ?? Path.Combine(Path.GetTempPath(), "robovision-unity-ackloss.json");

        private static int FreePort()
        {
            var probe = new TcpListener(IPAddress.Loopback, 0);
            probe.Start();
            var port = ((IPEndPoint)probe.LocalEndpoint).Port;
            probe.Stop();
            return port;
        }

        /// <summary>Arm one deliberate loss, from the client that is about to suffer it.</summary>
        /// <remarks>
        /// Independent of scene state and never mutating, so arming a fault
        /// cannot itself be the thing that changes what the next call sees.
        /// </remarks>
        private static void RegisterFaultTool()
        {
            if (RoboVisionHost.Instance.HasTool("test.fault")) return;
            RoboVisionHost.Instance.AddTool("test.fault", parameters =>
                {
                    var transport = RoboVisionHost.Instance.Transport;
                    transport.FaultAfterDispatch = null;
                    transport.FaultBeforeDispatch = null;
                    var target = parameters.Value<string>("method");
                    if (String.IsNullOrEmpty(target)) return new JObject { ["armed"] = false };

                    var used = false;
                    if (parameters.Value<string>("when") == "before")
                    {
                        transport.FaultBeforeDispatch = request =>
                        {
                            if (used || request.Value<string>("method") != target) return true;
                            used = true;
                            return false;
                        };
                    }
                    else
                    {
                        transport.FaultAfterDispatch = (request, _) =>
                        {
                            if (used || request.Value<string>("method") != target) return true;
                            used = true;
                            return false;
                        };
                    }
                    return new JObject { ["armed"] = true, ["method"] = target };
                },
                reads: RoboVisionHost.ReadsIndependent,
                stability: "internal",
                summary: "Test-only: lose exactly one delivery of a method.");
        }

        public static void Run()
        {
            var findings = new JObject();
            var port = FreePort();
            try
            {
                RegisterFaultTool();
                RoboVisionHost.Instance.Start(port);
                findings["port"] = port;

                var package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(
                    typeof(RoboVisionHost).Assembly);
                if (package == null || String.IsNullOrEmpty(package.resolvedPath))
                    throw new InvalidOperationException("PackageInfo did not report the RoboVision package");
                var repoRoot = Path.GetFullPath(Path.Combine(package.resolvedPath, "..", "..", "..", ".."));
                var gate = Path.Combine(repoRoot, "tests", "unity", "ack_loss.py");
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
            UnityEngine.Debug.Log("ROBOVISION_ACKLOSS " + findings.ToString(Formatting.None));
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
            var reportPath = Path.Combine(Path.GetTempPath(), "robovision-ackloss-gate.json");
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
