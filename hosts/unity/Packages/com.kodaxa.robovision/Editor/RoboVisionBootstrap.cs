using System;
using UnityEditor;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor
{
    [InitializeOnLoad]
    internal static class RoboVisionBootstrap
    {
        private const string AutoStartKey = "Kodaxa.RoboVision.AutoStart";
        private const string PortKey = "Kodaxa.RoboVision.Port";

        static RoboVisionBootstrap()
        {
            AssemblyReloadEvents.beforeAssemblyReload -= Stop;
            AssemblyReloadEvents.beforeAssemblyReload += Stop;
            EditorApplication.quitting -= Stop;
            EditorApplication.quitting += Stop;
            EditorApplication.delayCall += AutoStart;
        }

        private static void AutoStart()
        {
            if (EditorPrefs.GetBool(AutoStartKey, true)) Start();
        }

        [MenuItem("Tools/RoboVision/Start", priority = 0)]
        private static void Start()
        {
            if (RoboVisionHost.Instance.Running) return;
            var port = Mathf.Clamp(EditorPrefs.GetInt(PortKey, RoboVisionHost.DefaultPort), 1024, 65535);
            try
            {
                RoboVisionHost.Instance.Start(port);
                Debug.Log($"RoboVision listening on 127.0.0.1:{port}");
            }
            catch (Exception ex)
            {
                Debug.LogError("RoboVision failed to start: " + ex.Message);
            }
        }

        [MenuItem("Tools/RoboVision/Start", validate = true)]
        private static bool ValidateStart() => !RoboVisionHost.Instance.Running;

        [MenuItem("Tools/RoboVision/Stop", priority = 1)]
        private static void Stop() => RoboVisionHost.Instance.Stop();

        [MenuItem("Tools/RoboVision/Stop", validate = true)]
        private static bool ValidateStop() => RoboVisionHost.Instance.Running;

        [MenuItem("Tools/RoboVision/Toggle Auto Start", priority = 20)]
        private static void ToggleAutoStart()
        {
            var next = !EditorPrefs.GetBool(AutoStartKey, true);
            EditorPrefs.SetBool(AutoStartKey, next);
            Menu.SetChecked("Tools/RoboVision/Toggle Auto Start", next);
        }

        [MenuItem("Tools/RoboVision/Toggle Auto Start", validate = true)]
        private static bool ValidateAutoStart()
        {
            Menu.SetChecked("Tools/RoboVision/Toggle Auto Start", EditorPrefs.GetBool(AutoStartKey, true));
            return true;
        }
    }
}
