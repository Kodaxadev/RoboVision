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
            host.AddTool("object.inspect", p => InspectObject(p), stability: "beta");
            host.AddTool("object.create", p => CreateObject(p), mutating: true, stability: "alpha");
            host.AddTool("object.delete", p => DeleteObject(p), mutating: true, stability: "alpha");
            host.AddTool("object.transform", p => TransformObject(p), mutating: true, stability: "alpha");
            host.AddTool("transaction.begin", p => host.Transactions.Begin(p), stability: "alpha", transactionControl: true);
            host.AddTool("transaction.commit", p => host.Transactions.Commit(p), stability: "alpha", transactionControl: true);
            host.AddTool("transaction.rollback", p => host.Transactions.Rollback(p), mutating: true, stability: "alpha", transactionControl: true);
        }

        internal static string ComputeFingerprint()
        {
            var state = CaptureState();
            return HashToken(state);
        }

        private static JObject Describe(RoboVisionHost host, JObject parameters)
        {
            return new JObject
            {
                ["revision"] = host.Revision,
                ["scenes"] = CaptureState()["scenes"]
            };
        }

        private static JObject Snapshot(RoboVisionHost host, JObject parameters)
        {
            var state = CaptureState();
            var fingerprint = HashToken(state);
            var id = "snap:" + Guid.NewGuid();
            var snapshot = new JObject { ["fingerprint"] = fingerprint, ["state"] = state };
            Snapshots[id] = snapshot;
            SnapshotOrder.Enqueue(id);
            while (SnapshotOrder.Count > 32) Snapshots.Remove(SnapshotOrder.Dequeue());
            return new JObject { ["snapshot"] = id, ["fingerprint"] = fingerprint, ["state"] = state, ["revision"] = host.Revision };
        }

        private static JObject Diff(RoboVisionHost host, JObject parameters)
        {
            var id = parameters.Value<string>("from_snapshot");
            if (String.IsNullOrWhiteSpace(id)) throw new RoboVisionException("INVALID_PARAMS", "from_snapshot is required");
            if (!Snapshots.TryGetValue(id, out var before)) throw new RoboVisionException("NOT_FOUND", "snapshot not found: " + id);
            var afterState = CaptureState();
            var beforeObjects = FlattenObjects((JObject)before["state"]);
            var afterObjects = FlattenObjects(afterState);
            var created = afterObjects.Keys.Except(beforeObjects.Keys).OrderBy(x => x).Select(x => new JObject { ["id"] = x, ["name"] = afterObjects[x].Value<string>("name") });
            var deleted = beforeObjects.Keys.Except(afterObjects.Keys).OrderBy(x => x).Select(x => new JObject { ["id"] = x, ["name"] = beforeObjects[x].Value<string>("name") });
            var changed = beforeObjects.Keys.Intersect(afterObjects.Keys).Where(x => !JToken.DeepEquals(beforeObjects[x], afterObjects[x])).OrderBy(x => x)
                .Select(x => new JObject { ["id"] = x, ["before"] = beforeObjects[x], ["after"] = afterObjects[x] });
            var afterFingerprint = HashToken(afterState);
            return new JObject
            {
                ["from_snapshot"] = id,
                ["before_fingerprint"] = before.Value<string>("fingerprint"),
                ["after_fingerprint"] = afterFingerprint,
                ["equal"] = before.Value<string>("fingerprint") == afterFingerprint,
                ["created"] = new JArray(created), ["deleted"] = new JArray(deleted), ["changed"] = new JArray(changed),
                ["revision"] = host.Revision
            };
        }

        private static JObject InspectObject(JObject parameters)
        {
            var go = ResolveGameObject(parameters.Value<string>("object"));
            return ObjectState(go);
        }

        private static JObject CreateObject(JObject parameters)
        {
            var name = parameters.Value<string>("name") ?? "RoboVisionObject";
            var go = new GameObject(name);
            Undo.RegisterCreatedObjectUndo(go, "RoboVision create " + name);
            ApplyTransform(go.transform, parameters);
            return ObjectState(go);
        }

        private static JObject DeleteObject(JObject parameters)
        {
            var go = ResolveGameObject(parameters.Value<string>("object"));
            var id = IdFor(go, out _);
            var name = go.name;
            Undo.DestroyObjectImmediate(go);
            return new JObject { ["deleted"] = new JObject { ["id"] = id, ["name"] = name } };
        }

        private static JObject TransformObject(JObject parameters)
        {
            var go = ResolveGameObject(parameters.Value<string>("object"));
            ApplyTransform(go.transform, parameters);
            return ObjectState(go);
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

        internal static string IdFor(UnityEngine.Object obj, out bool persistent)
        {
            var gid = GlobalObjectId.GetGlobalObjectIdSlow(obj);
            var text = gid.ToString();
            if (!text.StartsWith("GlobalObjectId_V1-0-", StringComparison.Ordinal))
            {
                persistent = true;
                return "unity:" + text;
            }
            persistent = false;
            return RoboVisionSessionHandles.Token(obj);
        }

        private static JObject SceneState(UnityEngine.SceneManagement.Scene scene, string kind)
        {
            var objects = scene.GetRootGameObjects()
                .SelectMany(root => root.GetComponentsInChildren<Transform>(true))
                .Select(t => t.gameObject)
                .Distinct()
                .Select(ObjectState)
                .OrderBy(o => o.Value<string>("id"), StringComparer.Ordinal);
            return new JObject
            {
                ["name"] = scene.name,
                ["path"] = scene.path,
                ["build_index"] = scene.buildIndex,
                ["dirty"] = scene.isDirty,
                ["kind"] = kind,
                ["objects"] = new JArray(objects)
            };
        }

        private static JObject CaptureState()
        {
            // A Prefab Stage edits its contents in a preview scene that
            // SceneManager does not enumerate. Reporting only SceneManager's
            // scenes meant that with a prefab open for editing the host
            // described an empty project, so an agent would believe there was
            // nothing there and create objects in the wrong place.
            var stage = UnityEditor.SceneManagement.PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null && stage.scene.IsValid())
            {
                var staged = SceneState(stage.scene, "prefab_stage");
                staged["prefab_asset_path"] = stage.assetPath;
                return new JObject { ["scenes"] = new JArray { staged } };
            }

            var scenes = new JArray();
            for (var i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (!scene.isLoaded) continue;
                scenes.Add(SceneState(scene, "scene"));
            }
            return new JObject { ["scenes"] = scenes };
        }

        private static JObject ObjectState(GameObject go)
        {
            var id = IdFor(go, out var persistent);
            var t = go.transform;
            var r = t.localRotation;
            var components = go.GetComponents<Component>().Where(component => component != null).ToArray();
            var componentTypes = components.Select(component => component.GetType().FullName).OrderBy(name => name, StringComparer.Ordinal);
            var componentState = components.Select(ComponentState).OrderBy(state => state.Value<string>("id"), StringComparer.Ordinal);
            return new JObject
            {
                ["id"] = id,
                ["identity_persistent"] = persistent,
                ["name"] = go.name,
                ["scene"] = go.scene.path,
                ["active"] = go.activeSelf,
                ["layer"] = go.layer,
                ["tag"] = go.tag,
                ["parent"] = t.parent ? IdFor(t.parent.gameObject, out _) : null,
                ["local_position"] = new JArray(t.localPosition.x, t.localPosition.y, t.localPosition.z),
                ["local_rotation"] = new JArray(r.x, r.y, r.z, r.w),
                ["local_scale"] = new JArray(t.localScale.x, t.localScale.y, t.localScale.z),
                ["components"] = new JArray(componentTypes),
                ["component_state"] = new JArray(componentState)
            };
        }

        private static JObject ComponentState(Component component)
        {
            var id = IdFor(component, out var persistent);
            string serialized;
            try
            {
                serialized = EditorJsonUtility.ToJson(component, false) ?? String.Empty;
            }
            catch (Exception ex)
            {
                // A component that Unity cannot serialize must still perturb the
                // scene fingerprint deterministically instead of silently
                // disappearing from transaction/revision accounting.
                serialized = "<serialization-error>:" + ex.GetType().FullName + ":" + ex.Message;
            }
            return new JObject
            {
                ["id"] = id,
                ["identity_persistent"] = persistent,
                ["type"] = component.GetType().FullName,
                ["serialized_sha256"] = HashString(serialized)
            };
        }

        private static Dictionary<string, JObject> FlattenObjects(JObject state)
        {
            var result = new Dictionary<string, JObject>(StringComparer.Ordinal);
            foreach (var scene in (JArray)state["scenes"])
                foreach (var item in (JArray)scene["objects"])
                {
                    var obj = (JObject)item;
                    result[obj.Value<string>("id")] = obj;
                }
            return result;
        }

        private static string HashString(string value)
        {
            var bytes = Encoding.UTF8.GetBytes(value ?? String.Empty);
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private static string HashToken(JToken token)
        {
            return HashString(token.ToString(Formatting.None));
        }
    }
}
