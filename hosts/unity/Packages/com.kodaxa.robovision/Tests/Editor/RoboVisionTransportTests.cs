using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEngine.TestTools;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// The socket path, exercised the way an external agent uses it.
    /// </summary>
    /// <remarks>
    /// Every other test calls Dispatch directly, which skips the listener, the
    /// newline framing and the size limits entirely. These tests connect over
    /// TCP instead.
    ///
    /// The editor-thread rule is upheld the same way the Blender gate does it:
    /// socket work happens on a worker thread while the test yields, so the
    /// editor loop keeps pumping <c>Poll</c> on the main thread. Blocking the
    /// test thread on a socket read would deadlock, because that thread is the
    /// one that has to answer.
    /// </remarks>
    public sealed class RoboVisionTransportTests
    {
        private int _port;
        private bool _wasRunning;

        private static int FreePort()
        {
            var probe = new TcpListener(IPAddress.Loopback, 0);
            probe.Start();
            var port = ((IPEndPoint)probe.LocalEndpoint).Port;
            probe.Stop();
            return port;
        }

        [SetUp]
        public void SetUp()
        {
            Harness.FreshScene();
            _wasRunning = RoboVisionHost.Instance.Running;
            if (_wasRunning) RoboVisionHost.Instance.Stop();
            _port = FreePort();
            RoboVisionHost.Instance.Start(_port);
            Assert.That(RoboVisionHost.Instance.Running, Is.True, "the host did not start listening");
        }

        [TearDown]
        public void TearDown()
        {
            RoboVisionHost.Instance.Stop();
        }

        /// <summary>Run socket work off the main thread while the editor pumps.</summary>
        private static IEnumerator RunOffThread(Action work, List<string> failures, float timeoutSeconds = 60f)
        {
            var thread = new Thread(() =>
            {
                try { work(); }
                catch (Exception ex) { failures.Add(ex.GetType().Name + ": " + ex.Message); }
            });
            thread.IsBackground = true;
            thread.Start();

            var deadline = DateTime.UtcNow.AddSeconds(timeoutSeconds);
            while (thread.IsAlive)
            {
                if (DateTime.UtcNow > deadline)
                {
                    failures.Add("timed out waiting for the transport worker");
                    yield break;
                }
                yield return null;
            }
        }

        private static JObject Exchange(NetworkStream stream, JObject request)
        {
            var payload = Encoding.UTF8.GetBytes(request.ToString(Newtonsoft.Json.Formatting.None) + "\n");
            stream.Write(payload, 0, payload.Length);
            stream.Flush();

            var buffer = new List<byte>();
            var chunk = new byte[4096];
            while (true)
            {
                var read = stream.Read(chunk, 0, chunk.Length);
                if (read <= 0) throw new IOException("host closed the connection");
                for (var i = 0; i < read; i++)
                {
                    if (chunk[i] == (byte)'\n')
                        return JObject.Parse(Encoding.UTF8.GetString(buffer.ToArray()));
                    buffer.Add(chunk[i]);
                }
            }
        }

        [UnityTest]
        public IEnumerator HostAnswersOverTcpWithFramedResponses()
        {
            var failures = new List<string>();
            var findings = new Dictionary<string, string>();

            yield return RunOffThread(() =>
            {
                using (var client = new TcpClient())
                {
                    client.Connect(IPAddress.Loopback, _port);
                    client.ReceiveTimeout = 30000;
                    using (var stream = client.GetStream())
                    {
                        var hello = Exchange(stream, new JObject
                        {
                            ["rv"] = "1.0", ["id"] = "wire-1", ["method"] = "system.hello", ["params"] = new JObject()
                        });
                        findings["ok"] = hello.Value<bool>("ok").ToString();
                        findings["id"] = hello.Value<string>("id");
                        findings["editor"] = hello["result"]?["editor"]?.Value<string>("name");
                        findings["port"] = hello["result"]?["transport"]?.Value<string>("port");

                        var created = Exchange(stream, new JObject
                        {
                            ["rv"] = "1.0", ["id"] = "wire-2", ["method"] = "object.create",
                            ["params"] = new JObject { ["name"] = "OverTheWire" }
                        });
                        findings["created_ok"] = created.Value<bool>("ok").ToString();
                        findings["created_id"] = created["result"]?.Value<string>("id");

                        // Several calls on one connection: framing must not drift.
                        for (var i = 0; i < 20; i++)
                        {
                            var ping = Exchange(stream, new JObject
                            {
                                ["rv"] = "1.0", ["id"] = "wire-ping-" + i, ["method"] = "system.ping",
                                ["params"] = new JObject()
                            });
                            if (ping.Value<string>("id") != "wire-ping-" + i)
                                failures.Add("framing drifted at ping " + i + ": " + ping.Value<string>("id"));
                        }

                        var missing = Exchange(stream, new JObject
                        {
                            ["rv"] = "1.0", ["id"] = "wire-3", ["method"] = "object.inspect",
                            ["params"] = new JObject { ["object"] = "unity:session:999999" }
                        });
                        findings["error_code"] = missing["error"]?.Value<string>("code");
                    }
                }
            }, failures);

            Assert.That(failures, Is.Empty, string.Join("; ", failures));
            Assert.That(findings["ok"], Is.EqualTo("True"));
            Assert.That(findings["id"], Is.EqualTo("wire-1"), "the response did not carry the request id");
            Assert.That(findings["editor"], Is.EqualTo("Unity"));
            Assert.That(findings["port"], Is.EqualTo(_port.ToString()));
            Assert.That(findings["created_ok"], Is.EqualTo("True"));
            Assert.That(findings["created_id"], Does.StartWith("unity:session:"));
            Assert.That(findings["error_code"], Is.EqualTo("NOT_FOUND"),
                "a typed host error did not survive the wire");
        }

        [UnityTest]
        public IEnumerator MalformedTrafficIsRefusedAndTheHostKeepsServing()
        {
            var failures = new List<string>();
            var findings = new Dictionary<string, string>();

            yield return RunOffThread(() =>
            {
                using (var client = new TcpClient())
                {
                    client.Connect(IPAddress.Loopback, _port);
                    client.ReceiveTimeout = 30000;
                    using (var stream = client.GetStream())
                    {
                        var junk = Encoding.UTF8.GetBytes("{this is not json}\n");
                        stream.Write(junk, 0, junk.Length);
                        stream.Flush();

                        var buffer = new List<byte>();
                        var chunk = new byte[4096];
                        while (true)
                        {
                            var read = stream.Read(chunk, 0, chunk.Length);
                            if (read <= 0) break;
                            var complete = false;
                            for (var i = 0; i < read; i++)
                            {
                                if (chunk[i] == (byte)'\n') { complete = true; break; }
                                buffer.Add(chunk[i]);
                            }
                            if (complete) break;
                        }
                        var response = JObject.Parse(Encoding.UTF8.GetString(buffer.ToArray()));
                        findings["malformed_code"] = response["error"]?.Value<string>("code");
                    }
                }

                // A fresh client must still be served afterwards.
                using (var client = new TcpClient())
                {
                    client.Connect(IPAddress.Loopback, _port);
                    client.ReceiveTimeout = 30000;
                    using (var stream = client.GetStream())
                    {
                        var ping = Exchange(stream, new JObject
                        {
                            ["rv"] = "1.0", ["id"] = "after-junk", ["method"] = "system.ping", ["params"] = new JObject()
                        });
                        findings["survived"] = ping["result"]?.Value<bool>("pong").ToString();
                    }
                }
            }, failures);

            Assert.That(failures, Is.Empty, string.Join("; ", failures));
            Assert.That(findings["malformed_code"], Is.EqualTo("INVALID_REQUEST"));
            Assert.That(findings["survived"], Is.EqualTo("True"), "the host stopped serving after malformed traffic");
        }

        [UnityTest]
        public IEnumerator TwoClientsAreServedIndependently()
        {
            var failures = new List<string>();
            var findings = new Dictionary<string, string>();

            yield return RunOffThread(() =>
            {
                using (var first = new TcpClient())
                using (var second = new TcpClient())
                {
                    first.Connect(IPAddress.Loopback, _port);
                    second.Connect(IPAddress.Loopback, _port);
                    first.ReceiveTimeout = 30000;
                    second.ReceiveTimeout = 30000;
                    using (var a = first.GetStream())
                    using (var b = second.GetStream())
                    {
                        var one = Exchange(a, new JObject
                        {
                            ["rv"] = "1.0", ["id"] = "client-a", ["method"] = "system.ping", ["params"] = new JObject()
                        });
                        var two = Exchange(b, new JObject
                        {
                            ["rv"] = "1.0", ["id"] = "client-b", ["method"] = "system.ping", ["params"] = new JObject()
                        });
                        findings["a"] = one.Value<string>("id");
                        findings["b"] = two.Value<string>("id");
                    }
                }
            }, failures);

            Assert.That(failures, Is.Empty, string.Join("; ", failures));
            Assert.That(findings["a"], Is.EqualTo("client-a"));
            Assert.That(findings["b"], Is.EqualTo("client-b"),
                "responses were delivered to the wrong client");
        }

        /// <summary>
        /// Drive the Unity host with the shipped Python client, not a test-local one.
        /// </summary>
        /// <remarks>
        /// robovision.client.RoboVisionClient is what an agent actually uses. A
        /// C# client proves the socket works; only the real client proves the
        /// protocol library and this host agree.
        /// </remarks>
        [UnityTest]
        public IEnumerator PythonRoboVisionClientCanDriveThisHost()
        {
            // Locate the repo from the installed package rather than from the
            // testbed's location, so the test does not depend on where the
            // throwaway project was generated.
            var package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(RoboVisionHost).Assembly);
            if (package == null || string.IsNullOrEmpty(package.resolvedPath))
                Assert.Ignore("package path unavailable; cannot locate the RoboVision python client");
            var repoRoot = Path.GetFullPath(Path.Combine(package.resolvedPath, "..", "..", "..", ".."));
            var clientModule = Path.Combine(repoRoot, "robovision", "client.py");
            if (!File.Exists(clientModule))
                Assert.Ignore("RoboVision Python package not found next to the testbed: " + clientModule);

            var python = Environment.GetEnvironmentVariable("ROBOVISION_PYTHON") ?? "python";
            var script =
                "import json,sys\n" +
                "sys.path.insert(0, sys.argv[1])\n" +
                "from robovision.client import RoboVisionClient\n" +
                "with RoboVisionClient('127.0.0.1', int(sys.argv[2]), timeout=30.0) as c:\n" +
                "    hello = c.call('system.hello')\n" +
                "    created = c.call('object.create', {'name': 'FromPython'})\n" +
                "    print('RESULT ' + json.dumps({\n" +
                "        'editor': hello['result']['editor']['name'],\n" +
                "        'protocol': hello['result']['protocol'],\n" +
                "        'id': created['result']['id'],\n" +
                "        'ok': created['ok'],\n" +
                "    }))\n";

            var scriptPath = Path.Combine(Path.GetTempPath(), "robovision_gate4_client.py");
            File.WriteAllText(scriptPath, script);

            var failures = new List<string>();
            string output = null;
            string stderr = null;
            var exitCode = -1;

            yield return RunOffThread(() =>
            {
                var info = new ProcessStartInfo
                {
                    FileName = python,
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true
                };
                info.ArgumentList.Add(scriptPath);
                info.ArgumentList.Add(repoRoot);
                info.ArgumentList.Add(_port.ToString());

                using (var process = Process.Start(info))
                {
                    output = process.StandardOutput.ReadToEnd();
                    stderr = process.StandardError.ReadToEnd();
                    process.WaitForExit(60000);
                    exitCode = process.HasExited ? process.ExitCode : -1;
                }
            }, failures, 90f);

            if (failures.Count > 0 && failures[0].StartsWith("Win32Exception"))
                Assert.Ignore("python is not available on PATH: " + failures[0]);
            Assert.That(failures, Is.Empty, string.Join("; ", failures));
            Assert.That(exitCode, Is.EqualTo(0), "python client failed: " + stderr);

            var marker = output.IndexOf("RESULT ", StringComparison.Ordinal);
            Assert.That(marker, Is.GreaterThanOrEqualTo(0), "no result from the python client. stdout=" + output + " stderr=" + stderr);
            var payload = JObject.Parse(output.Substring(marker + "RESULT ".Length).Trim());

            Assert.That(payload.Value<string>("editor"), Is.EqualTo("Unity"));
            Assert.That(payload.Value<string>("protocol"), Is.EqualTo("1.0"));
            Assert.That(payload.Value<bool>("ok"), Is.True);
            Assert.That(payload.Value<string>("id"), Does.StartWith("unity:session:"));
            Assert.That(UnityEngine.GameObject.Find("FromPython"), Is.Not.Null,
                "the python client's object.create did not reach the editor");
        }
    }
}
