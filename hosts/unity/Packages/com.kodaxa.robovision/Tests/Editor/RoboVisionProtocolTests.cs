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
    }
}
