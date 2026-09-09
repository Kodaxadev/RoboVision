using System;
using System.Collections;
using System.Linq;
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
    /// Play mode is a different universe, and the API has to say which one it is in.
    /// </summary>
    /// <remarks>
    /// Entering play mode instantiates the open scenes, gives their objects
    /// runtime addresses, and discards all of it on exit. Two things follow, and
    /// both were wrong before these tests existed.
    ///
    /// Authoring is an Edit Mode operation. `object.create` ran happily in play
    /// mode: it really did create a runtime GameObject, reconciliation correctly
    /// refused to make a runtime read the authored baseline, so the response
    /// reported `outcome: noop` about a mutation that had visibly happened — and
    /// the object then evaporated on exit. Measured, then refused.
    ///
    /// Reads are still useful and still allowed, but a response full of runtime
    /// objects must not be mistakable for authored state just because it carries
    /// the same revision. It carries `state_domain` now, and the revision it
    /// reports versions none of what it is showing.
    /// </remarks>
    public sealed class RoboVisionPlayModeTests
    {
        private const string Folder = "Assets/RoboVisionPlayMode";
        private const string ScenePath = Folder + "/PlayMode.unity";
        private const string RevisionKey = "RoboVision.Tests.PlayRevision";

        [SetUp]
        public void SetUp()
        {
            if (!System.IO.Directory.Exists(Folder)) System.IO.Directory.CreateDirectory(Folder);
            AssetDatabase.Refresh();
        }

        [TearDown]
        public void TearDown()
        {
            SessionState.EraseString(RevisionKey);
            if (AssetDatabase.IsValidFolder(Folder)) AssetDatabase.DeleteAsset(Folder);
            AssetDatabase.Refresh();
        }

        [UnityTest]
        public IEnumerator AuthoringMutationsAreRefusedInPlayMode()
        {
            Harness.FreshScene();
            var before = new Harness("play");
            before.CreateObject("Authored");
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), ScenePath);
            SessionState.SetString(RevisionKey,
                before.Result("scene.describe").Value<long>("revision").ToString());

            yield return new EnterPlayMode();

            var rv = new Harness("play-in");
            foreach (var method in new[] { "object.create", "transaction.begin" })
            {
                var refused = rv.Call(method, new JObject { ["name"] = "RuntimeIntruder" },
                    ok: false, code: "PLAY_MODE_MUTATION_REFUSED");
                Assert.That(((JObject)refused["error"]["data"]).Value<bool>("playing"), Is.True,
                    method + " was refused without saying why");
                Assert.That(refused["error"].Value<bool>("retryable"), Is.True,
                    "leaving play mode makes " + method + " possible again, so it is retryable");
            }

            Assert.That(GameObject.Find("RuntimeIntruder"), Is.Null,
                "a refused mutation still created a runtime object");

            yield return new ExitPlayMode();

            var after = new Harness("play-after");
            var described = after.Result("scene.describe");
            Assert.That(described.Value<long>("revision"),
                Is.EqualTo(Int64.Parse(SessionState.GetString(RevisionKey, "-1"))),
                "the authored revision moved across a play mode round trip");
            var names = described["scenes"].SelectMany(s => s["objects"])
                .Select(o => o.Value<string>("name")).ToList();
            Assert.That(names, Is.EquivalentTo(new[] { "Authored" }),
                "the authored scene did not come back as it was");
        }

        [UnityTest]
        public IEnumerator APlayModeReadSaysItIsARuntimeObservation()
        {
            Harness.FreshScene();
            var before = new Harness("domain");
            before.CreateObject("Authored");
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), ScenePath);
            var edit = before.Call("scene.describe");
            Assert.That(edit.Value<string>("state_domain"), Is.EqualTo(RoboVisionHost.DomainAuthored),
                "an Edit Mode read must be reported as authored state");
            SessionState.SetString(RevisionKey,
                ((JObject)edit["result"]).Value<long>("revision").ToString());

            yield return new EnterPlayMode();

            var rv = new Harness("domain-in");
            var response = rv.Call("scene.describe");
            var result = (JObject)response["result"];

            Assert.That(response.Value<string>("state_domain"), Is.EqualTo(RoboVisionHost.DomainPlayRuntime),
                "a play mode read was presented as authored state");
            Assert.That(result.Value<string>("state_domain"), Is.EqualTo(RoboVisionHost.DomainPlayRuntime));
            Assert.That(result.Value<bool>("playing"), Is.True);

            // The revision it carries is the authored one, and it versions none
            // of what is being reported: the objects below are runtime copies.
            Assert.That(result.Value<long>("revision"),
                Is.EqualTo(Int64.Parse(SessionState.GetString(RevisionKey, "-1"))),
                "the authored revision changed because the editor entered play mode");

            // A read still answers, because looking at a running scene is a
            // legitimate thing to want.
            var names = result["scenes"].SelectMany(s => s["objects"])
                .Select(o => o.Value<string>("name")).ToList();
            Assert.That(names, Contains.Item("Authored"),
                "the running scene was not readable at all");

            // And the journal does not pretend to cover it.
            var journal = rv.Result("scene.changes_since");
            Assert.That(journal.Value<bool>("certain"), Is.True,
                "entering play mode cost the journal its certainty over an authored scene "
                + "nothing had touched");

            yield return new ExitPlayMode();

            var after = new Harness("domain-after");
            Assert.That(after.Call("scene.describe").Value<string>("state_domain"),
                Is.EqualTo(RoboVisionHost.DomainAuthored),
                "the host went on reporting runtime state after leaving play mode");
        }
    }
}
