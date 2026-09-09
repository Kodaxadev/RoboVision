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
    /// <summary>
    /// How the Unity scene is read, hashed and compared.
    /// </summary>
    /// <remarks>
    /// Separate from the tools that expose it because it answers a different
    /// question. These functions are the host's only source of truth about what
    /// is in the editor: reconciliation, transactions, `scene.snapshot` and
    /// `scene.diff` all go through them, so there is one definition of what the
    /// scene is, one of what counts as a change to it, and one of what is
    /// bookkeeping rather than authored state.
    /// </remarks>
    internal static class RoboVisionSceneRead
    {
        /// <summary>Strip editor bookkeeping that is not authored state.</summary>
        /// <remarks>
        /// Two kinds of field are removed, for the same reason: they move when
        /// nothing about the scene's contents has.
        ///
        /// scene.isDirty describes whether the editor thinks the scene needs
        /// saving, and Unity flips it asynchronously after an edit. Hashing it
        /// meant a fingerprint could move with no scene change at all, which the
        /// host then reported as an out-of-band edit and refused to roll back on.
        ///
        /// The scene's name and path, and each object's scene path, say where
        /// the document is stored. Saving an untitled scene fills all three in,
        /// so hashing them made a save look like every object in the scene had
        /// changed — a save is not an edit, and the journal said otherwise.
        /// Scene membership is not lost by this: objects are nested under the
        /// scene they belong to, so moving one between scenes still moves it
        /// between arrays. All of these stay in the reported state, where they
        /// are useful, and out of the hash, where they are actively harmful.
        ///
        /// The scene handle and the active-scene flag join them. The handle
        /// names this session's loaded instance and is what scene-level diffing
        /// keys on; hashing it would make closing and reopening one scene with
        /// identical contents look like a change nothing could account for.
        /// Which scene is active decides where the editor puts new objects,
        /// which an agent needs to see, but switching it authors nothing.
        /// </remarks>
        internal static JToken HashableState(JObject state)
        {
            var copy = (JObject)state.DeepClone();
            var scenes = copy["scenes"] as JArray;
            if (scenes != null)
            {
                foreach (var scene in scenes.OfType<JObject>())
                {
                    scene.Remove("dirty");
                    scene.Remove("name");
                    scene.Remove("path");
                    scene.Remove("handle");
                    scene.Remove("active");
                    var objects = scene["objects"] as JArray;
                    if (objects == null) continue;
                    foreach (var obj in objects.OfType<JObject>()) obj.Remove("scene");
                }
            }
            return copy;
        }

        internal static string ComputeFingerprint()
        {
            return CaptureRead().Fingerprint;
        }

        /// <summary>One authoritative read: the scene state and its fingerprint.</summary>
        /// <remarks>
        /// Taken together deliberately. Capturing the state and hashing it in
        /// two separate passes would let the scene move between them, and the
        /// pair would then describe two different moments.
        /// </remarks>
        internal static SceneRead CaptureRead()
        {
            var state = CaptureState();
            return new SceneRead
            {
                State = state,
                Fingerprint = HashToken(HashableState(state)),
                World = WorldContext.Read()
            };
        }

        /// <summary>What changed between two reads: objects, and the scenes holding them.</summary>
        /// <remarks>
        /// The one diff implementation. `scene.diff` answers a client with it and
        /// reconciliation attributes changes with it, so the two can never come
        /// to different conclusions about what moved.
        ///
        /// Scenes are diffed as well as objects because a world can gain or lose
        /// one without gaining or losing an object: opening an empty scene
        /// additively moves the fingerprint and produces no object difference at
        /// all, and a host with nothing to attribute that to would have to admit
        /// it had lost track of a scene it watched being opened.
        /// </remarks>
        internal static JObject DiffReads(SceneRead before, SceneRead after)
        {
            var beforeObjects = FlattenObjects(before.State);
            var afterObjects = FlattenObjects(after.State);
            var created = afterObjects.Keys.Except(beforeObjects.Keys).OrderBy(x => x, StringComparer.Ordinal)
                .Select(x => new JObject { ["id"] = x, ["name"] = afterObjects[x].Value<string>("name") });
            var deleted = beforeObjects.Keys.Except(afterObjects.Keys).OrderBy(x => x, StringComparer.Ordinal)
                .Select(x => new JObject { ["id"] = x, ["name"] = beforeObjects[x].Value<string>("name") });
            var changed = beforeObjects.Keys.Intersect(afterObjects.Keys)
                .Where(x => !JToken.DeepEquals(beforeObjects[x], afterObjects[x]))
                .OrderBy(x => x, StringComparer.Ordinal)
                .Select(x => new JObject { ["id"] = x, ["before"] = beforeObjects[x], ["after"] = afterObjects[x] });
            var beforeScenes = ScenesOf(before.State);
            var afterScenes = ScenesOf(after.State);
            var loaded = afterScenes.Keys.Except(beforeScenes.Keys).OrderBy(x => x, StringComparer.Ordinal)
                .Select(x => SceneRef(x, afterScenes[x]));
            var unloaded = beforeScenes.Keys.Except(afterScenes.Keys).OrderBy(x => x, StringComparer.Ordinal)
                .Select(x => SceneRef(x, beforeScenes[x]));
            return new JObject
            {
                ["created"] = new JArray(created),
                ["deleted"] = new JArray(deleted),
                ["changed"] = new JArray(changed),
                ["scenes_loaded"] = new JArray(loaded),
                ["scenes_unloaded"] = new JArray(unloaded)
            };
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

        /// <summary>Loaded scenes by handle, which is what identifies one loaded instance.</summary>
        internal static Dictionary<string, JObject> ScenesOf(JObject state)
        {
            var scenes = new Dictionary<string, JObject>(StringComparer.Ordinal);
            foreach (var scene in ((JArray)state["scenes"]).OfType<JObject>())
            {
                var handle = scene.Value<string>("handle");
                if (handle != null) scenes[handle] = scene;
            }
            return scenes;
        }

        internal static JObject SceneRef(string handle, JObject scene)
        {
            return new JObject
            {
                ["handle"] = handle,
                ["name"] = scene.Value<string>("name"),
                ["path"] = scene.Value<string>("path"),
                ["kind"] = scene.Value<string>("kind")
            };
        }

        internal static JObject SceneState(UnityEngine.SceneManagement.Scene scene, string kind)
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
                ["handle"] = scene.handle.ToString(),
                ["active"] = scene == SceneManager.GetActiveScene(),
                ["build_index"] = scene.buildIndex,
                ["dirty"] = scene.isDirty,
                ["kind"] = kind,
                ["objects"] = new JArray(objects)
            };
        }

        internal static JObject CaptureState()
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

        internal static JObject ObjectState(GameObject go)
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

        internal static JObject ComponentState(Component component)
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

        internal static Dictionary<string, JObject> FlattenObjects(JObject state)
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

        internal static string HashString(string value)
        {
            var bytes = Encoding.UTF8.GetBytes(value ?? String.Empty);
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        internal static string HashToken(JToken token)
        {
            return HashString(token.ToString(Formatting.None));
        }
    }
}
