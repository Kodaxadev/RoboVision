using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace Kodaxa.RoboVision.Editor
{
    internal static class RoboVisionToolDocs
    {
        internal sealed class MethodDoc
        {
            public readonly string Summary;
            public readonly string[] Tags;
            public readonly JObject ParamsSchema;

            public MethodDoc(string summary, string[] tags, JObject paramsSchema)
            {
                Summary = summary ?? String.Empty;
                Tags = tags ?? Array.Empty<string>();
                ParamsSchema = paramsSchema ?? ObjectSchema();
            }
        }

        private static JObject StringSchema(string description = null)
        {
            var schema = new JObject { ["type"] = "string" };
            if (!String.IsNullOrWhiteSpace(description)) schema["description"] = description;
            return schema;
        }

        private static JObject BoolSchema() => new JObject { ["type"] = "boolean" };
        private static JObject NumberSchema() => new JObject { ["type"] = "number" };
        private static JObject IntegerSchema(int? minimum = null, int? maximum = null)
        {
            var schema = new JObject { ["type"] = "integer" };
            if (minimum.HasValue) schema["minimum"] = minimum.Value;
            if (maximum.HasValue) schema["maximum"] = maximum.Value;
            return schema;
        }

        private static JObject ArraySchema(JToken items, int? minItems = null, int? maxItems = null)
        {
            var schema = new JObject { ["type"] = "array", ["items"] = items };
            if (minItems.HasValue) schema["minItems"] = minItems.Value;
            if (maxItems.HasValue) schema["maxItems"] = maxItems.Value;
            return schema;
        }

        private static JObject Vec3() => ArraySchema(NumberSchema(), 3, 3);
        private static JObject Quat() => ArraySchema(NumberSchema(), 4, 4);
        private static JObject ObjectRef() => StringSchema("RoboVision Unity id: persistent unity:GlobalObjectId_* when available, otherwise an opaque session handle unity:session:<scope>:<n>, scoped to the loaded domain and never valid after a reload");

        private static JObject ObjectSchema(JObject properties = null, params string[] required)
        {
            var schema = new JObject
            {
                ["type"] = "object",
                ["properties"] = properties ?? new JObject(),
                ["additionalProperties"] = false
            };
            if (required != null && required.Length > 0) schema["required"] = new JArray(required);
            return schema;
        }

        private static MethodDoc Doc(string summary, string[] tags, JObject schema) => new MethodDoc(summary, tags, schema);

        private static readonly Dictionary<string, MethodDoc> Docs = new Dictionary<string, MethodDoc>(StringComparer.Ordinal)
        {
            ["system.ping"] = Doc("Check that the Unity host is responsive.", new[] { "system" }, ObjectSchema()),
            ["system.hello"] = Doc("Discover Unity/editor state, scene revision, security facts and the compact live capability catalog.", new[] { "system", "discovery" }, ObjectSchema()),
            ["system.capabilities"] = Doc(
                "Search and page the live Unity operation catalog; optionally include exact parameter schemas.",
                new[] { "system", "discovery" },
                ObjectSchema(new JObject
                {
                    ["query"] = StringSchema(),
                    ["prefix"] = StringSchema(),
                    ["tags"] = ArraySchema(StringSchema()),
                    ["include_schema"] = BoolSchema(),
                    ["offset"] = IntegerSchema(0),
                    ["limit"] = IntegerSchema(1, 500)
                })),
            ["system.method"] = Doc(
                "Describe one Unity operation including its exact parameter schema.",
                new[] { "system", "discovery" },
                ObjectSchema(new JObject { ["method"] = StringSchema() }, "method")),

            ["scene.describe"] = Doc("Inspect all loaded Unity scenes and GameObjects with stable editor identities.", new[] { "scene", "inspect" }, ObjectSchema()),
            ["scene.snapshot"] = Doc("Store a fingerprinted Unity scene snapshot for later diff or rollback verification.", new[] { "scene", "inspect", "history" }, ObjectSchema()),
            ["scene.diff"] = Doc(
                "Compare current Unity scene state with a stored RoboVision snapshot.",
                new[] { "scene", "inspect", "history" },
                ObjectSchema(new JObject { ["from_snapshot"] = StringSchema() }, "from_snapshot")),

            ["object.inspect"] = Doc(
                "Inspect one Unity GameObject including hierarchy, local transform and component types.",
                new[] { "object", "inspect" },
                ObjectSchema(new JObject { ["object"] = ObjectRef() }, "object")),
            ["object.create"] = Doc(
                "Create a Unity GameObject with optional local transform through Undo-aware editor APIs.",
                new[] { "object", "create", "scene" },
                ObjectSchema(new JObject
                {
                    ["name"] = StringSchema(),
                    ["local_position"] = Vec3(),
                    ["local_rotation"] = Quat(),
                    ["local_scale"] = Vec3()
                })),
            ["object.delete"] = Doc(
                "Delete a Unity GameObject through Undo-aware editor APIs.",
                new[] { "object", "scene" },
                ObjectSchema(new JObject { ["object"] = ObjectRef() }, "object")),
            ["object.transform"] = Doc(
                "Set a GameObject's local position, quaternion rotation and/or scale through SerializedObject.",
                new[] { "object", "transform", "scene" },
                ObjectSchema(new JObject
                {
                    ["object"] = ObjectRef(),
                    ["local_position"] = Vec3(),
                    ["local_rotation"] = Quat(),
                    ["local_scale"] = Vec3()
                }, "object")),

            ["component.list"] = Doc(
                "List components attached to one GameObject with RoboVision object identities.",
                new[] { "component", "inspect" },
                ObjectSchema(new JObject { ["object"] = ObjectRef() }, "object")),
            ["component.add"] = Doc(
                "Add a non-abstract Unity Component by exact/full type name through Undo.AddComponent.",
                new[] { "component", "scene" },
                ObjectSchema(new JObject { ["object"] = ObjectRef(), ["type"] = StringSchema() }, "object", "type")),
            ["component.remove"] = Doc(
                "Remove a Unity Component through Undo, excluding Transform.",
                new[] { "component", "scene" },
                ObjectSchema(new JObject { ["component"] = ObjectRef() }, "component")),

            ["serialized.inspect"] = Doc(
                "Page visible or hidden SerializedProperty metadata and values for any RoboVision-addressable Unity object/component.",
                new[] { "serialized", "inspect", "component" },
                ObjectSchema(new JObject
                {
                    ["target"] = ObjectRef(),
                    ["include_hidden"] = BoolSchema(),
                    ["offset"] = IntegerSchema(0),
                    ["limit"] = IntegerSchema(1, 2000)
                }, "target")),
            ["serialized.get"] = Doc(
                "Read one SerializedProperty by exact property path.",
                new[] { "serialized", "inspect" },
                ObjectSchema(new JObject { ["target"] = ObjectRef(), ["path"] = StringSchema() }, "target", "path")),
            ["serialized.set"] = Doc(
                "Set one supported SerializedProperty using Unity's SerializedObject/Undo/Prefab-override semantics.",
                new[] { "serialized", "scene", "component" },
                ObjectSchema(new JObject
                {
                    ["target"] = ObjectRef(),
                    ["path"] = StringSchema(),
                    ["value"] = new JObject { ["description"] = "JSON value compatible with the property's SerializedPropertyType" }
                }, "target", "path", "value")),

            ["viewport.inspect"] = Doc("Inspect the active SceneView camera/projection and render-pipeline metadata.", new[] { "viewport", "perception" }, ObjectSchema()),
            ["viewport.focus"] = Doc(
                "Frame one GameObject in the active SceneView without mutating scene state.",
                new[] { "viewport", "perception", "navigation" },
                ObjectSchema(new JObject { ["object"] = ObjectRef() }, "object")),
            ["viewport.capture"] = Doc(
                "Render a copy of the active SceneView camera to PNG with exact camera/projection provenance.",
                new[] { "viewport", "perception", "evidence" },
                ObjectSchema(new JObject
                {
                    ["path"] = StringSchema(),
                    ["width"] = IntegerSchema(64, 4096),
                    ["height"] = IntegerSchema(64, 4096)
                })),

            ["transaction.begin"] = Doc(
                "Begin a verified Unity Undo transaction and record its scene fingerprint.",
                new[] { "transaction", "history", "safety" },
                ObjectSchema(new JObject { ["transaction"] = StringSchema(), ["label"] = StringSchema() })),
            ["transaction.commit"] = Doc(
                "Commit the active Unity transaction, refusing externally contaminated state unless explicitly forced.",
                new[] { "transaction", "history", "safety" },
                ObjectSchema(new JObject { ["transaction"] = StringSchema(), ["force"] = BoolSchema() }, "transaction")),
            ["transaction.rollback"] = Doc(
                "Undo until the transaction begin fingerprint is restored and verified.",
                new[] { "transaction", "history", "safety" },
                ObjectSchema(new JObject
                {
                    ["transaction"] = StringSchema(),
                    ["max_steps"] = IntegerSchema(1, 512),
                    ["force"] = BoolSchema()
                }, "transaction"))
        };

        public static MethodDoc Get(string method)
        {
            Docs.TryGetValue(method, out var doc);
            return doc;
        }
    }
}
