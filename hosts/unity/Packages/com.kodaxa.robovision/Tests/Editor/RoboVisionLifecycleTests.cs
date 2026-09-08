using System.Collections;
using System.IO;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// What survives a domain reload, and what the host must not assume does.
    /// </summary>
    /// <remarks>
    /// Entering and leaving Play Mode reloads the scripting domain, which clears
    /// every static in the package — including the session handle table. That is
    /// the intended design, so it is asserted rather than trusted: a durable
    /// <c>GlobalObjectId</c> must still resolve afterwards, while a
    /// <c>unity:session:*</c> handle must not silently start addressing some
    /// other object.
    ///
    /// State that has to cross the reload is stashed in <c>SessionState</c>,
    /// which survives a domain reload but not an editor restart — the test's
    /// own fields do not survive either.
    /// </remarks>
    public sealed class RoboVisionLifecycleTests
    {
        private const string Directory = "Assets/RoboVisionLifecycle";
        private const string ScenePath = Directory + "/Lifecycle.unity";
        private const string SessionKey = "RoboVision.Tests.SessionHandle";
        private const string DurableKey = "RoboVision.Tests.DurableId";

        private static Harness NewHarness() => new Harness("lifecycle");

        [SetUp]
        public void SetUp()
        {
            if (!System.IO.Directory.Exists(Directory)) System.IO.Directory.CreateDirectory(Directory);
            AssetDatabase.Refresh();
        }

        [TearDown]
        public void TearDown()
        {
            SessionState.EraseString(SessionKey);
            SessionState.EraseString(DurableKey);
            if (AssetDatabase.IsValidFolder(Directory)) AssetDatabase.DeleteAsset(Directory);
            AssetDatabase.Refresh();
        }

        [UnityTest]
        public IEnumerator HostSurvivesAPlayModeRoundTrip()
        {
            Harness.FreshScene();
            var before = NewHarness();
            before.CreateObject("BeforePlayMode");
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), ScenePath);

            yield return new EnterPlayMode();
            yield return new ExitPlayMode();

            // The singleton is rebuilt by the reload; it must still answer.
            var after = NewHarness();
            var hello = after.Result("system.hello");
            Assert.That(hello["editor"].Value<string>("name"), Is.EqualTo("Unity"),
                "the host did not answer after a play mode round trip");

            var describe = after.Result("scene.describe");
            Assert.That(describe["scenes"], Is.Not.Null,
                "the host could not describe the scene after a play mode round trip");
        }

        [UnityTest]
        public IEnumerator GlobalObjectIdSurvivesADomainReloadAndSessionHandlesDoNot()
        {
            Harness.FreshScene();
            var before = NewHarness();
            var sessionId = before.CreateObject("AcrossReload");
            Assert.That(sessionId, Does.StartWith("unity:session:"));

            // Saving upgrades identity; capture both forms before the reload.
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), ScenePath);
            var durableId = before.Result("object.inspect", new JObject { ["object"] = sessionId })
                .Value<string>("id");
            Assert.That(durableId, Does.StartWith("unity:GlobalObjectId_"));

            SessionState.SetString(SessionKey, sessionId);
            SessionState.SetString(DurableKey, durableId);

            yield return new EnterPlayMode();
            yield return new ExitPlayMode();

            var staleHandle = SessionState.GetString(SessionKey, null);
            var durable = SessionState.GetString(DurableKey, null);
            Assert.That(staleHandle, Is.Not.Null.And.Not.Empty, "the test lost its own state across the reload");

            var after = NewHarness();

            // The durable reference is the whole point of GlobalObjectId.
            var resolved = after.Result("object.inspect", new JObject { ["object"] = durable });
            Assert.That(resolved.Value<string>("name"), Is.EqualTo("AcrossReload"),
                "a GlobalObjectId did not survive the domain reload");
            Assert.That(resolved.Value<bool>("identity_persistent"), Is.True);

            // The session handle must fail loudly rather than resolve to
            // whatever object now happens to occupy that slot.
            var response = after.Call("object.inspect", new JObject { ["object"] = staleHandle }, ok: false);
            var code = response["error"].Value<string>("code");
            Assert.That(code, Is.EqualTo("NOT_FOUND"),
                "a session handle from before the domain reload did not fail cleanly; got " + code);
        }

        [UnityTest]
        public IEnumerator TransportComesBackServingAfterADomainReload()
        {
            Harness.FreshScene();
            if (!RoboVisionHost.Instance.Running) RoboVisionHost.Instance.Start(RoboVisionHost.DefaultPort);
            Assert.That(RoboVisionHost.Instance.Running, Is.True);

            yield return new EnterPlayMode();
            yield return new ExitPlayMode();

            // The bootstrap stops the listener on beforeAssemblyReload and
            // auto-starts the rebuilt host afterwards, so a live editor keeps
            // answering across a reload without anyone reconnecting by hand.
            Assert.That(RoboVisionHost.Instance.Running, Is.True,
                "the host did not come back listening after a domain reload");

            // A flag is not proof the socket was really released and rebound.
            // Stopping and rebinding on a fresh port exercises that path.
            RoboVisionHost.Instance.Stop();
            Assert.That(RoboVisionHost.Instance.Running, Is.False, "Stop did not release the listener");

            var probe = new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback, 0);
            probe.Start();
            var port = ((System.Net.IPEndPoint)probe.LocalEndpoint).Port;
            probe.Stop();

            RoboVisionHost.Instance.Start(port);
            Assert.That(RoboVisionHost.Instance.Running, Is.True,
                "the rebuilt host could not bind a port after a domain reload");

            var reply = RoboVisionHost.Instance.Dispatch(new JObject
            {
                ["rv"] = "1.0",
                ["id"] = "after-reload",
                ["method"] = "system.ping",
                ["params"] = new JObject()
            });
            Assert.That(reply.Value<bool>("ok"), Is.True, "the host did not answer after a domain reload");

            RoboVisionHost.Instance.Stop();
        }
    }
}
