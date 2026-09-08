using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace Kodaxa.RoboVision.Editor
{
    internal static class RoboVisionSerializedTools
    {
        public static void Register(RoboVisionHost host)
        {
            host.AddTool("component.list", ListComponents, stability: "beta");
            host.AddTool("component.add", AddComponent, mutating: true, stability: "alpha");
            host.AddTool("component.remove", RemoveComponent, mutating: true, stability: "alpha");
            host.AddTool("serialized.inspect", Inspect, stability: "beta");
            host.AddTool("serialized.get", Get, stability: "beta");
            host.AddTool("serialized.set", Set, mutating: true, stability: "alpha");
        }

        private static JToken ListComponents(JObject parameters)
        {
            var go = RoboVisionSceneTools.ResolveGameObject(parameters.Value<string>("object"));
            return new JObject
            {
                ["object"] = IdFor(go),
                ["components"] = new JArray(go.GetComponents<Component>()
                    .Where(component => component != null)
                    .Select(ComponentDescriptor))
            };
        }

        private static JToken AddComponent(JObject parameters)
        {
            var go = RoboVisionSceneTools.ResolveGameObject(parameters.Value<string>("object"));
            var requested = parameters.Value<string>("type");
            if (String.IsNullOrWhiteSpace(requested))
                throw new RoboVisionException("INVALID_PARAMS", "type is required");
            var type = ResolveComponentType(requested);
            try
            {
                return ComponentDescriptor(Undo.AddComponent(go, type));
            }
            catch (Exception ex)
            {
                throw new RoboVisionException("INVALID_PARAMS", "Unity could not add component " + type.FullName + ": " + ex.Message);
            }
        }

        private static JToken RemoveComponent(JObject parameters)
        {
            var component = ResolveObject(parameters.Value<string>("component")) as Component;
            if (component == null)
                throw new RoboVisionException("INVALID_PARAMS", "component must resolve to a Component");
            if (component is Transform)
                throw new RoboVisionException("UNSUPPORTED", "Transform cannot be removed");
            var descriptor = ComponentDescriptor(component);
            try
            {
                Undo.DestroyObjectImmediate(component);
            }
            catch (Exception ex)
            {
                throw new RoboVisionException("INVALID_CONTEXT", "Unity could not remove component: " + ex.Message);
            }
            return new JObject { ["removed"] = descriptor };
        }

        private static JToken Inspect(JObject parameters)
        {
            var target = ResolveObject(parameters.Value<string>("target"));
            var includeHidden = parameters.Value<bool?>("include_hidden") ?? false;
            var offset = Math.Max(0, parameters.Value<int?>("offset") ?? 0);
            var limit = Math.Max(1, Math.Min(2000, parameters.Value<int?>("limit") ?? 500));
            var properties = Enumerate(target, includeHidden);
            return new JObject
            {
                ["target"] = IdFor(target),
                ["type"] = target.GetType().FullName,
                ["offset"] = offset,
                ["limit"] = limit,
                ["total"] = properties.Count,
                ["properties"] = new JArray(properties.Skip(offset).Take(limit)),
                ["has_more"] = offset + limit < properties.Count
            };
        }

        private static JToken Get(JObject parameters)
        {
            var target = ResolveObject(parameters.Value<string>("target"));
            var path = RequiredString(parameters, "path");
            var serialized = new SerializedObject(target);
            serialized.UpdateIfRequiredOrScript();
            var property = serialized.FindProperty(path);
            if (property == null)
                throw new RoboVisionException("NOT_FOUND", "serialized property not found: " + path);
            return PropertyDescriptor(property);
        }

        private static JToken Set(JObject parameters)
        {
            var target = ResolveObject(parameters.Value<string>("target"));
            var path = RequiredString(parameters, "path");
            if (path == "m_Script")
                throw new RoboVisionException("UNSUPPORTED", "serialized.set does not replace MonoBehaviour script references");
            if (!parameters.TryGetValue("value", out var value))
                throw new RoboVisionException("INVALID_PARAMS", "value is required");

            var serialized = new SerializedObject(target);
            serialized.UpdateIfRequiredOrScript();
            var property = serialized.FindProperty(path);
            if (property == null)
                throw new RoboVisionException("NOT_FOUND", "serialized property not found: " + path);
            WriteProperty(property, value);
            var changed = serialized.ApplyModifiedProperties();
            serialized.UpdateIfRequiredOrScript();
            property = serialized.FindProperty(path);
            return new JObject { ["changed"] = changed, ["property"] = PropertyDescriptor(property) };
        }

        private static List<JObject> Enumerate(UnityEngine.Object target, bool includeHidden)
        {
            var serialized = new SerializedObject(target);
            serialized.UpdateIfRequiredOrScript();
            var iterator = serialized.GetIterator();
            var result = new List<JObject>();
            var enterChildren = true;
            while (includeHidden ? iterator.Next(enterChildren) : iterator.NextVisible(enterChildren))
            {
                enterChildren = false;
                result.Add(PropertyDescriptor(iterator.Copy()));
            }
            return result;
        }

        private static JObject ComponentDescriptor(Component component)
        {
            return new JObject
            {
                ["id"] = IdFor(component),
                ["type"] = component.GetType().FullName,
                ["game_object"] = IdFor(component.gameObject),
                ["name"] = component.name,
                ["enabled"] = component is Behaviour behaviour ? (JToken)behaviour.enabled : JValue.CreateNull()
            };
        }

        private static Type ResolveComponentType(string requested)
        {
            var matches = TypeCache.GetTypesDerivedFrom<Component>()
                .Where(type => !type.IsAbstract &&
                    (String.Equals(type.FullName, requested, StringComparison.Ordinal) ||
                     String.Equals(type.Name, requested, StringComparison.Ordinal)))
                .ToArray();
            if (matches.Length == 0)
                throw new RoboVisionException("NOT_FOUND", "component type not found: " + requested);
            if (matches.Length > 1)
                throw new RoboVisionException(
                    "INVALID_PARAMS",
                    "component type name is ambiguous; use the full type name",
                    data: new JArray(matches.Select(type => type.FullName).OrderBy(name => name, StringComparer.Ordinal)));
            return matches[0];
        }

        private static UnityEngine.Object ResolveObject(string reference)
        {
            if (String.IsNullOrWhiteSpace(reference))
                throw new RoboVisionException("INVALID_PARAMS", "target reference is required");
            if (reference.StartsWith("unity:GlobalObjectId_", StringComparison.Ordinal))
            {
                var raw = reference.Substring("unity:".Length);
                if (!GlobalObjectId.TryParse(raw, out var gid))
                    throw new RoboVisionException("INVALID_PARAMS", "invalid GlobalObjectId");
                var resolved = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(gid);
                if (resolved == null)
                    throw new RoboVisionException("NOT_FOUND", "Unity object no longer resolves: " + reference);
                return resolved;
            }
            if (reference.StartsWith("unity:instance:", StringComparison.Ordinal) &&
                Int32.TryParse(reference.Substring("unity:instance:".Length), out var instanceId))
            {
                var resolved = EditorUtility.InstanceIDToObject(instanceId);
                if (resolved == null)
                    throw new RoboVisionException("NOT_FOUND", "Unity instance no longer resolves: " + reference);
                return resolved;
            }
            throw new RoboVisionException("INVALID_PARAMS", "target must be a RoboVision Unity id");
        }

        private static string IdFor(UnityEngine.Object obj)
        {
            if (obj == null) return null;
            var text = GlobalObjectId.GetGlobalObjectIdSlow(obj).ToString();
            return text.StartsWith("GlobalObjectId_V1-0-", StringComparison.Ordinal)
                ? "unity:instance:" + obj.GetInstanceID()
                : "unity:" + text;
        }

        private static JObject PropertyDescriptor(SerializedProperty property)
        {
            var result = new JObject
            {
                ["path"] = property.propertyPath,
                ["name"] = property.name,
                ["display_name"] = property.displayName,
                ["depth"] = property.depth,
                ["type"] = property.type,
                ["property_type"] = property.propertyType.ToString(),
                ["numeric_type"] = property.numericType.ToString(),
                ["is_array"] = property.isArray,
                ["has_visible_children"] = property.hasVisibleChildren,
                ["value"] = ReadProperty(property)
            };
            if (property.isArray) result["array_size"] = property.arraySize;
            return result;
        }

        private static JToken ReadProperty(SerializedProperty property)
        {
            switch (property.propertyType)
            {
                case SerializedPropertyType.Integer:
                    return IsUnsigned(property.numericType) ? (JToken)property.ulongValue : property.longValue;
                case SerializedPropertyType.Boolean: return property.boolValue;
                case SerializedPropertyType.Float:
                    return property.numericType == SerializedPropertyNumericType.Double ? (JToken)property.doubleValue : property.floatValue;
                case SerializedPropertyType.String: return property.stringValue;
                case SerializedPropertyType.Color: return ColorToken(property.colorValue);
                case SerializedPropertyType.ObjectReference:
                    return property.objectReferenceValue == null ? JValue.CreateNull() : ObjectReferenceToken(property.objectReferenceValue);
                case SerializedPropertyType.LayerMask: return property.intValue;
                case SerializedPropertyType.Enum:
                    return new JObject
                    {
                        ["index"] = property.enumValueIndex,
                        ["name"] = property.enumValueIndex >= 0 && property.enumValueIndex < property.enumNames.Length
                            ? property.enumNames[property.enumValueIndex] : null,
                        ["names"] = new JArray(property.enumNames)
                    };
                case SerializedPropertyType.Vector2: return Vector2Token(property.vector2Value);
                case SerializedPropertyType.Vector3: return Vector3Token(property.vector3Value);
                case SerializedPropertyType.Vector4: return Vector4Token(property.vector4Value);
                case SerializedPropertyType.Rect: return RectToken(property.rectValue);
                case SerializedPropertyType.ArraySize: return property.intValue;
                case SerializedPropertyType.Character: return property.intValue;
                case SerializedPropertyType.Bounds: return BoundsToken(property.boundsValue);
                case SerializedPropertyType.Quaternion: return QuaternionToken(property.quaternionValue);
                case SerializedPropertyType.Vector2Int: return Vector2IntToken(property.vector2IntValue);
                case SerializedPropertyType.Vector3Int: return Vector3IntToken(property.vector3IntValue);
                case SerializedPropertyType.RectInt: return RectIntToken(property.rectIntValue);
                case SerializedPropertyType.BoundsInt: return BoundsIntToken(property.boundsIntValue);
                case SerializedPropertyType.ManagedReference:
                    return new JObject
                    {
                        ["managed_reference_type"] = property.managedReferenceFullTypename,
                        ["supported_for_write"] = false
                    };
                case SerializedPropertyType.Hash128: return property.hash128Value.ToString();
                case SerializedPropertyType.Generic:
                    return property.isArray
                        ? new JObject { ["array_size"] = property.arraySize, ["expand_with"] = "serialized.inspect" }
                        : JValue.CreateNull();
                default:
                    return new JObject { ["unsupported_value_type"] = property.propertyType.ToString() };
            }
        }

        private static void WriteProperty(SerializedProperty property, JToken value)
        {
            if (property.isArray && property.propertyType == SerializedPropertyType.Generic && value is JArray values)
            {
                property.arraySize = values.Count;
                for (var i = 0; i < values.Count; i++)
                    WriteProperty(property.GetArrayElementAtIndex(i), values[i]);
                return;
            }

            switch (property.propertyType)
            {
                case SerializedPropertyType.Integer:
                    if (IsUnsigned(property.numericType)) property.ulongValue = value.Value<ulong>();
                    else property.longValue = value.Value<long>();
                    return;
                case SerializedPropertyType.Boolean: property.boolValue = value.Value<bool>(); return;
                case SerializedPropertyType.Float:
                    if (property.numericType == SerializedPropertyNumericType.Double) property.doubleValue = value.Value<double>();
                    else property.floatValue = value.Value<float>();
                    return;
                case SerializedPropertyType.String:
                    property.stringValue = value.Type == JTokenType.Null ? null : value.Value<string>();
                    return;
                case SerializedPropertyType.Color: property.colorValue = ReadColor(value); return;
                case SerializedPropertyType.ObjectReference:
                    property.objectReferenceValue = value.Type == JTokenType.Null ? null : ResolveObject(ReferenceId(value));
                    return;
                case SerializedPropertyType.LayerMask: property.intValue = value.Value<int>(); return;
                case SerializedPropertyType.Enum:
                    WriteEnum(property, value);
                    return;
                case SerializedPropertyType.Vector2: property.vector2Value = ReadVector2(value); return;
                case SerializedPropertyType.Vector3: property.vector3Value = ReadVector3(value); return;
                case SerializedPropertyType.Vector4: property.vector4Value = ReadVector4(value); return;
                case SerializedPropertyType.Rect: property.rectValue = ReadRect(value); return;
                case SerializedPropertyType.ArraySize: property.intValue = value.Value<int>(); return;
                case SerializedPropertyType.Character: property.intValue = value.Value<int>(); return;
                case SerializedPropertyType.Bounds: property.boundsValue = ReadBounds(value); return;
                case SerializedPropertyType.Quaternion: property.quaternionValue = ReadQuaternion(value); return;
                case SerializedPropertyType.Vector2Int: property.vector2IntValue = ReadVector2Int(value); return;
                case SerializedPropertyType.Vector3Int: property.vector3IntValue = ReadVector3Int(value); return;
                case SerializedPropertyType.RectInt: property.rectIntValue = ReadRectInt(value); return;
                case SerializedPropertyType.BoundsInt: property.boundsIntValue = ReadBoundsInt(value); return;
                case SerializedPropertyType.Hash128:
                    try { property.hash128Value = Hash128.Parse(value.Value<string>()); }
                    catch (Exception ex) { throw new RoboVisionException("INVALID_PARAMS", "invalid Hash128 string: " + ex.Message); }
                    return;
                case SerializedPropertyType.ManagedReference:
                    throw new RoboVisionException("UNSUPPORTED", "managed-reference construction requires a typed operation");
                default:
                    throw new RoboVisionException("UNSUPPORTED", "serialized property type is not safely writable: " + property.propertyType);
            }
        }

        private static void WriteEnum(SerializedProperty property, JToken value)
        {
            if (value.Type != JTokenType.String)
            {
                property.enumValueIndex = value.Value<int>();
                return;
            }
            var requested = value.Value<string>();
            var index = Array.IndexOf(property.enumNames, requested);
            if (index < 0)
                throw new RoboVisionException("INVALID_PARAMS", "enum value not found: " + requested, data: new JArray(property.enumNames));
            property.enumValueIndex = index;
        }

        private static bool IsUnsigned(SerializedPropertyNumericType type)
        {
            return type == SerializedPropertyNumericType.UInt8 ||
                   type == SerializedPropertyNumericType.UInt16 ||
                   type == SerializedPropertyNumericType.UInt32 ||
                   type == SerializedPropertyNumericType.UInt64;
        }

        private static string RequiredString(JObject parameters, string key)
        {
            var value = parameters.Value<string>(key);
            if (String.IsNullOrWhiteSpace(value))
                throw new RoboVisionException("INVALID_PARAMS", key + " is required");
            return value;
        }

        private static string ReferenceId(JToken value)
        {
            if (value.Type == JTokenType.String) return value.Value<string>();
            var id = value["id"]?.Value<string>();
            if (String.IsNullOrWhiteSpace(id))
                throw new RoboVisionException("INVALID_PARAMS", "object reference value must be an id string or {id}");
            return id;
        }

        private static JObject ObjectReferenceToken(UnityEngine.Object value)
        {
            return new JObject { ["id"] = IdFor(value), ["type"] = value.GetType().FullName, ["name"] = value.name };
        }

        private static JArray RequireArray(JToken token, int count, string name)
        {
            var array = token as JArray;
            if (array == null || array.Count != count)
                throw new RoboVisionException("INVALID_PARAMS", name + " must be an array of " + count + " numbers");
            return array;
        }

        private static Vector2 ReadVector2(JToken t) { var a = RequireArray(t, 2, "Vector2"); return new Vector2(a[0].Value<float>(), a[1].Value<float>()); }
        private static Vector3 ReadVector3(JToken t) { var a = RequireArray(t, 3, "Vector3"); return new Vector3(a[0].Value<float>(), a[1].Value<float>(), a[2].Value<float>()); }
        private static Vector4 ReadVector4(JToken t) { var a = RequireArray(t, 4, "Vector4"); return new Vector4(a[0].Value<float>(), a[1].Value<float>(), a[2].Value<float>(), a[3].Value<float>()); }
        private static Quaternion ReadQuaternion(JToken t) { var a = RequireArray(t, 4, "Quaternion"); return new Quaternion(a[0].Value<float>(), a[1].Value<float>(), a[2].Value<float>(), a[3].Value<float>()); }
        private static Color ReadColor(JToken t) { var a = RequireArray(t, 4, "Color"); return new Color(a[0].Value<float>(), a[1].Value<float>(), a[2].Value<float>(), a[3].Value<float>()); }
        private static Rect ReadRect(JToken t) { var a = RequireArray(t, 4, "Rect"); return new Rect(a[0].Value<float>(), a[1].Value<float>(), a[2].Value<float>(), a[3].Value<float>()); }
        private static Vector2Int ReadVector2Int(JToken t) { var a = RequireArray(t, 2, "Vector2Int"); return new Vector2Int(a[0].Value<int>(), a[1].Value<int>()); }
        private static Vector3Int ReadVector3Int(JToken t) { var a = RequireArray(t, 3, "Vector3Int"); return new Vector3Int(a[0].Value<int>(), a[1].Value<int>(), a[2].Value<int>()); }
        private static RectInt ReadRectInt(JToken t) { var a = RequireArray(t, 4, "RectInt"); return new RectInt(a[0].Value<int>(), a[1].Value<int>(), a[2].Value<int>(), a[3].Value<int>()); }
        private static Bounds ReadBounds(JToken t) { var o = t as JObject ?? throw new RoboVisionException("INVALID_PARAMS", "Bounds must be {center,size}"); return new Bounds(ReadVector3(o["center"]), ReadVector3(o["size"])); }
        private static BoundsInt ReadBoundsInt(JToken t) { var o = t as JObject ?? throw new RoboVisionException("INVALID_PARAMS", "BoundsInt must be {position,size}"); return new BoundsInt(ReadVector3Int(o["position"]), ReadVector3Int(o["size"])); }

        private static JArray Vector2Token(Vector2 v) => new JArray(v.x, v.y);
        private static JArray Vector3Token(Vector3 v) => new JArray(v.x, v.y, v.z);
        private static JArray Vector4Token(Vector4 v) => new JArray(v.x, v.y, v.z, v.w);
        private static JArray QuaternionToken(Quaternion q) => new JArray(q.x, q.y, q.z, q.w);
        private static JArray ColorToken(Color c) => new JArray(c.r, c.g, c.b, c.a);
        private static JArray RectToken(Rect r) => new JArray(r.x, r.y, r.width, r.height);
        private static JArray Vector2IntToken(Vector2Int v) => new JArray(v.x, v.y);
        private static JArray Vector3IntToken(Vector3Int v) => new JArray(v.x, v.y, v.z);
        private static JArray RectIntToken(RectInt r) => new JArray(r.x, r.y, r.width, r.height);
        private static JObject BoundsToken(Bounds b) => new JObject { ["center"] = Vector3Token(b.center), ["size"] = Vector3Token(b.size) };
        private static JObject BoundsIntToken(BoundsInt b) => new JObject { ["position"] = Vector3IntToken(b.position), ["size"] = Vector3IntToken(b.size) };
    }
}
