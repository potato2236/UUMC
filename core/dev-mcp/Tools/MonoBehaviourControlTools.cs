using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Uumc.DevMcp.Server;

namespace Uumc.DevMcp.Tools
{
    public class ToggleBehaviourToolDefinition : ToolDefinitionBase
    {
        public override string Name => "toggle_behaviour";
        public override string Description => @"Enable or disable a MonoBehaviour component.
This controls whether the component receives Update, FixedUpdate, and other callbacks.";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["gameObjectPath"] = new ToolPropertySchema { Type = "string", Description = "Path to the GameObject containing the behaviour" },
                    ["gameObjectId"] = new ToolPropertySchema { Type = "integer", Description = "Instance ID of the GameObject (alternative to path)" },
                    ["componentType"] = new ToolPropertySchema { Type = "string", Description = "Name of the MonoBehaviour type" },
                    ["componentIndex"] = new ToolPropertySchema { Type = "integer", Description = "Index if multiple components of same type (default: 0)" },
                    ["enabled"] = new ToolPropertySchema { Type = "boolean", Description = "Set to true to enable, false to disable" }
                },
                Required = new List<string> { "componentType", "enabled" }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var componentType = GetStringArg(arguments, "componentType");
            var componentIndex = GetIntArg(arguments, "componentIndex", 0);
            var enabled = GetBoolArg(arguments, "enabled");

            if (string.IsNullOrEmpty(componentType)) return ErrorResult("componentType is required");

            try
            {
                if (!TryResolveGameObject(arguments, "gameObjectPath", "gameObjectId", out var gameObject, out var failure))
                    return failure;

                if (!TryResolveComponent(gameObject, componentType, componentIndex, out var targetComponent, out var compFailure))
                    return compFailure;

                return UnityHelper.SetBehaviourEnabled(targetComponent, enabled)
                    ? TextResult($"Successfully set {componentType}.enabled = {enabled}")
                    : ErrorResult("Failed to set enabled state - component may not be a Behaviour");
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to toggle behaviour: {ex.Message}");
            }
        }
    }

    public class SetPropertyToolDefinition : ToolDefinitionBase
    {
        public override string Name => "set_property";
        public override string Description => @"Set a property or field value on a Unity object (GameObject or Component).
Supports basic types (string, int, float, bool) and some Unity types (Vector3, Color).";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["gameObjectPath"] = new ToolPropertySchema { Type = "string", Description = "Path to the GameObject" },
                    ["gameObjectId"] = new ToolPropertySchema { Type = "integer", Description = "Instance ID of the GameObject (alternative to path)" },
                    ["componentType"] = new ToolPropertySchema { Type = "string", Description = "Component type name (omit to set property on GameObject itself)" },
                    ["componentIndex"] = new ToolPropertySchema { Type = "integer", Description = "Index if multiple components of same type (default: 0)" },
                    ["propertyName"] = new ToolPropertySchema { Type = "string", Description = "Name of the property or field to set" },
                    ["value"] = new ToolPropertySchema { Type = "string", Description = "Value to set (will be parsed based on property type)" }
                },
                Required = new List<string> { "propertyName", "value" }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var componentType = GetStringArg(arguments, "componentType");
            var componentIndex = GetIntArg(arguments, "componentIndex", 0);
            var propertyName = GetStringArg(arguments, "propertyName");
            var valueStr = GetStringArg(arguments, "value");

            if (string.IsNullOrEmpty(propertyName)) return ErrorResult("propertyName is required");

            try
            {
                if (!TryResolveGameObject(arguments, "gameObjectPath", "gameObjectId", out var gameObject, out var failure))
                    return failure;

                object target = gameObject;
                if (!string.IsNullOrEmpty(componentType))
                {
                    if (!TryResolveComponent(gameObject, componentType, componentIndex, out target, out var compFailure))
                        return compFailure;
                }

                var targetType = target.GetType();
                var prop = targetType.GetProperty(propertyName, BindingFlags.Public | BindingFlags.Instance);
                if (prop != null && prop.CanWrite)
                {
                    var convertedValue = ConvertValue(valueStr, prop.PropertyType);
                    prop.SetValue(target, convertedValue);
                    return TextResult($"Set {propertyName} = {convertedValue}");
                }

                var field = targetType.GetField(propertyName, BindingFlags.Public | BindingFlags.Instance);
                if (field != null)
                {
                    var convertedValue = ConvertValue(valueStr, field.FieldType);
                    field.SetValue(target, convertedValue);
                    return TextResult($"Set {propertyName} = {convertedValue}");
                }

                return ErrorResult($"Property or field '{propertyName}' not found or is not writable");
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to set property: {ex.Message}");
            }
        }

        private static object ConvertValue(string valueStr, Type targetType)
        {
            if (targetType == typeof(string)) return valueStr;
            if (targetType == typeof(int)) return int.Parse(valueStr, CultureInfo.InvariantCulture);
            if (targetType == typeof(float)) return float.Parse(valueStr, CultureInfo.InvariantCulture);
            if (targetType == typeof(double)) return double.Parse(valueStr, CultureInfo.InvariantCulture);
            if (targetType == typeof(bool)) return bool.Parse(valueStr);
            if (targetType == typeof(long)) return long.Parse(valueStr, CultureInfo.InvariantCulture);
            if (targetType.IsEnum) return Enum.Parse(targetType, valueStr, true);

            if (targetType.Name == "Vector3")
            {
                var parts = valueStr.Replace("(", "").Replace(")", "").Trim().Split(',').Select(p => float.Parse(p.Trim(), CultureInfo.InvariantCulture)).ToArray();
                if (parts.Length >= 3)
                {
                    return targetType.GetConstructor(new[] { typeof(float), typeof(float), typeof(float) })
                        ?.Invoke(new object[] { parts[0], parts[1], parts[2] });
                }
            }

            if (targetType.Name == "Vector2")
            {
                var parts = valueStr.Replace("(", "").Replace(")", "").Trim().Split(',').Select(p => float.Parse(p.Trim(), CultureInfo.InvariantCulture)).ToArray();
                if (parts.Length >= 2)
                {
                    return targetType.GetConstructor(new[] { typeof(float), typeof(float) })
                        ?.Invoke(new object[] { parts[0], parts[1] });
                }
            }

            if (targetType.Name == "Color")
            {
                if (valueStr.StartsWith("#"))
                {
                    var hex = valueStr.Substring(1);
                    float r = int.Parse(hex.Substring(0, 2), NumberStyles.HexNumber) / 255f;
                    float g = int.Parse(hex.Substring(2, 2), NumberStyles.HexNumber) / 255f;
                    float b = int.Parse(hex.Substring(4, 2), NumberStyles.HexNumber) / 255f;
                    float a = hex.Length >= 8 ? int.Parse(hex.Substring(6, 2), NumberStyles.HexNumber) / 255f : 1f;
                    return targetType.GetConstructor(new[] { typeof(float), typeof(float), typeof(float), typeof(float) })
                        ?.Invoke(new object[] { r, g, b, a });
                }

                var parts = valueStr.Split(',').Select(p => float.Parse(p.Trim(), CultureInfo.InvariantCulture)).ToArray();
                if (parts.Length >= 3)
                {
                    var a = parts.Length >= 4 ? parts[3] : 1f;
                    return targetType.GetConstructor(new[] { typeof(float), typeof(float), typeof(float), typeof(float) })
                        ?.Invoke(new object[] { parts[0], parts[1], parts[2], a });
                }
            }

            throw new ArgumentException($"Cannot convert '{valueStr}' to type {targetType.Name}");
        }
    }

    public class InvokeMethodToolDefinition : ToolDefinitionBase
    {
        public override string Name => "invoke_method";
        public override string Description => @"Invoke a method on a Unity object (GameObject or Component).
Methods can be called with arguments. The result is returned as text.";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["gameObjectPath"] = new ToolPropertySchema { Type = "string", Description = "Path to the GameObject" },
                    ["gameObjectId"] = new ToolPropertySchema { Type = "integer", Description = "Instance ID of the GameObject (alternative to path)" },
                    ["componentType"] = new ToolPropertySchema { Type = "string", Description = "Component type name (omit to call method on GameObject)" },
                    ["componentIndex"] = new ToolPropertySchema { Type = "integer", Description = "Index if multiple components of same type (default: 0)" },
                    ["methodName"] = new ToolPropertySchema { Type = "string", Description = "Name of the method to invoke" },
                    ["arguments"] = new ToolPropertySchema
                    {
                        Type = "array",
                        Description = "Arguments to pass to the method (as JSON array of strings)",
                        Items = new ToolPropertySchema { Type = "string" }
                    }
                },
                Required = new List<string> { "methodName" }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var componentType = GetStringArg(arguments, "componentType");
            var componentIndex = GetIntArg(arguments, "componentIndex", 0);
            var methodName = GetStringArg(arguments, "methodName");
            var args = GetArg<List<string>>(arguments, "arguments", new List<string>());

            if (string.IsNullOrEmpty(methodName)) return ErrorResult("methodName is required");

            try
            {
                if (!TryResolveGameObject(arguments, "gameObjectPath", "gameObjectId", out var gameObject, out var failure))
                    return failure;

                object target = gameObject;
                if (!string.IsNullOrEmpty(componentType))
                {
                    if (!TryResolveComponent(gameObject, componentType, componentIndex, out target, out var compFailure))
                        return compFailure;
                }

                var targetType = target.GetType();
                var methods = targetType.GetMethods(BindingFlags.Public | BindingFlags.Instance)
                    .Where(m => m.Name.Equals(methodName, StringComparison.OrdinalIgnoreCase))
                    .ToList();

                if (methods.Count == 0) return ErrorResult($"Method '{methodName}' not found on {targetType.Name}");

                MethodInfo bestMethod = null;
                object[] convertedArgs = null;

                foreach (var method in methods)
                {
                    var parameters = method.GetParameters();
                    if (args.Count > parameters.Length || args.Count < parameters.Count(p => !p.IsOptional)) continue;

                    try
                    {
                        convertedArgs = new object[parameters.Length];
                        for (int i = 0; i < parameters.Length; i++)
                        {
                            convertedArgs[i] = i < args.Count
                                ? ConvertMethodArg(args[i], parameters[i].ParameterType)
                                : parameters[i].DefaultValue;
                        }
                        bestMethod = method;
                        break;
                    }
                    catch { }
                }

                if (bestMethod == null)
                    return ErrorResult($"No matching overload found for '{methodName}' with {args.Count} arguments");

                var result = bestMethod.Invoke(target, convertedArgs);
                return bestMethod.ReturnType == typeof(void)
                    ? TextResult($"Method '{methodName}' invoked successfully (void return)")
                    : TextResult($"Result: {FormatResult(result)}");
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to invoke method: {ex.Message}");
            }
        }

        private static object ConvertMethodArg(string arg, Type targetType)
        {
            if (targetType == typeof(string)) return arg;
            if (targetType == typeof(int)) return int.Parse(arg, CultureInfo.InvariantCulture);
            if (targetType == typeof(float)) return float.Parse(arg, CultureInfo.InvariantCulture);
            if (targetType == typeof(double)) return double.Parse(arg, CultureInfo.InvariantCulture);
            if (targetType == typeof(bool)) return bool.Parse(arg);
            if (targetType == typeof(long)) return long.Parse(arg, CultureInfo.InvariantCulture);
            if (targetType.IsEnum) return Enum.Parse(targetType, arg, true);

            return JsonConvert.DeserializeObject(arg, targetType, MCPProtocol.JsonSettings);
        }

        private static string FormatResult(object result)
        {
            if (result == null) return "null";
            if (result is System.Collections.IEnumerable enumerable && !(result is string))
            {
                var items = new List<string>();
                foreach (var item in enumerable) items.Add(item?.ToString() ?? "null");
                return $"[{string.Join(", ", items)}]";
            }
            return result.ToString();
        }
    }
}
