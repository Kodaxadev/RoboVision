using System;
using System.Collections;
using System.IO;
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
    /// own fields do not survive either. The host uses it for the same reason
    /// and with the same scope: an editor restart is exactly the boundary at
    /// which a new world identity is correct anyway.
    /// </remarks>
    public sealed class RoboVisionLifecycleTests
    {
        private const string Directory = "Assets/RoboVisionLifecycle";
        private const string ScenePath = Directory + "/Lifecycle.unity";
        private const string SessionKey = "RoboVision.Tests.SessionHandle";
        private const string DurableKey = "RoboVision.Tests.DurableId";
        private const string BridgeKey = "RoboVision.Tests.Bridge";
        private const string WorldKey = "RoboVision.Tests.World";
        private const string JournalKey = "RoboVision.Tests.Journal";
        private const string RevisionKey = "RoboVision.Tests.Revision";
        private const string CursorKey = "RoboVision.Tests.Cursor";

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
            SessionState.EraseString(BridgeKey);
            SessionState.EraseString(WorldKey);
            SessionState.EraseString(JournalKey);
            SessionState.EraseString(RevisionKey);
            SessionState.EraseString(CursorKey);
            if (AssetDatabase.IsValidFolder(Directory)) AssetDatabase.DeleteAsset(Directory);
            AssetDatabase.Refresh();
        }

        /// <summary>
        /// A rebuilt bridge is not a new world, and must not pretend to be.
        /// </summary>
        /// <remarks>
        /// This claim is stronger than the one it replaces, and it is only
        /// allowed because it can be checked. A domain reload leaves the
        /// editor's scenes exactly where they were — measured: the loaded scene
        /// handles were identical either side of a play mode round trip — and
        /// SessionState carries the previous identity across, so the host can
        /// read the context back out of the editor and see for itself that it is
        /// the same one. Declaring the world replaced would have cost the client
        /// every durable reference it holds for no reason but our own restart.
        ///
        /// What does not survive is the history. The journal is new, so a cursor
        /// from before the reload is refused — and refused as a replaced
        /// journal, not a replaced world, because those are different problems:
        /// one means re-observe everything, the other means resume where you
        /// were with the references you already have.
        /// </remarks>
        [UnityTest]
        public IEnumerator ADomainReloadRebuildsTheBridgeAndJournalButNotTheWorld()
        {
            Harness.FreshScene();
            var before = NewHarness();
            before.CreateObject("BeforeReload");
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), ScenePath);

            var described = before.Result("scene.describe");
            var journal = before.Result("scene.changes_since");
            SessionState.SetString(BridgeKey, described.Value<string>("bridge"));
            SessionState.SetString(WorldKey, described.Value<string>("world_incarnation"));
            SessionState.SetString(RevisionKey, described.Value<long>("revision").ToString());
            SessionState.SetString(JournalKey, journal.Value<string>("journal_incarnation"));
            SessionState.SetString(CursorKey, journal.Value<string>("cursor"));

            yield return new EnterPlayMode();
            yield return new ExitPlayMode();

            var staleBridge = SessionState.GetString(BridgeKey, null);
            var staleWorld = SessionState.GetString(WorldKey, null);
            var staleJournal = SessionState.GetString(JournalKey, null);
            var staleCursor = SessionState.GetString(CursorKey, null);
            var staleRevision = Int64.Parse(SessionState.GetString(RevisionKey, "-1"));
            Assert.That(staleCursor, Is.Not.Null.And.Not.Empty, "the test lost its own state across the reload");

            var after = NewHarness();
            var now = after.Result("scene.describe");
            var nowJournal = after.Result("scene.changes_since");

            Assert.That(now.Value<string>("bridge"), Is.Not.EqualTo(staleBridge),
                "a rebuilt bridge reported the previous bridge identity");
            Assert.That(now.Value<string>("world_incarnation"), Is.EqualTo(staleWorld),
                "the same scenes are open, verified by reading them, and the host still declared "
                + "the editing context replaced");
            Assert.That(nowJournal.Value<string>("journal_incarnation"), Is.Not.EqualTo(staleJournal),
                "the journal's history died with the domain and it claimed the same identity");
            Assert.That(now.Value<long>("revision"), Is.EqualTo(staleRevision),
                "a saved scene hashes the same across a reload, so the revision must resume");
            Assert.That(nowJournal.Value<bool>("certain"), Is.True,
                "nothing changed across the reload and the host said it could not tell");

            // The equal-counter case: same world, same epoch, and the new
            // journal has already reached the sequence number the stale cursor
            // names. Only the journal identity separates them.
            after.CreateObject("AfterReload");
            var refused = after.Call("scene.changes_since", new JObject { ["cursor"] = staleCursor },
                ok: false, code: "JOURNAL_REPLACED");
            var data = (JObject)refused["error"]["data"];
            Assert.That(data.Value<string>("current_cursor"),
                Is.Not.Null.And.Not.Empty, "the refusal did not say where to resume from");
            Assert.That(data.Value<string>("current_journal_incarnation"),
                Is.EqualTo(after.Result("scene.changes_since").Value<string>("journal_incarnation")));
        }

        /// <summary>
        /// In an unsaved scene the reload re-addresses everything, and the host says so.
        /// </summary>
        /// <remarks>
        /// An object with no durable identity is addressed by a session handle,
        /// and those die with the domain. Every object therefore comes back
        /// under a new name, the world no longer hashes the way it did, and
        /// nothing observed the transition. The host cannot say whether anything
        /// authored moved, so it says exactly that rather than reporting a scene
        /// full of deletions and creations that never happened.
        /// </remarks>
        [UnityTest]
        public IEnumerator ADomainReloadInAnUnsavedSceneAdmitsItCannotAccountForTheChange()
        {
            Harness.FreshScene();
            var before = NewHarness();
            var sessionId = before.CreateObject("Unsaved");
            Assert.That(sessionId, Does.StartWith("unity:session:"),
                "precondition: this object must have no durable identity");
            SessionState.SetString(WorldKey,
                before.Result("scene.describe").Value<string>("world_incarnation"));

            yield return new EnterPlayMode();
            yield return new ExitPlayMode();

            var staleWorld = SessionState.GetString(WorldKey, null);
            var after = NewHarness();
            var now = after.Result("scene.describe");
            var journal = after.Result("scene.changes_since");

            Assert.That(now.Value<string>("world_incarnation"), Is.EqualTo(staleWorld),
                "the same scenes are open and the host declared the editing context replaced");
            Assert.That(journal.Value<bool>("certain"), Is.False,
                "the world came back hashing differently and the host claimed it knew why");
            Assert.That(journal.Value<string>("uncertain_reason"), Is.Not.Null.And.Not.Empty,
                "certainty was lost without a reason a client could act on");
        }

        /// <summary>
        /// A reload that began inside a Prefab Stage must report whichever world it is in.
        /// </summary>
        /// <remarks>
        /// Whether Unity keeps a Prefab Stage open across a play mode round trip
        /// is Unity's business, and asserting a guess about it would be a test
        /// of the editor rather than of the host. What the host owes is the same
        /// either way: the world it reports must be the world that is actually
        /// open, and a position from before the reload must not resolve.
        /// </remarks>
        [UnityTest]
        public IEnumerator ADomainReloadInsideAPrefabStageReportsTheWorldItWokeUpIn()
        {
            Harness.FreshScene();
            var before = NewHarness();
            var source = new GameObject("Staged");
            var prefabPath = Directory + "/Staged.prefab";
            PrefabUtility.SaveAsPrefabAsset(source, prefabPath);
            UnityEngine.Object.DestroyImmediate(source);

            var stage = PrefabStageUtility.OpenPrefab(prefabPath);
            Assert.That(stage, Is.Not.Null, "precondition: the prefab stage did not open");
            SessionState.SetString(WorldKey,
                before.Result("scene.describe").Value<string>("world_incarnation"));
            SessionState.SetString(CursorKey,
                before.Result("scene.changes_since").Value<string>("cursor"));

            yield return new EnterPlayMode();
            yield return new ExitPlayMode();

            var staleWorld = SessionState.GetString(WorldKey, null);
            var staleCursor = SessionState.GetString(CursorKey, null);
            var after = NewHarness();
            var now = after.Result("scene.describe");
            var stillStaged = PrefabStageUtility.GetCurrentPrefabStage() != null;

            if (stillStaged)
            {
                Assert.That(now.Value<string>("world_incarnation"), Is.EqualTo(staleWorld),
                    "the same prefab is still open for editing and the host replaced the world");
                after.Call("scene.changes_since", new JObject { ["cursor"] = staleCursor },
                    ok: false, code: "JOURNAL_REPLACED");
            }
            else
            {
                Assert.That(now.Value<string>("world_incarnation"), Is.Not.EqualTo(staleWorld),
                    "the prefab stage closed across the reload and the host went on reporting "
                    + "the world it was editing inside it");
                after.Call("scene.changes_since", new JObject { ["cursor"] = staleCursor },
                    ok: false, code: "STALE_WORLD");
            }

            // Either way, what is reported is what is open.
            var kinds = now["scenes"].Select(scene => scene.Value<string>("kind")).ToList();
            Assert.That(kinds.Contains("prefab_stage"), Is.EqualTo(stillStaged),
                "the host described a prefab stage it is not in, or missed the one it is");

            if (PrefabStageUtility.GetCurrentPrefabStage() != null) StageUtility.GoToMainStage();
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

            // And that failure has to be a guarantee rather than luck. A bare
            // counter restarts in the rebuilt domain, so the handles it issues
            // next would eventually collide with low-numbered ones from before
            // and silently address a different object; the scope in the token is
            // what makes the collision impossible.
            var reissued = after.CreateObject("AfterReload");
            Assert.That(reissued, Is.Not.EqualTo(staleHandle),
                "a handle issued after the reload reused a name from before it");
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

            // A socket round-trip in this same coroutine was tried and removed.
            // Resuming an iterator across a domain reload and then doing socket
            // work inside it fails in the test framework's own resumption, not
            // in the host. The stronger claim — the shipped Python client
            // connecting to a brand new editor process — is proven by
            // tools/unity-restart-gate.sh instead.
            RoboVisionHost.Instance.Stop();
        }
    }
}
