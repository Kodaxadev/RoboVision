using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
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
            host.AddTool("transaction.begin", p => host.Transactions.Begin(p, host.CurrentClientId), stability: "alpha", transactionControl: true);
            host.AddTool("transaction.commit", p => host.Transactions.Commit(p), stability: "alpha", transactionControl: true);
            host.AddTool("transaction.rollback", p => host.Transactions.Rollback(p), mutating: true, stability: "alpha", transactionControl: true);
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
                ["revision"] = host.Revision,
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

        private static JObject CreateObject(JObject parameters)
        {
            var name = parameters.Value<string>("name") ?? "RoboVisionObject";
            var go = new GameObject(name);
            Undo.RegisterCreatedObjectUndo(go, "RoboVision create " + name);
            ApplyTransform(go.transform, parameters);
            return RoboVisionSceneRead.ObjectState(go);
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
