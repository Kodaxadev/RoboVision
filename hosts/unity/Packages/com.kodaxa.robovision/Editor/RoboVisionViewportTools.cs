using System;
using System.IO;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace Kodaxa.RoboVision.Editor
{
    internal static class RoboVisionViewportTools
    {
        public static void Register(RoboVisionHost host)
        {
            host.AddTool("viewport.inspect", p => Inspect(host), requiresUi: true, stability: "beta");
            host.AddTool("viewport.focus", p => Focus(host, p), requiresUi: true, stability: "alpha");
            host.AddTool("viewport.capture", p => Capture(host, p), evidence: true, requiresUi: true, stability: "alpha");
        }

        private static SceneView RequireSceneView()
        {
            var view = SceneView.lastActiveSceneView;
            if (view == null || view.camera == null)
                throw new RoboVisionException("INVALID_CONTEXT", "no active SceneView with a camera is available");
            return view;
        }

        private static JObject Inspect(RoboVisionHost host)
        {
            var view = RequireSceneView();
            return Metadata(host, view, "scene_view");
        }

        private static JToken Focus(RoboVisionHost host, JObject parameters)
        {
            var go = RoboVisionSceneTools.ResolveGameObject(parameters.Value<string>("object"));
            var view = RequireSceneView();
            var bounds = CalculateBounds(go);
            view.Frame(bounds, false);
            view.Repaint();
            return Metadata(host, view, "scene_view");
        }

        private static JToken Capture(RoboVisionHost host, JObject parameters)
        {
            var view = RequireSceneView();
            var source = view.camera;
            var width = Mathf.Clamp(parameters.Value<int?>("width") ?? 1280, 64, 4096);
            var height = Mathf.Clamp(parameters.Value<int?>("height") ?? 720, 64, 4096);
            var requestedPath = parameters.Value<string>("path");
            var path = String.IsNullOrWhiteSpace(requestedPath)
                ? Path.Combine(Path.GetTempPath(), "robovision", $"unity-scene-{DateTime.UtcNow.Ticks}-{Guid.NewGuid():N}.png")
                : Path.GetFullPath(requestedPath);
            if (!String.Equals(Path.GetExtension(path), ".png", StringComparison.OrdinalIgnoreCase))
                throw new RoboVisionException("INVALID_PARAMS", "viewport capture path must end in .png");
            var directory = Path.GetDirectoryName(path);
            if (!String.IsNullOrEmpty(directory)) Directory.CreateDirectory(directory);

            var cameraObject = new GameObject("RoboVisionCaptureCamera") { hideFlags = HideFlags.HideAndDontSave };
            var camera = cameraObject.AddComponent<Camera>();
            var renderTexture = RenderTexture.GetTemporary(width, height, 24, RenderTextureFormat.ARGB32, RenderTextureReadWrite.Default);
            var texture = new Texture2D(width, height, TextureFormat.RGBA32, false, false) { hideFlags = HideFlags.HideAndDontSave };
            var oldActive = RenderTexture.active;
            try
            {
                camera.CopyFrom(source);
                camera.enabled = false;
                camera.targetTexture = renderTexture;
                camera.Render();
                RenderTexture.active = renderTexture;
                texture.ReadPixels(new Rect(0, 0, width, height), 0, 0, false);
                texture.Apply(false, false);
                File.WriteAllBytes(path, texture.EncodeToPNG());
            }
            catch (Exception ex)
            {
                throw new RoboVisionException("CAPTURE_FAILED", "Unity camera capture failed: " + ex.Message,
                    data: new JObject { ["render_pipeline"] = ActiveRenderPipelineName() });
            }
            finally
            {
                RenderTexture.active = oldActive;
                RenderTexture.ReleaseTemporary(renderTexture);
                UnityEngine.Object.DestroyImmediate(texture);
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }

            var file = new FileInfo(path);
            var metadata = Metadata(host, view, "scene_view_camera_copy");
            metadata["capture_width"] = width;
            metadata["capture_height"] = height;
            metadata["editor_overlays_included"] = false;
            metadata["active_render_pipeline"] = ActiveRenderPipelineName();
            metadata["capture_backend"] = "camera_render";
            metadata["fidelity_note"] = GraphicsSettings.currentRenderPipeline == null
                ? "Built-in pipeline camera render from a copy of the SceneView camera."
                : "SRP camera render from a copy of the SceneView camera; pipeline-specific post-processing may differ from the visible SceneView. Use an SRP render-request backend when exact SRP output is required.";

            return new JObject
            {
                ["artifact"] = new JObject { ["kind"] = "image", ["mime"] = "image/png", ["path"] = path, ["bytes"] = file.Length },
                ["view"] = metadata
            };
        }

        private static string ActiveRenderPipelineName()
        {
            var pipeline = GraphicsSettings.currentRenderPipeline;
            return pipeline == null ? "BuiltIn" : pipeline.GetType().AssemblyQualifiedName;
        }

        private static Bounds CalculateBounds(GameObject root)
        {
            var renderers = root.GetComponentsInChildren<Renderer>(true);
            if (renderers.Length == 0) return new Bounds(root.transform.position, Vector3.one);
            var bounds = renderers[0].bounds;
            for (var i = 1; i < renderers.Length; i++) bounds.Encapsulate(renderers[i].bounds);
            if (bounds.size.sqrMagnitude < 1e-8f) bounds.size = Vector3.one;
            return bounds;
        }

        private static JObject Metadata(RoboVisionHost host, SceneView view, string source)
        {
            var camera = view.camera;
            return new JObject
            {
                ["source"] = source,
                ["scene_revision"] = host.Revision,
                ["view_position_points"] = new JArray(view.position.x, view.position.y, view.position.width, view.position.height),
                ["camera_position"] = Vector3Token(camera.transform.position),
                ["camera_rotation"] = QuaternionToken(camera.transform.rotation),
                ["field_of_view"] = camera.fieldOfView,
                ["orthographic"] = camera.orthographic,
                ["orthographic_size"] = camera.orthographicSize,
                ["near_clip"] = camera.nearClipPlane,
                ["far_clip"] = camera.farClipPlane,
                ["aspect"] = camera.aspect,
                ["projection_matrix"] = MatrixToken(camera.projectionMatrix),
                ["world_to_camera_matrix"] = MatrixToken(camera.worldToCameraMatrix),
                ["camera_to_world_matrix"] = MatrixToken(camera.cameraToWorldMatrix),
                ["pivot"] = Vector3Token(view.pivot),
                ["size"] = view.size,
                ["in_2d_mode"] = view.in2DMode,
                ["camera_mode"] = view.cameraMode.name,
                ["active_render_pipeline"] = ActiveRenderPipelineName()
            };
        }

        private static JArray Vector3Token(Vector3 v) => new JArray(v.x, v.y, v.z);
        private static JArray QuaternionToken(Quaternion q) => new JArray(q.x, q.y, q.z, q.w);

        private static JArray MatrixToken(Matrix4x4 matrix)
        {
            var rows = new JArray();
            for (var row = 0; row < 4; row++)
                rows.Add(new JArray(matrix[row, 0], matrix[row, 1], matrix[row, 2], matrix[row, 3]));
            return rows;
        }
    }
}
