using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// What happens when two agents use one editor at the same time.
    /// </summary>
    /// <remarks>
    /// The concurrency policy is deliberate, not accidental:
    ///
    /// Optimistic concurrency is per-request. A client passes the revision it
    /// planned against and the host refuses the mutation if the scene has moved,
    /// whoever moved it.
    ///
    /// A transaction belongs to the connection that opened it. Another
    /// connection's mutation would otherwise join that transaction invisibly and
    /// be rolled back with it, so it is refused with TRANSACTION_FOREIGN. Reads
    /// stay available to everyone. If the owner disconnects the transaction is
    /// marked orphaned rather than silently committed or discarded, and only the
    /// client that can prove it opened it — with the recovery token issued at
    /// begin — may take it back.
    ///
    /// One connection's bad behaviour must not affect another's.
    /// </remarks>
    public sealed class RoboVisionConcurrencyTests
    {
        private int _port;

        private sealed class Client : IDisposable
        {
            private readonly TcpClient _tcp = new TcpClient();
            private readonly NetworkStream _stream;
            private int _serial;

            public Client(int port, string label)
            {
                Label = label;
                _tcp.Connect(IPAddress.Loopback, port);
                _tcp.ReceiveTimeout = 30000;
                _stream = _tcp.GetStream();
            }

            public string Label { get; }

            public JObject Call(string method, JObject parameters = null, long? ifRevision = null)
            {
                _serial++;
                var request = new JObject
                {
                    ["rv"] = "1.0",
                    ["id"] = Label + "-" + _serial,
                    ["method"] = method,
                    ["params"] = parameters ?? new JObject()
                };
                if (ifRevision.HasValue) request["if_revision"] = ifRevision.Value;
                var payload = Encoding.UTF8.GetBytes(request.ToString(Newtonsoft.Json.Formatting.None) + "\n");
                _stream.Write(payload, 0, payload.Length);
                _stream.Flush();
                return ReadLine();
            }

            public void SendRaw(string text)
            {
                var payload = Encoding.UTF8.GetBytes(text);
                _stream.Write(payload, 0, payload.Length);
                _stream.Flush();
            }

            // Leftover bytes must survive between reads. A pipelining client that
            // returned at the first newline and dropped the rest of the chunk
            // would lose every reply sharing a TCP segment with it, then block
            // forever waiting for data the host had already sent.
            private readonly List<byte> _inbound = new List<byte>();

            public JObject ReadLine()
            {
                var chunk = new byte[4096];
                while (true)
                {
                    var newline = _inbound.IndexOf((byte)'\n');
                    if (newline >= 0)
                    {
                        var line = Encoding.UTF8.GetString(_inbound.GetRange(0, newline).ToArray());
                        _inbound.RemoveRange(0, newline + 1);
                        if (line.Trim().Length == 0) continue;
                        return JObject.Parse(line);
                    }
                    var read = _stream.Read(chunk, 0, chunk.Length);
                    if (read <= 0) throw new IOException("host closed the connection");
                    for (var i = 0; i < read; i++) _inbound.Add(chunk[i]);
                }
            }

            public void Dispose()
            {
                try { _stream.Dispose(); } catch { }
                try { _tcp.Close(); } catch { }
            }
        }

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
            EnsureHost(restart: true);
        }

        /// <summary>
        /// Guarantee a listening host and a valid port for this fixture.
        /// </summary>
        /// <remarks>
        /// A domain reload can bring the fixture back with its fields at their
        /// defaults and without re-running SetUp, and connecting to port 0 fails
        /// with an argument error that says nothing about the actual contract.
        /// </remarks>
        private void EnsureHost(bool restart)
        {
            if (restart && RoboVisionHost.Instance.Running) RoboVisionHost.Instance.Stop();
            if (_port == 0 || restart) _port = FreePort();
            if (!RoboVisionHost.Instance.Running) RoboVisionHost.Instance.Start(_port);
        }

        [TearDown]
        public void TearDown() => RoboVisionHost.Instance.Stop();

        /// <summary>Run socket work off the main thread while the editor pumps.</summary>
        private static IEnumerator OffThread(Action work, List<string> failures, float timeoutSeconds = 90f)
        {
            var thread = new Thread(() =>
            {
                try { work(); }
                catch (Exception ex) { failures.Add(ex.GetType().Name + ": " + ex.Message); }
            }) { IsBackground = true };
            thread.Start();

            var deadline = DateTime.UtcNow.AddSeconds(timeoutSeconds);
            while (thread.IsAlive)
            {
                if (DateTime.UtcNow > deadline)
                {
                    failures.Add("timed out waiting for the concurrency worker");
                    yield break;
                }
                // Service the socket from the editor thread directly. Relying on
                // how often EditorApplication.update happens to fire between
                // yields makes a concurrency test measure the editor's tick rate
                // rather than the host's behaviour.
                RoboVisionHost.Instance.ServiceTransportOnce();
                yield return null;
            }
        }

        [UnityTest]
        public IEnumerator InterleavedMutationsAreArbitratedByRevision()
        {
            var failures = new List<string>();
            var findings = new Dictionary<string, string>();

            yield return OffThread(() =>
            {
                using (var a = new Client(_port, "clientA"))
                using (var b = new Client(_port, "clientB"))
                {
                    // Both clients observe the same revision, then A moves first.
                    var seen = a.Call("scene.describe")["result"].Value<long>("revision");
                    var created = a.Call("object.create", new JObject { ["name"] = "Contended" });
                    findings["a_created"] = created.Value<bool>("ok").ToString();
                    var id = created["result"].Value<string>("id");

                    // B plans against the revision it saw before A's change.
                    var stale = b.Call("object.transform", new JObject
                    {
                        ["object"] = id,
                        ["local_position"] = new JArray(9f, 9f, 9f)
                    }, ifRevision: seen);
                    findings["b_ok"] = stale.Value<bool>("ok").ToString();
                    findings["b_code"] = stale["error"]?.Value<string>("code");

                    // After re-observing, B's mutation is accepted.
                    var current = b.Call("scene.describe")["result"].Value<long>("revision");
                    var retried = b.Call("object.transform", new JObject
                    {
                        ["object"] = id,
                        ["local_position"] = new JArray(9f, 9f, 9f)
                    }, ifRevision: current);
                    findings["b_retry_ok"] = retried.Value<bool>("ok").ToString();
                }
            }, failures);

            Assert.That(failures, Is.Empty, string.Join("; ", failures));
            Assert.That(findings["a_created"], Is.EqualTo("True"));
            Assert.That(findings["b_ok"], Is.EqualTo("False"), "a mutation planned against a superseded revision was accepted");
            Assert.That(findings["b_code"], Is.EqualTo("STALE_REVISION"));
            Assert.That(findings["b_retry_ok"], Is.EqualTo("True"), "re-observing did not let the client proceed");
            Assert.That(GameObject.Find("Contended").transform.localPosition.x, Is.EqualTo(9f).Within(1e-4f));
        }

        [UnityTest]
        public IEnumerator ATransactionBelongsToTheConnectionThatOpenedIt()
        {
            var failures = new List<string>();
            var findings = new Dictionary<string, string>();

            yield return OffThread(() =>
            {
                using (var owner = new Client(_port, "owner"))
                using (var other = new Client(_port, "other"))
                {
                    var begun = owner.Call("transaction.begin", new JObject { ["label"] = "owned" });
                    var tx = begun["result"].Value<string>("transaction");

                    // The other connection may still read.
                    findings["other_read_ok"] = other.Call("scene.describe").Value<bool>("ok").ToString();

                    // But its mutation must not join a transaction it does not own.
                    var foreign = other.Call("object.create", new JObject { ["name"] = "Intruder" });
                    findings["other_mutate_ok"] = foreign.Value<bool>("ok").ToString();
                    findings["other_mutate_code"] = foreign["error"]?.Value<string>("code");

                    // The owner works normally throughout.
                    findings["owner_mutate_ok"] =
                        owner.Call("object.create", new JObject { ["name"] = "Owned" }).Value<bool>("ok").ToString();

                    owner.Call("transaction.commit", new JObject { ["transaction"] = tx });

                    // Once released, the other connection proceeds.
                    findings["other_after_commit"] =
                        other.Call("object.create", new JObject { ["name"] = "Allowed" }).Value<bool>("ok").ToString();
                }
            }, failures);

            Assert.That(failures, Is.Empty, string.Join("; ", failures));
            Assert.That(findings["other_read_ok"], Is.EqualTo("True"), "reads must stay available during a transaction");
            Assert.That(findings["other_mutate_ok"], Is.EqualTo("False"),
                "a second client silently joined a transaction it did not open");
            Assert.That(findings["other_mutate_code"], Is.EqualTo("TRANSACTION_FOREIGN"));
            Assert.That(findings["owner_mutate_ok"], Is.EqualTo("True"));
            Assert.That(findings["other_after_commit"], Is.EqualTo("True"));
            Assert.That(GameObject.Find("Intruder"), Is.Null, "the refused mutation still created its object");
        }

        /// <summary>
        /// An orphan is visible to everyone and usable by nobody without proof.
        /// </summary>
        /// <remarks>
        /// This replaces a weaker rule that this test used to assert: any client
        /// could commit or roll back an orphaned transaction, so that the editor
        /// would not stay wedged mid-edit. Convenient, and wrong — it treated a
        /// dropped TCP connection as authorization to finish someone else's
        /// half-written edit. The editor still does not stay wedged, because the
        /// owner holds a token that reclaims it and anyone can discard it; what
        /// is gone is the ability to *finish* it without proving anything.
        ///
        /// Over real sockets, because none of this can be observed in-process:
        /// every in-process call looks like the same local client, so ownership
        /// is exactly the thing an in-process test cannot see.
        /// </remarks>
        [UnityTest]
        public IEnumerator AnOrphanedTransactionRefusesEveryoneWithoutTheRecoveryToken()
        {
            var failures = new List<string>();
            var findings = new Dictionary<string, string>();

            yield return OffThread(() =>
            {
                string tx;
                using (var owner = new Client(_port, "owner"))
                {
                    var begun = owner.Call("transaction.begin", new JObject { ["label"] = "abandoned" })["result"];
                    tx = begun.Value<string>("transaction");
                    findings["token_issued"] =
                        (!String.IsNullOrEmpty(begun.Value<string>("recovery_token"))).ToString();
                    findings["id_is_world_scoped"] = tx.StartsWith("rvtx:").ToString();
                    owner.Call("object.create", new JObject { ["name"] = "Abandoned" });
                }
                // The owning connection is gone; give the host a moment to notice.
                Thread.Sleep(200);

                using (var stranger = new Client(_port, "stranger"))
                {
                    var hello = stranger.Call("system.hello")["result"];
                    var state = hello["transaction"]?["state"];
                    findings["reported_active"] = hello["transaction"]?.Value<bool>("active").ToString();
                    findings["reported_orphaned"] = state?.Value<bool>("owner_disconnected").ToString();
                    findings["adoption_required"] = state?.Value<bool>("adoption_required").ToString();
                    // The credential must not be anywhere a diagnostic reaches.
                    findings["hello_leaks_token"] =
                        hello.ToString(Newtonsoft.Json.Formatting.None).Contains("recovery_token").ToString();

                    findings["reads_ok"] = stranger.Call("scene.describe").Value<bool>("ok").ToString();
                    findings["mutate_code"] = stranger
                        .Call("object.create", new JObject { ["name"] = "Intruder" })["error"]
                        ?.Value<string>("code");
                    findings["commit_code"] = stranger
                        .Call("transaction.commit", new JObject { ["transaction"] = tx })["error"]
                        ?.Value<string>("code");
                    findings["rollback_code"] = stranger
                        .Call("transaction.rollback", new JObject { ["transaction"] = tx })["error"]
                        ?.Value<string>("code");

                    var beforeGuess = stranger.Call("scene.describe")["result"].Value<long>("revision");
                    var guessed = stranger.Call("transaction.adopt", new JObject
                    {
                        ["transaction"] = tx,
                        ["recovery_token"] = "not-the-token"
                    });
                    findings["guess_code"] = guessed["error"]?.Value<string>("code");
                    // A refused adoption is not a move: nothing about the
                    // transaction or the scene may have changed.
                    var after = stranger.Call("system.hello")["result"]["transaction"]["state"];
                    findings["still_orphaned"] = after?.Value<bool>("owner_disconnected").ToString();
                    findings["revision_unmoved"] =
                        (stranger.Call("scene.describe")["result"].Value<long>("revision") == beforeGuess)
                        .ToString();
                }
            }, failures);

            Assert.That(failures, Is.Empty, string.Join("; ", failures));
            Assert.That(findings["token_issued"], Is.EqualTo("True"), "begin issued no recovery token");
            Assert.That(findings["id_is_world_scoped"], Is.EqualTo("True"),
                "the transaction id is not scoped to the world that issued it");
            Assert.That(findings["reported_active"], Is.EqualTo("True"), "the orphaned transaction was not reported");
            Assert.That(findings["reported_orphaned"], Is.EqualTo("True"),
                "system.hello did not say the transaction's owner had disconnected");
            Assert.That(findings["adoption_required"], Is.EqualTo("True"));
            Assert.That(findings["hello_leaks_token"], Is.EqualTo("False"),
                "system.hello exposed the recovery credential");
            Assert.That(findings["reads_ok"], Is.EqualTo("True"), "reads must stay available");
            Assert.That(findings["mutate_code"], Is.EqualTo("TRANSACTION_ORPHANED"),
                "a stranger continued someone else's orphaned transaction");
            Assert.That(findings["commit_code"], Is.EqualTo("TRANSACTION_ORPHANED"),
                "a stranger committed someone else's half-written edit");
            Assert.That(findings["rollback_code"], Is.EqualTo("TRANSACTION_ORPHANED"),
                "a stranger destroyed someone else's work by rolling it back");
            Assert.That(findings["guess_code"], Is.EqualTo("TRANSACTION_ADOPTION_REFUSED"));
            Assert.That(findings["still_orphaned"], Is.EqualTo("True"),
                "a failed adoption changed the transaction's state");
            Assert.That(findings["revision_unmoved"], Is.EqualTo("True"),
                "a failed adoption moved the scene revision");
            Assert.That(GameObject.Find("Intruder"), Is.Null, "the refused mutation still created its object");
        }

        /// <summary>
        /// The token reclaims the transaction, and then it is not that token any more.
        /// </summary>
        /// <remarks>
        /// Rotation is what stops a leaked token being a permanent key to
        /// whatever transaction is open. The replacement goes only to the client
        /// that just proved itself, in the adoption response, and the previous
        /// one is asserted dead rather than assumed to be.
        /// </remarks>
        [UnityTest]
        public IEnumerator TheRecoveryTokenAdoptsAnOrphanAndIsThenRotated()
        {
            var failures = new List<string>();
            var findings = new Dictionary<string, string>();

            yield return OffThread(() =>
            {
                string tx, token, rotated;
                using (var owner = new Client(_port, "owner"))
                {
                    var begun = owner.Call("transaction.begin", new JObject { ["label"] = "reclaimed" })["result"];
                    tx = begun.Value<string>("transaction");
                    token = begun.Value<string>("recovery_token");
                    owner.Call("object.create", new JObject { ["name"] = "InsideReclaimed" });
                }
                Thread.Sleep(200);

                using (var heir = new Client(_port, "heir"))
                {
                    // A valid token for a transaction that is not the one open.
                    findings["wrong_transaction_code"] = heir.Call("transaction.adopt", new JObject
                    {
                        ["transaction"] = "rvtx:0badc0de:" + new string('0', 32),
                        ["recovery_token"] = token
                    })["error"]?.Value<string>("code");

                    var adopted = heir.Call("transaction.adopt", new JObject
                    {
                        ["transaction"] = tx,
                        ["recovery_token"] = token
                    });
                    findings["adopt_ok"] = adopted.Value<bool>("ok").ToString();
                    rotated = adopted["result"]?.Value<string>("recovery_token");
                    findings["rotated"] = (rotated != null && rotated != token).ToString();
                    findings["state_after_adopt"] = adopted["result"]?.Value<string>("state");

                    // The adopter is the owner now, in both directions.
                    findings["adopter_mutate_ok"] = heir
                        .Call("object.create", new JObject { ["name"] = "ByHeir" }).Value<bool>("ok").ToString();
                    findings["rollback_ok"] = heir
                        .Call("transaction.rollback", new JObject { ["transaction"] = tx })
                        .Value<bool>("ok").ToString();
                }

                // And a finished transaction is finished, not merely absent.
                using (var late = new Client(_port, "late"))
                {
                    findings["late_commit_code"] = late
                        .Call("transaction.commit", new JObject { ["transaction"] = tx })["error"]
                        ?.Value<string>("code");
                }

                // Rotation, proven by orphaning a second transaction and trying
                // the token that the first adoption retired.
                using (var owner = new Client(_port, "owner2"))
                {
                    var begun = owner.Call("transaction.begin", new JObject { ["label"] = "second" })["result"];
                    tx = begun.Value<string>("transaction");
                    token = begun.Value<string>("recovery_token");
                    owner.Call("object.create", new JObject { ["name"] = "SecondInside" });
                }
                Thread.Sleep(200);
                using (var heir = new Client(_port, "heir2"))
                {
                    findings["stale_token_code"] = heir.Call("transaction.adopt", new JObject
                    {
                        ["transaction"] = tx,
                        ["recovery_token"] = rotated
                    })["error"]?.Value<string>("code");
                    var second = heir.Call("transaction.adopt", new JObject
                    {
                        ["transaction"] = tx,
                        ["recovery_token"] = token
                    });
                    findings["second_adopt_ok"] = second.Value<bool>("ok").ToString();
                    heir.Call("transaction.rollback", new JObject { ["transaction"] = tx });
                }
            }, failures);

            Assert.That(failures, Is.Empty, string.Join("; ", failures));
            Assert.That(findings["wrong_transaction_code"], Is.EqualTo("TRANSACTION_ADOPTION_REFUSED"),
                "a token adopted a transaction it was not issued for");
            Assert.That(findings["adopt_ok"], Is.EqualTo("True"), "the correct token did not adopt the orphan");
            Assert.That(findings["rotated"], Is.EqualTo("True"),
                "adoption did not rotate the recovery token");
            Assert.That(findings["state_after_adopt"], Is.EqualTo("active"));
            Assert.That(findings["adopter_mutate_ok"], Is.EqualTo("True"),
                "the adopter could not continue the transaction it now owns");
            Assert.That(findings["rollback_ok"], Is.EqualTo("True"),
                "the adopter could not roll back the transaction it now owns");
            Assert.That(findings["late_commit_code"], Is.EqualTo("TRANSACTION_FINISHED"),
                "a finished transaction was reported as never having existed");
            Assert.That(findings["stale_token_code"], Is.EqualTo("TRANSACTION_ADOPTION_REFUSED"),
                "a token retired by an earlier adoption still worked");
            Assert.That(findings["second_adopt_ok"], Is.EqualTo("True"));
            Assert.That(GameObject.Find("InsideReclaimed"), Is.Null,
                "the adopted transaction's work was not rolled back");
        }

        [UnityTest]
        public IEnumerator OneClientsMisbehaviourDoesNotPoisonAnother()
        {
            var failures = new List<string>();
            var findings = new Dictionary<string, string>();

            yield return OffThread(() =>
            {
                using (var good = new Client(_port, "good"))
                {
                    findings["before"] = good.Call("system.ping").Value<bool>("ok").ToString();

                    using (var bad = new Client(_port, "bad"))
                    {
                        bad.SendRaw("{this is not json}\n");
                        var refusal = bad.ReadLine();
                        findings["bad_code"] = refusal["error"]?.Value<string>("code");
                    }

                    // A client that vanishes mid-request must not disturb others.
                    var abrupt = new Client(_port, "abrupt");
                    abrupt.SendRaw("{\"rv\":\"1.0\",\"id\":\"half\",\"method\":\"system.pi");
                    abrupt.Dispose();

                    findings["after"] = good.Call("system.ping").Value<bool>("ok").ToString();
                    findings["after_id"] = good.Call("system.hello").Value<string>("id");
                }
            }, failures);

            Assert.That(failures, Is.Empty, string.Join("; ", failures));
            Assert.That(findings["before"], Is.EqualTo("True"));
            Assert.That(findings["bad_code"], Is.EqualTo("INVALID_REQUEST"));
            Assert.That(findings["after"], Is.EqualTo("True"),
                "a healthy connection stopped working after another client misbehaved");
            Assert.That(findings["after_id"], Is.Not.Null.And.Not.Empty,
                "responses drifted to the wrong connection after a partial request");
        }

        [UnityTest]
        public IEnumerator OneClientCanPipelineManyRequests()
        {
            var failures = new List<string>();
            var findings = new Dictionary<string, string>();

            yield return OffThread(() =>
            {
                using (var solo = new Client(_port, "solo"))
                {
                    var payload = new StringBuilder();
                    for (var i = 0; i < 8; i++)
                        payload.Append("{\"rv\":\"1.0\",\"id\":\"solo-" + i + "\",\"method\":\"system.ping\",\"params\":{}}\n");
                    solo.SendRaw(payload.ToString());

                    var read = 0;
                    var inOrder = true;
                    try
                    {
                        for (var i = 0; i < 8; i++)
                        {
                            if (solo.ReadLine().Value<string>("id") != "solo-" + i) inOrder = false;
                            read++;
                        }
                    }
                    finally
                    {
                        findings["read"] = read.ToString();
                        findings["in_order"] = inOrder.ToString();
                    }
                }
            }, failures);

            var progress = " (read=" + (findings.ContainsKey("read") ? findings["read"] : "?") + " of 8)";
            Assert.That(failures, Is.Empty, string.Join("; ", failures) + progress);
            Assert.That(findings["read"], Is.EqualTo("8"), "the host did not answer every pipelined request" + progress);
            Assert.That(findings["in_order"], Is.EqualTo("True"), "pipelined replies arrived out of order");
        }

        [UnityTest]
        public IEnumerator PipelinedRequestsFromTwoClientsStayOnTheirOwnConnections()
        {
            var failures = new List<string>();
            var findings = new Dictionary<string, string>();

            yield return OffThread(() =>
            {
                using (var a = new Client(_port, "pipeA"))
                using (var b = new Client(_port, "pipeB"))
                {
                    // Queue several requests before reading any reply, on both
                    // connections, so the host has interleaved work outstanding.
                    var payload = new StringBuilder();
                    for (var i = 0; i < 8; i++)
                        payload.Append("{\"rv\":\"1.0\",\"id\":\"pipeA-" + i + "\",\"method\":\"system.ping\",\"params\":{}}\n");
                    a.SendRaw(payload.ToString());

                    payload.Clear();
                    for (var i = 0; i < 8; i++)
                        payload.Append("{\"rv\":\"1.0\",\"id\":\"pipeB-" + i + "\",\"method\":\"system.ping\",\"params\":{}}\n");
                    b.SendRaw(payload.ToString());

                    // Drain each connection fully and record how far it got, so a
                    // stall says which client stopped and after how many replies
                    // instead of only that something timed out.
                    var aOk = true;
                    var bOk = true;
                    var aRead = 0;
                    var bRead = 0;
                    try
                    {
                        for (var i = 0; i < 8; i++)
                        {
                            if (a.ReadLine().Value<string>("id") != "pipeA-" + i) aOk = false;
                            aRead++;
                        }
                        for (var i = 0; i < 8; i++)
                        {
                            if (b.ReadLine().Value<string>("id") != "pipeB-" + i) bOk = false;
                            bRead++;
                        }
                    }
                    finally
                    {
                        findings["a_read"] = aRead.ToString();
                        findings["b_read"] = bRead.ToString();
                        findings["a_in_order"] = aOk.ToString();
                        findings["b_in_order"] = bOk.ToString();
                    }
                }
            }, failures);

            var progress = " (a_read=" + findings.GetValueOrDefault("a_read", "?")
                + " b_read=" + findings.GetValueOrDefault("b_read", "?") + ")";
            Assert.That(failures, Is.Empty, string.Join("; ", failures) + progress);
            Assert.That(findings["a_read"], Is.EqualTo("8"), "client A did not receive every reply" + progress);
            Assert.That(findings["b_read"], Is.EqualTo("8"), "client B did not receive every reply" + progress);
            Assert.That(findings["a_in_order"], Is.EqualTo("True"),
                "responses arrived out of order or on the wrong connection" + progress);
            Assert.That(findings["b_in_order"], Is.EqualTo("True"),
                "responses arrived out of order or on the wrong connection" + progress);
        }

        // Reconnecting after a domain reload is asserted in
        // RoboVisionLifecycleTests.TransportComesBackServingAfterADomainReload,
        // where the socket work happens entirely after the reload. A version
        // here that held clients and collections across the boundary failed
        // inside the test framework's own resumption rather than in the host,
        // and the stronger claim — the shipped Python client connecting to a
        // brand new process — is covered by tools/unity-restart-gate.sh.
    }
}
