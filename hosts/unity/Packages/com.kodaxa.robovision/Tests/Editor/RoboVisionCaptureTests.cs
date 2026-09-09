using System.IO;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// Visual evidence: the pixels must correspond to the scene, not merely exist.
    /// </summary>
    /// <remarks>
    /// A capture that only proves a PNG was written is worth very little. These
    /// tests place a known colour in front of the camera and read the written
    /// image back, so a capture that silently rendered an empty frame, an
    /// unrelated view, or a stale buffer fails.
    ///
    /// viewport.capture needs a SceneView, which does not exist under
    /// -batchmode -nographics. In that configuration these tests report
    /// themselves as skipped rather than passing vacuously; run the testbed with
    /// --gui to execute them.
    /// </remarks>
    public sealed class RoboVisionCaptureTests
    {
        private Harness _rv;
        private string _artifact;

        [SetUp]
        public void SetUp()
        {
            Harness.FreshScene();
            _rv = new Harness("capture");
            _artifact = Path.Combine(Path.GetTempPath(), "robovision-capture-" + System.Guid.NewGuid().ToString("N") + ".png");
        }

        [TearDown]
        public void TearDown()
        {
            _rv.ResetTransactions();
            if (_artifact != null && File.Exists(_artifact)) File.Delete(_artifact);
        }

        private static UnityEditor.SceneView RequireSceneView()
        {
            if (Application.isBatchMode)
                Assert.Ignore("headless editor has no SceneView; run the testbed with --gui to cover viewport capture");

            var view = UnityEditor.SceneView.lastActiveSceneView;
            if (view == null)
            {
                // A freshly launched editor has no focused Scene view, so open
                // one rather than skipping coverage that this session can run.
                view = EditorWindow.GetWindow<UnityEditor.SceneView>();
                if (view != null) view.Focus();
            }
            if (view == null || view.camera == null)
                Assert.Ignore("no SceneView with a camera is available in this editor session");
            return view;
        }

        /// <summary>Fill the SceneView with an unlit quad of a known colour.</summary>
        private static GameObject PlaceColouredQuad(Color colour)
        {
            var quad = GameObject.CreatePrimitive(PrimitiveType.Quad);
            quad.name = "CaptureTarget";
            Undo.RegisterCreatedObjectUndo(quad, "capture target");

            var shader = Shader.Find("Unlit/Color");
            Assert.That(shader, Is.Not.Null, "Unlit/Color shader is unavailable in this project");
            var material = new Material(shader) { color = colour, hideFlags = HideFlags.HideAndDontSave };
            quad.GetComponent<MeshRenderer>().sharedMaterial = material;

            var view = RequireSceneView();
            view.orthographic = true;
            view.LookAt(quad.transform.position, Quaternion.identity, 0.5f);
            view.Repaint();
            return quad;
        }

        private static Color32[] ReadCapture(string path, out int width, out int height)
        {
            var bytes = File.ReadAllBytes(path);
            var texture = new Texture2D(2, 2, TextureFormat.RGBA32, false, false);
            Assert.That(texture.LoadImage(bytes), Is.True, "the capture artifact is not a readable PNG");
            width = texture.width;
            height = texture.height;
            var pixels = texture.GetPixels32();
            Object.DestroyImmediate(texture);
            return pixels;
        }

        private static Color32 CentrePixel(Color32[] pixels, int width, int height)
        {
            return pixels[(height / 2) * width + (width / 2)];
        }

        [Test]
        public void CaptureRecordsProvenanceThatMatchesTheRequest()
        {
            RequireSceneView();
            PlaceColouredQuad(Color.red);

            var result = _rv.Result("viewport.capture", new JObject
            {
                ["path"] = _artifact,
                ["width"] = 320,
                ["height"] = 240
            });

            Assert.That(File.Exists(_artifact), Is.True, "no capture artifact was written");
            var view = result["view"];
            Assert.That(view.Value<int>("capture_width"), Is.EqualTo(320));
            Assert.That(view.Value<int>("capture_height"), Is.EqualTo(240));
            Assert.That(view["camera_position"], Is.Not.Null, "the capture carries no camera position");

            ReadCapture(_artifact, out var width, out var height);
            Assert.That(width, Is.EqualTo(320), "the image is not the size the response claims");
            Assert.That(height, Is.EqualTo(240), "the image is not the size the response claims");
        }

        [Test]
        public void CapturedPixelsShowTheObjectInFrontOfTheCamera()
        {
            RequireSceneView();
            PlaceColouredQuad(Color.red);

            _rv.Call("viewport.capture", new JObject
            {
                ["path"] = _artifact,
                ["width"] = 256,
                ["height"] = 256
            });

            var pixels = ReadCapture(_artifact, out var width, out var height);
            var centre = CentrePixel(pixels, width, height);
            Assert.That(centre.r, Is.GreaterThan(centre.g + 40),
                "the centre of the capture is not the red quad placed in front of the camera; got " + centre);
            Assert.That(centre.r, Is.GreaterThan(centre.b + 40),
                "the centre of the capture is not the red quad placed in front of the camera; got " + centre);
        }

        [Test]
        public void ChangingTheSceneChangesWhatIsCaptured()
        {
            RequireSceneView();
            var quad = PlaceColouredQuad(Color.red);

            _rv.Call("viewport.capture", new JObject
            {
                ["path"] = _artifact, ["width"] = 128, ["height"] = 128
            });
            var before = CentrePixel(ReadCapture(_artifact, out var w1, out var h1), w1, h1);

            var material = quad.GetComponent<MeshRenderer>().sharedMaterial;
            material.color = Color.blue;
            UnityEditor.SceneView.lastActiveSceneView.Repaint();

            var second = Path.Combine(Path.GetTempPath(), "robovision-capture-second.png");
            try
            {
                _rv.Call("viewport.capture", new JObject
                {
                    ["path"] = second, ["width"] = 128, ["height"] = 128
                });
                var after = CentrePixel(ReadCapture(second, out var w2, out var h2), w2, h2);

                Assert.That(after.b, Is.GreaterThan(after.r + 40),
                    "the second capture does not show the blue quad; got " + after);
                Assert.That(before.r, Is.GreaterThan(before.b),
                    "the first capture did not show the red quad; got " + before);
            }
            finally
            {
                if (File.Exists(second)) File.Delete(second);
            }
        }

        [Test]
        public void FocusMovesTheViewToTheRequestedObject()
        {
            RequireSceneView();
            var id = _rv.CreateObject("FocusTarget", 12f, 0f, 0f);

            var focused = _rv.Result("viewport.focus", new JObject { ["object"] = id });
            var pivot = focused["pivot"];
            Assert.That(pivot, Is.Not.Null, "focus reported no pivot");
            Assert.That((float)pivot[0], Is.EqualTo(12f).Within(0.5f),
                "the view did not move to the requested object");
        }

        [Test]
        public void CaptureIsRefusedWithoutASceneView()
        {
            if (!Application.isBatchMode)
                Assert.Ignore("a windowed editor has a SceneView; the refusal path is covered by batchmode runs");

            // Headless: the host must say so plainly rather than write a blank image.
            _rv.Call("viewport.capture", new JObject { ["path"] = _artifact },
                ok: false, code: "INVALID_CONTEXT");
            Assert.That(File.Exists(_artifact), Is.False, "a refused capture still wrote an artifact");
        }
    }
}
