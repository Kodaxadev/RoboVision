using System.Linq;
using NUnit.Framework;
using Newtonsoft.Json.Linq;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>Protocol substrate: discovery, validation and error shaping.</summary>
    public sealed class RoboVisionProtocolTests
    {
        private Harness _rv;

        [SetUp]
        public void SetUp()
        {
            Harness.FreshScene();
            _rv = new Harness("protocol");
        }

        [TearDown]
        public void TearDown() => _rv.ResetTransactions();

        [Test]
        public void HelloDescribesThisEditorAndItsLimits()
        {
            var hello = _rv.Result("system.hello");

            Assert.That(hello.Value<string>("protocol"), Is.EqualTo("1.0"));
            Assert.That(hello["host"].Value<string>("name"), Is.EqualTo("unity"));
            Assert.That(hello["editor"].Value<string>("name"), Is.EqualTo("Unity"));
            Assert.That(hello["editor"].Value<string>("version"), Is.Not.Null.And.Not.Empty);
            Assert.That(hello["security"].Value<bool>("loopback_only"), Is.True);
            Assert.That(hello["security"].Value<bool>("arbitrary_code_enabled"), Is.False,
                "arbitrary code execution must stay off by default");
        }

        [Test]
        public void CapabilitiesEnumerateTheLiveToolSurface()
        {
            var capabilities = _rv.Result("system.capabilities");
            var names = capabilities["methods"].Select(entry => entry.Value<string>("name")).ToList();

            Assert.That(names, Is.Not.Empty);
            Assert.That(names, Is.Unique);
            // The substrate the host contract requires of every editor.
            foreach (var required in new[]
            {
                "system.ping", "system.hello", "system.capabilities", "system.method",
                "scene.describe", "scene.snapshot", "scene.diff",
                "object.inspect", "object.create", "object.delete", "object.transform",
                "transaction.begin", "transaction.commit", "transaction.rollback"
            })
            {
                Assert.That(names, Contains.Item(required), "missing required method " + required);
            }
        }

        [Test]
        public void CapabilitiesCanBeFilteredByPrefix()
        {
            var scoped = _rv.Result("system.capabilities", new JObject { ["prefix"] = "transaction." });
            var names = scoped["methods"].Select(entry => entry.Value<string>("name")).ToList();

            Assert.That(names, Is.Not.Empty);
            Assert.That(names.All(name => name.StartsWith("transaction.")), Is.True,
                "prefix filter returned unrelated methods: " + string.Join(", ", names));
        }

        [Test]
        public void MethodDescribesOneToolIncludingItsSafetyMetadata()
        {
            var described = _rv.Result("system.method", new JObject { ["method"] = "object.create" });

            Assert.That(described.Value<string>("name"), Is.EqualTo("object.create"));
            Assert.That(described.Value<bool>("mutating"), Is.True,
                "object.create must advertise that it mutates, or a client cannot reason about safety");
        }

        [Test]
        public void UnknownMethodIsRejectedWithATypedError()
        {
            _rv.Call("object.levitate", ok: false, code: "UNKNOWN_METHOD");
        }

        [Test]
        public void ProtocolMismatchIsRejected()
        {
            var response = _rv.Host.Dispatch(new JObject
            {
                ["rv"] = "0.9",
                ["id"] = "protocol-mismatch",
                ["method"] = "system.ping",
                ["params"] = new JObject()
            });
            Assert.That(response.Value<bool>("ok"), Is.False);
            Assert.That(response["error"].Value<string>("code"), Is.EqualTo("PROTOCOL_MISMATCH"));
        }

        [Test]
        public void MalformedRequestsAreRejectedBeforeReachingATool()
        {
            var response = _rv.Host.Dispatch(new JObject
            {
                ["rv"] = "1.0",
                ["method"] = "system.ping",
                ["params"] = new JObject()
            });
            Assert.That(response.Value<bool>("ok"), Is.False);
            Assert.That(response["error"].Value<string>("code"), Is.EqualTo("INVALID_REQUEST"));
        }

        [Test]
        public void ResponsesCarryRevisionAndTiming()
        {
            var response = _rv.Call("system.ping");
            Assert.That(response["revision"], Is.Not.Null);
            Assert.That(response["timing_ms"], Is.Not.Null, "responses must carry timing evidence");
        }

        [Test]
        public void MissingObjectIsNotFound()
        {
            _rv.Call("object.inspect",
                new JObject { ["object"] = "unity:session:999999" },
                ok: false, code: "NOT_FOUND");
        }

        /// <summary>
        /// The envelope is the same shape whether the call worked or not.
        /// </summary>
        /// <remarks>
        /// PROTOCOL.md said every response carries `state_domain` and
        /// `consistency`. Measured before this test existed: no error response
        /// carried either, on either host — the contract was true of the success
        /// path and written as if it were true of all of them. An audit rather
        /// than a spot check, because the failure mode is a field that quietly
        /// exists in only half the answers.
        /// </remarks>
        [Test]
        public void EveryResponseCarriesTheEnvelopeFieldsIncludingFailures()
        {
            var rv = new Harness("envelope");
            var id = rv.CreateObject("Envelope");
            var cases = new (string Method, JObject Params, long? Revision)[]
            {
                ("scene.describe", null, null),
                ("scene.changes_since", null, null),
                ("system.capabilities", null, null),
                ("nope.nope", null, null),
                ("object.transform", new JObject { ["object"] = "" }, null),
                ("object.transform", new JObject
                {
                    ["object"] = id,
                    ["local_position"] = new JArray(1f, 0f, 0f)
                }, 0),
                ("transaction.commit", new JObject { ["transaction"] = "rvtx:nope:nope" }, null),
                ("transaction.adopt", new JObject
                {
                    ["transaction"] = "rvtx:nope:nope",
                    ["recovery_token"] = "nope"
                }, null)
            };

            foreach (var probe in cases)
            {
                var response = rv.Call(probe.Method, probe.Params, ifRevision: probe.Revision,
                    ok: false, code: null, allowEither: true);
                Assert.That(response.Value<string>("state_domain"), Is.Not.Null.And.Not.Empty,
                    probe.Method + " answered without saying which universe it read");
                Assert.That(response.Value<string>("consistency"), Is.Not.Null.And.Not.Empty,
                    probe.Method + " answered without saying what its revision is worth");
            }

            // A failure before any method resolves has no class to report, and
            // says so rather than claiming the strongest one.
            var malformed = rv.Host.Dispatch(new JObject
            {
                ["rv"] = "1.0",
                ["id"] = "",
                ["method"] = "system.ping"
            });
            Assert.That(malformed.Value<bool>("ok"), Is.False);
            Assert.That(malformed.Value<string>("consistency"),
                Is.EqualTo(RoboVisionHost.ReadsUnknown),
                "a failure with no resolved tool claimed a consistency class");
            Assert.That(malformed.Value<string>("state_domain"), Is.Not.Null.And.Not.Empty);
        }
    }
}
