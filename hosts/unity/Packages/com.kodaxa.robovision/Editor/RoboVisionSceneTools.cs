using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Kodaxa.RoboVision.Editor
{
    internal static class RoboVisionSceneTools
    {
        private static readonly Dictionary<string, JObject> Snapshots = new Dictionary<string, JObject>();
        private static readonly Queue<string> SnapshotOrder = new Queue<string>();

        public static void Register(RoboVisionHost host)
        {
            host.AddTool("scene.describe", p => Describe(host, p), stability: "beta");
            host.AddTool("scene.snapshot", p => Snapshot(host, p), stability: "beta");
            host.AddTool("scene.diff", p => Diff(host, p), stability: "beta");
            // Polling the journal must not cost an authoritative read, and must
            // not be the thing that discovers a change: it reports the host's
            // position, it does not establish it.
            host.AddTool("scene.changes_since", p => ChangesSince(host, p),
                reads: RoboVisionHost.ReadsNotified, stability: "alpha");
            host.AddTool("object.inspect", p => InspectObject(p), stability: "beta");
            host.AddTool("object.create", p => CreateObject(p), mutating: true, stability: "alpha");
            host.AddTool("object.delete", p => DeleteObject(p), mutating: true, stability: "alpha");
            host.AddTool("object.transform", p => TransformObject(p), mutating: true, stability: "alpha");
            // All of the transaction verbs are side-effecting although only
            // rollback moves the scene: "does not advance the revision" is not
            // the same claim as "safe to execute twice", and a duplicated begin,
            // commit, adopt or discard is not free.
            //
            // They are deliberately not covered by idempotent replay. Each is
            // already duplicate-safe through its own state machine — a second
            // begin is TRANSACTION_ACTIVE, a second commit or rollback is
            // TRANSACTION_FINISHED, a second adopt is refused because the
            // transaction is no longer orphaned — and begin's response can carry
            // a recovery token, which must never be stored in a ledger or handed
            // back by a replay to whoever redelivers the request.
            //
            // Begin is observation-bound: a transaction's checkpoint is only
            // worth anything if it is the state the caller planned against.
            // Opening one against a scene that has moved produces a rollback
            // target nobody chose.
            host.AddTool("transaction.begin", p => host.Transactions.Begin(p, host.CurrentClientId),
                stability: "alpha", transactionControl: true, sideEffecting: true,
                duplicatePolicy: RoboVisionHost.DuplicateTerminalState, observationBound: true);
            host.AddTool("transaction.commit", p => host.Transactions.Commit(p, host.CurrentClientId),
                stability: "alpha", transactionControl: true, sideEffecting: true,
                duplicatePolicy: RoboVisionHost.DuplicateTerminalState);
            host.AddTool("transaction.rollback", p => host.Transactions.Rollback(p, host.CurrentClientId),
                mutating: true, stability: "alpha", transactionControl: true,
                duplicatePolicy: RoboVisionHost.DuplicateTerminalState);
            // Adoption is how an interrupted transaction gets an owner again, so
            // it must reach the host without one — and it proves authority with
            // a token rather than with the connection it arrives on.
            host.AddTool("transaction.adopt", p => host.Transactions.Adopt(p, host.CurrentClientId),
                stability: "alpha", transactionControl: true, sideEffecting: true,
                duplicatePolicy: RoboVisionHost.DuplicateTerminalState);
            host.AddTool("transaction.discard", p => host.Transactions.Discard(p, host.CurrentClientId),
                stability: "alpha", transactionControl: true, sideEffecting: true,
                duplicatePolicy: RoboVisionHost.DuplicateTerminalState);
            // Read-only, and deliberately cheap: resolving a lost acknowledgement
            // must not cost an authoritative scene read, must never be the thing
            // that changes what it is reporting on, and must stay answerable in
            // play mode — the outcome of a transaction does not depend on which
            // universe the editor happens to be in when someone asks.
            host.AddTool("transaction.status", p => host.Transactions.Status(p),
                reads: RoboVisionHost.ReadsNotified, stability: "alpha");
        }

        private static SceneRead ReadOf(JObject stored)
        {
            return new SceneRead
            {
                State = (JObject)stored["state"],
                Fingerprint = stored.Value<string>("fingerprint")
            };
        }

        private static JObject Describe(RoboVisionHost host, JObject parameters)
        {
            return new JObject
            {
                // The revision is always the authored one. In play mode the
                // objects below are runtime observations that it does not
                // version, which is what state_domain is here to say.
                ["revision"] = host.Revision,
                ["state_domain"] = RoboVisionHost.StateDomain,
                ["playing"] = EditorApplication.isPlaying,
                ["transitioning"] = EditorApplication.isPlayingOrWillChangePlaymode
                    && !EditorApplication.isPlaying,
                ["bridge"] = host.Bridge,
                ["world_incarnation"] = host.WorldIncarnation,
                ["scenes"] = host.CurrentRead.State["scenes"]
            };
        }

        private static JObject Snapshot(RoboVisionHost host, JObject parameters)
        {
            // Reuse the dispatcher's authoritative read rather than taking a
            // second one, which could differ from the revision being reported.
            var read = host.CurrentRead;
            // Cloned, not aliased: a stored snapshot is evidence, and evidence
            // that shares a mutable object with the host's live baseline could
            // change under the client holding it.
            var state = (JObject)read.State.DeepClone();
            var fingerprint = read.Fingerprint;
            var id = "snap:" + Guid.NewGuid();
            // Bound to the world it was taken in. A snapshot is evidence about
            // one editing context, and diffing it against another would compare
            // two universes and call the result a change.
            var snapshot = new JObject
            {
                ["fingerprint"] = fingerprint,
                ["state"] = state,
                ["world_incarnation"] = host.WorldIncarnation
            };
            Snapshots[id] = snapshot;
            SnapshotOrder.Enqueue(id);
            while (SnapshotOrder.Count > 32) Snapshots.Remove(SnapshotOrder.Dequeue());
            // A full authoritative read is the only thing that may restore
            // certainty after the host has admitted it lost track.
            var openedEpoch = host.Journal.AuthoritativeSnapshot();
            var journal = host.Journal.State();
            journal["opened_new_epoch"] = openedEpoch;
            return new JObject
            {
                ["snapshot"] = id,
                ["fingerprint"] = fingerprint,
                ["state"] = state,
                ["journal"] = journal,
                ["revision"] = host.Revision
            };
        }

        /// <summary>Cheap incremental history, with its limits reported rather than hidden.</summary>
        /// <remarks>
        /// Absent cursor means bootstrap. Present-but-null does not: a client
        /// that computed a null cursor has lost its position, and answering
        /// "nothing changed" is the exact failure this call exists to prevent.
        /// </remarks>
        private static JObject ChangesSince(RoboVisionHost host, JObject parameters)
        {
            foreach (var legacy in new[] { "after", "epoch" })
            {
                if (parameters[legacy] != null)
                    throw new RoboVisionException(
                        "INVALID_PARAMS",
                        "changes_since takes a cursor; a bare sequence number cannot say which "
                        + "document incarnation or certainty epoch it came from, and both restart at 1",
                        false,
                        new JObject
                        {
                            ["rejected_parameter"] = legacy,
                            ["current_cursor"] = host.Journal.Cursor()
                        });
            }

            var result = parameters.ContainsKey("cursor")
                ? host.Journal.ChangesSince(parameters["cursor"])
                : host.Journal.Bootstrap();
            result["revision"] = host.Revision;
            result["bridge"] = host.Bridge;
            return result;
        }

        private static JObject Diff(RoboVisionHost host, JObject parameters)
        {
            var id = parameters.Value<string>("from_snapshot");
            if (String.IsNullOrWhiteSpace(id)) throw new RoboVisionException("INVALID_PARAMS", "from_snapshot is required");
            if (!Snapshots.TryGetValue(id, out var before)) throw new RoboVisionException("NOT_FOUND", "snapshot not found: " + id);
            var takenIn = before.Value<string>("world_incarnation");
            if (takenIn != null && takenIn != host.WorldIncarnation)
                throw new RoboVisionException("STALE_WORLD",
                    "that snapshot was taken in an editing context that is no longer open", true,
                    new JObject
                    {
                        ["snapshot"] = id,
                        ["taken_in"] = takenIn,
                        ["current_world_incarnation"] = host.WorldIncarnation
                    });
            // The dispatcher reconciled authoritatively before this handler ran,
            // so the host's baseline is the scene as it is now.
            var after = host.CurrentRead;
            var diff = RoboVisionSceneRead.DiffReads(ReadOf(before), after);
            diff["from_snapshot"] = id;
            diff["before_fingerprint"] = before.Value<string>("fingerprint");
            diff["after_fingerprint"] = after.Fingerprint;
            diff["equal"] = before.Value<string>("fingerprint") == after.Fingerprint;
            diff["revision"] = host.Revision;
            return diff;
        }

        private static JObject InspectObject(JObject parameters)
        {
            var go = ResolveGameObject(parameters.Value<string>("object"));
            return RoboVisionSceneRead.ObjectState(go);
        }

        /// <summary>
        /// Create an object somewhere the caller actually chose.
        /// </summary>
        /// <remarks>
        /// `new GameObject` lands in the active scene, and which scene is active
        /// is editor-control state that changes outside the authored journal: a
        /// human switching it produces no event, no revision movement and
        /// nothing a polling client can see. Measured — after such a switch the
        /// journal reported zero events and the next create landed in the other
        /// scene. A client must not perform a different operation than the one
        /// it issued because something it could not observe changed underneath
        /// it, so with more than one scene open the target has to be named.
        /// </remarks>
        private static JObject CreateObject(JObject parameters)
        {
            var name = parameters.Value<string>("name") ?? "RoboVisionObject";
            var target = TargetScene(parameters);
            var go = new GameObject(name);
            Undo.RegisterCreatedObjectUndo(go, "RoboVision create " + name);
            if (go.scene != target) Undo.MoveGameObjectToScene(go, target, "RoboVision place " + name);
            ApplyTransform(go.transform, parameters);
            return RoboVisionSceneRead.ObjectState(go);
        }

        /// <summary>Where a new object goes: named, or unambiguous, or refused.</summary>
        private static Scene TargetScene(JObject parameters)
        {
            var requested = parameters.Value<string>("scene");
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null && stage.scene.IsValid())
            {
                // The prefab stage is the whole world while it is open, and its
                // preview scene is not the active one — an object created
                // without this would land in the main stage, outside everything
                // the host is describing.
                if (requested != null && requested != stage.scene.handle.ToString())
                    throw new RoboVisionException("INVALID_PARAMS",
                        "a prefab stage is open; its preview scene is the only target", false,
                        new JObject { ["prefab_stage"] = stage.scene.handle.ToString() });
                return stage.scene;
            }

            var loaded = new List<Scene>();
            for (var i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (scene.isLoaded) loaded.Add(scene);
            }
            if (loaded.Count == 0)
                throw new RoboVisionException("INVALID_CONTEXT", "no scene is loaded to create in");

            if (requested != null)
            {
                foreach (var scene in loaded)
                {
                    if (scene.handle.ToString() == requested) return scene;
                }
                throw new RoboVisionException("NOT_FOUND", "no loaded scene has that handle", false,
                    new JObject { ["scene"] = requested, ["candidates"] = Candidates(loaded) });
            }

            if (loaded.Count == 1) return loaded[0];
            throw new RoboVisionException("AMBIGUOUS_TARGET_SCENE",
                "more than one scene is open; name the scene to create in", false,
                new JObject { ["candidates"] = Candidates(loaded) });
        }

        private static JArray Candidates(List<Scene> scenes)
        {
            return new JArray(scenes.Select(scene => new JObject
            {
                ["handle"] = scene.handle.ToString(),
                ["name"] = scene.name,
                ["path"] = scene.path,
                ["active"] = scene == SceneManager.GetActiveScene()
            }).Cast<object>().ToArray());
        }

        private static JObject DeleteObject(JObject parameters)
        {
            var go = ResolveGameObject(parameters.Value<string>("object"));
            var id = RoboVisionSceneRead.IdFor(go, out _);
            var name = go.name;
            Undo.DestroyObjectImmediate(go);
            return new JObject { ["deleted"] = new JObject { ["id"] = id, ["name"] = name } };
        }

        private static JObject TransformObject(JObject parameters)
        {
            var go = ResolveGameObject(parameters.Value<string>("object"));
            ApplyTransform(go.transform, parameters);
            return RoboVisionSceneRead.ObjectState(go);
        }

        private static void ApplyTransform(Transform transform, JObject parameters)
        {
            var so = new SerializedObject(transform);
            var changed = false;
            if (parameters["local_position"] is JArray position)
            {
                so.FindProperty("m_LocalPosition").vector3Value = ReadVector3(position, "local_position");
                changed = true;
            }
            if (parameters["local_rotation"] is JArray rotation)
            {
                if (rotation.Count != 4) throw new RoboVisionException("INVALID_PARAMS", "local_rotation must be [x,y,z,w]");
                so.FindProperty("m_LocalRotation").quaternionValue = new Quaternion(rotation[0].Value<float>(), rotation[1].Value<float>(), rotation[2].Value<float>(), rotation[3].Value<float>());
                changed = true;
            }
            if (parameters["local_scale"] is JArray scale)
            {
                so.FindProperty("m_LocalScale").vector3Value = ReadVector3(scale, "local_scale");
                changed = true;
            }
            if (changed) so.ApplyModifiedProperties();
        }

        private static Vector3 ReadVector3(JArray value, string name)
        {
            if (value.Count != 3) throw new RoboVisionException("INVALID_PARAMS", name + " must be a three-number array");
            return new Vector3(value[0].Value<float>(), value[1].Value<float>(), value[2].Value<float>());
        }

        internal static GameObject ResolveGameObject(string reference)
        {
            if (String.IsNullOrWhiteSpace(reference)) throw new RoboVisionException("INVALID_PARAMS", "object is required");
            if (reference.StartsWith("unity:GlobalObjectId_", StringComparison.Ordinal))
            {
                var raw = reference.Substring("unity:".Length);
                if (!GlobalObjectId.TryParse(raw, out var gid)) throw new RoboVisionException("INVALID_PARAMS", "invalid GlobalObjectId");
                var resolved = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid) as GameObject;
                if (resolved == null) throw new RoboVisionException("NOT_FOUND", "Unity object no longer resolves: " + reference);
                return resolved;
            }
            if (reference.StartsWith(RoboVisionSessionHandles.Prefix, StringComparison.Ordinal))
            {
                var resolved = RoboVisionSessionHandles.Resolve(reference) as GameObject;
                if (resolved == null) throw new RoboVisionException("NOT_FOUND", "Unity session handle no longer resolves: " + reference);
                return resolved;
            }
            throw new RoboVisionException("INVALID_PARAMS", "object must be a RoboVision Unity id");
        }

    }
}
