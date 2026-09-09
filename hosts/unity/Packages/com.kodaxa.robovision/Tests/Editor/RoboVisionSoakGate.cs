using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Kodaxa.RoboVision.Editor.Tests
{
    /// <summary>
    /// A long-lived soak in one editor process, with domain reloads mixed in.
    /// </summary>
    /// <remarks>
    /// Starting a clean editor five hundred times says little. What matters is
    /// whether one editor still behaves after five hundred cycles have piled up
    /// in it: undo stacks, session handle tables, snapshot caches, sockets and
    /// managed memory all accumulate, and a leak only shows under repetition.
    ///
    /// The driver therefore keeps its state in SessionState and on disk rather
    /// than in fields, so it can request a script reload part-way through and
    /// resume in the rebuilt domain. That makes the reloads part of the soak
    /// instead of something tested separately in isolation.
    /// </remarks>
    public static class RoboVisionSoakGate
    {
        private const string ActiveKey = "RoboVision.Soak.Active";
        private const string CycleKey = "RoboVision.Soak.Cycle";
        private const string BaselineKey = "RoboVision.Soak.Baseline";
        private const string BystanderKey = "RoboVision.Soak.Bystander";
        private const string ReloadsKey = "RoboVision.Soak.Reloads";
        private const string SceneDirectory = "Assets/RoboVisionSoak";
        private const string ScenePath = SceneDirectory + "/Soak.unity";

        private static string ReportPath =>
            Environment.GetEnvironmentVariable("ROBOVISION_SOAK_REPORT")
            ?? Path.Combine(Path.GetTempPath(), "robovision-soak.json");

        private static int TotalCycles =>
            int.TryParse(Environment.GetEnvironmentVariable("ROBOVISION_SOAK_CYCLES"), out var value) && value > 0
                ? value : 50;

        /// <summary>Cycles between domain reloads; 0 disables them.</summary>
        private static int ReloadEvery =>
            int.TryParse(Environment.GetEnvironmentVariable("ROBOVISION_SOAK_RELOAD_EVERY"), out var value) && value >= 0
                ? value : 0;

        private static JObject Call(string method, JObject parameters = null)
        {
            return RoboVisionHost.Instance.Dispatch(new JObject
            {
                ["rv"] = "1.0",
                ["id"] = "soak-" + Guid.NewGuid().ToString("N").Substring(0, 8),
                ["method"] = method,
                ["params"] = parameters ?? new JObject()
            });
        }

        private static JObject Ok(string method, JObject parameters = null)
        {
            var response = Call(method, parameters);
            if (!response.Value<bool>("ok"))
                throw new Exception(method + " failed: " + response.ToString(Formatting.None));
            return (JObject)response["result"];
        }

        /// <summary>Entry point for -executeMethod.</summary>
        public static void Run()
        {
            SessionState.SetBool(ActiveKey, true);
            SessionState.SetInt(CycleKey, 0);
            SessionState.SetInt(ReloadsKey, 0);
            File.WriteAllText(ReportPath, new JObject { ["state"] = "starting" }.ToString(Formatting.Indented));

            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            if (!Directory.Exists(SceneDirectory)) Directory.CreateDirectory(SceneDirectory);
            AssetDatabase.Refresh();

            // A bystander created before the soak must be untouched at the end.
            var bystander = Ok("object.create", new JObject
            {
                ["name"] = "SoakBystander",
                ["local_position"] = new JArray(11f, 0f, 0f)
            }).Value<string>("id");
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), ScenePath);

            var durable = Ok("object.inspect", new JObject { ["object"] = bystander }).Value<string>("id");
            SessionState.SetString(BystanderKey, durable);
            SessionState.SetString(BaselineKey, Ok("scene.snapshot").Value<string>("fingerprint"));

            Continue();
        }

        [InitializeOnLoadMethod]
        private static void ResumeAfterReload()
        {
            if (!SessionState.GetBool(ActiveKey, false)) return;
            // The domain has just been rebuilt; let the editor settle before
            // driving it again.
            EditorApplication.delayCall += () =>
            {
                SessionState.SetInt(ReloadsKey, SessionState.GetInt(ReloadsKey, 0) + 1);
                Continue();
            };
        }

        private static void Continue()
        {
            var report = LoadReport();
            var failures = report["failures"] as JArray ?? new JArray();
            var durations = (report["durations_ms"] as JArray) ?? new JArray();
            var baseline = SessionState.GetString(BaselineKey, null);
            var bystander = SessionState.GetString(BystanderKey, null);
            var cycle = SessionState.GetInt(CycleKey, 0);
            var total = TotalCycles;
            var reloadEvery = ReloadEvery;

            var objectCount = SceneManager.GetActiveScene().rootCount;
            var startedRevision = RoboVisionHost.Instance.Revision;

            try
            {
                while (cycle < total)
                {
                    var watch = Stopwatch.StartNew();
                    RunCycle(cycle, baseline, failures);
                    watch.Stop();
                    durations.Add(Math.Round(watch.Elapsed.TotalMilliseconds, 3));

                    cycle++;
                    SessionState.SetInt(CycleKey, cycle);

                    if (reloadEvery > 0 && cycle < total && cycle % reloadEvery == 0)
                    {
                        // Persist before the domain goes away, then come back
                        // through ResumeAfterReload and keep going in the same
                        // process with everything that has accumulated.
                        Save(report, failures, durations, cycle, "reloading");
                        EditorUtility.RequestScriptReload();
                        return;
                    }
                }
            }
            catch (Exception ex)
            {
                failures.Add("cycle " + cycle + " threw " + ex.GetType().Name + ": " + ex.Message);
            }

            // Final accounting once every cycle has run.
            try
            {
                var finalFingerprint = Ok("scene.snapshot").Value<string>("fingerprint");
                if (finalFingerprint != baseline)
                    failures.Add("final fingerprint " + finalFingerprint + " != baseline " + baseline);

                var bystanderState = Call("object.inspect", new JObject { ["object"] = bystander });
                if (!bystanderState.Value<bool>("ok"))
                    failures.Add("the bystander no longer resolves: " + bystanderState.ToString(Formatting.None));
                else if (bystanderState["result"].Value<string>("name") != "SoakBystander")
                    failures.Add("the bystander resolves to the wrong object");

                if (RoboVisionHost.Instance.Transactions.Active)
                    failures.Add("a transaction was still active at the end of the soak");

                if (SceneManager.GetActiveScene().rootCount != objectCount)
                    failures.Add("scene root count drifted from " + objectCount
                        + " to " + SceneManager.GetActiveScene().rootCount);

                if (RoboVisionHost.Instance.Revision < startedRevision)
                    failures.Add("the scene revision went backwards");

                report["final_fingerprint"] = finalFingerprint;
                report["baseline"] = baseline;
                report["revision"] = RoboVisionHost.Instance.Revision;
                report["scene_roots"] = SceneManager.GetActiveScene().rootCount;
                report["leaked_gameobjects"] = Resources.FindObjectsOfTypeAll<GameObject>()
                    .Count(go => go != null && go.name.StartsWith("Soak", StringComparison.Ordinal)
                                 && go.name != "SoakBystander");
            }
            catch (Exception ex)
            {
                failures.Add("final accounting threw " + ex.GetType().Name + ": " + ex.Message);
            }

            Save(report, failures, durations, cycle, failures.Count == 0 ? "passed" : "failed");
            SessionState.SetBool(ActiveKey, false);
            UnityEngine.Debug.Log("ROBOVISION_SOAK_DONE " + report.ToString(Formatting.None));
            EditorApplication.Exit(failures.Count == 0 ? 0 : 1);
        }

        /// <summary>One observe/mutate/verify/roll-back cycle.</summary>
        private static void RunCycle(int index, string baseline, JArray failures)
        {
            var before = Ok("scene.snapshot").Value<string>("fingerprint");
            if (before != baseline)
            {
                failures.Add("cycle " + index + " started from " + before + ", not the baseline");
                return;
            }

            var tx = Ok("transaction.begin", new JObject { ["label"] = "soak-" + index })
                .Value<string>("transaction");

            var id = Ok("object.create", new JObject
            {
                ["name"] = "Soak" + index,
                ["local_position"] = new JArray(index % 5, 0f, 0f)
            }).Value<string>("id");

            Ok("object.transform", new JObject
            {
                ["object"] = id,
                ["local_position"] = new JArray(0f, index % 3, 0f)
            });

            Ok("component.add", new JObject { ["object"] = id, ["type"] = "UnityEngine.BoxCollider" });
            var componentId = Ok("component.list", new JObject { ["object"] = id })["components"]
                .First(entry => entry.Value<string>("type") == "UnityEngine.BoxCollider")
                .Value<string>("id");
            Ok("serialized.set", new JObject
            {
                ["target"] = componentId,
                ["path"] = "m_IsTrigger",
                ["value"] = index % 2 == 0
            });

            var inspected = Ok("object.inspect", new JObject { ["object"] = id });
            if (inspected.Value<string>("name") != "Soak" + index)
                failures.Add("cycle " + index + " inspected the wrong object");

            var rolled = Ok("transaction.rollback", new JObject { ["transaction"] = tx });
            if (!rolled.Value<bool>("rolled_back"))
                failures.Add("cycle " + index + " rollback did not report success");

            var after = Ok("scene.snapshot").Value<string>("fingerprint");
            if (after != baseline)
                failures.Add("cycle " + index + " did not restore the baseline fingerprint");

            if (GameObject.Find("Soak" + index) != null)
                failures.Add("cycle " + index + " left its object behind");
        }

        private static JObject LoadReport()
        {
            try
            {
                if (File.Exists(ReportPath))
                {
                    var existing = JObject.Parse(File.ReadAllText(ReportPath));
                    if (existing["failures"] != null) return existing;
                }
            }
            catch (Exception)
            {
                // A partial report from an interrupted run is not worth keeping.
            }
            return new JObject { ["failures"] = new JArray(), ["durations_ms"] = new JArray() };
        }

        private static void Save(JObject report, JArray failures, JArray durations, int cycle, string state)
        {
            report["failures"] = failures;
            report["durations_ms"] = durations;
            report["cycles_completed"] = cycle;
            report["cycles_requested"] = TotalCycles;
            report["reloads"] = SessionState.GetInt(ReloadsKey, 0);
            report["state"] = state;
            report["mono_heap_bytes"] = UnityEngine.Profiling.Profiler.GetMonoUsedSizeLong();
            report["gc_total_bytes"] = GC.GetTotalMemory(false);
            report["undo_group"] = Undo.GetCurrentGroup();
            report["host_running"] = RoboVisionHost.Instance.Running;

            var values = durations.Select(v => (double)v).OrderBy(v => v).ToList();
            if (values.Count > 0)
            {
                report["latency_ms"] = new JObject
                {
                    ["mean"] = Math.Round(values.Average(), 2),
                    ["p50"] = Math.Round(values[values.Count / 2], 2),
                    ["p95"] = Math.Round(values[(int)(values.Count * 0.95) >= values.Count
                        ? values.Count - 1 : (int)(values.Count * 0.95)], 2),
                    ["max"] = Math.Round(values[values.Count - 1], 2)
                };
            }
            File.WriteAllText(ReportPath, report.ToString(Formatting.Indented));
        }
    }
}
