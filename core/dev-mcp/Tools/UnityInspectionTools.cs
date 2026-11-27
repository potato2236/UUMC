using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using Uumc.DevMcp.Server;

namespace Uumc.DevMcp.Tools
{
    public class GetSceneInfoToolDefinition : ToolDefinitionBase
    {
        public override string Name => "get_scene_info";
        public override string Description => "Get information about the currently loaded Unity scene including name, build index, and root object count.";

        protected override ToolInputSchema GetInputSchema() =>
            new ToolInputSchema { Properties = new Dictionary<string, ToolPropertySchema>() };

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            try
            {
                return TextResult(UnityHelper.GetGameInfoJson());
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to get scene info: {ex.Message}");
            }
        }
    }

    public class ListGameObjectsToolDefinition : ToolDefinitionBase
    {
        public override string Name => "list_game_objects";
        public override string Description => @"List GameObjects in the current scene hierarchy.
Returns a tree structure of GameObjects with their components, active state, and children.
Use depth parameter to control how deep to traverse (default: 2).";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["depth"] = new ToolPropertySchema
                    {
                        Type = "integer",
                        Description = "Maximum depth to traverse (default: 2, max: 5)",
                        Default = 2,
                        Minimum = 1,
                        Maximum = 5
                    },
                    ["filter"] = new ToolPropertySchema
                    {
                        Type = "string",
                        Description = "Optional name filter - only returns objects containing this text"
                    },
                    ["activeOnly"] = new ToolPropertySchema
                    {
                        Type = "boolean",
                        Description = "If true, only return active GameObjects (default: false)"
                    }
                }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var depth = Math.Max(1, Math.Min(5, GetIntArg(arguments, "depth", 2)));
            var filter = GetStringArg(arguments, "filter");
            var activeOnly = GetBoolArg(arguments, "activeOnly", false);

            try
            {
                var hierarchy = new List<object>();
                foreach (var go in UnityHelper.GetRootGameObjects())
                {
                    var name = UnityHelper.GetProperty(go, "name")?.ToString() ?? "";
                    var isActive = (bool)(UnityHelper.GetProperty(go, "activeInHierarchy") ?? true);

                    if (activeOnly && !isActive) continue;
                    if (!string.IsNullOrEmpty(filter) && !name.Contains(filter, StringComparison.OrdinalIgnoreCase)) continue;

                    hierarchy.Add(UnityHelper.GetGameObjectHierarchy(go, 0, depth));
                }
                return JsonResult(hierarchy);
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to list game objects: {ex.Message}");
            }
        }
    }

    public class FindGameObjectToolDefinition : ToolDefinitionBase
    {
        public override string Name => "find_game_object";
        public override string Description => @"Find an active GameObject by Unity GameObject.Find path/name, or any GameObject by instanceId.
Paths use '/' as separator (e.g., 'Canvas/Panel/Button').
To search inactive objects or match by substring, use list_game_objects or find_objects_of_type.";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["path"] = new ToolPropertySchema
                    {
                        Type = "string",
                        Description = "Path to the GameObject (e.g., 'Canvas/Panel/Button')"
                    },
                    ["instanceId"] = new ToolPropertySchema
                    {
                        Type = "integer",
                        Description = "Instance ID of the GameObject (alternative to path)"
                    },
                    ["includeChildren"] = new ToolPropertySchema
                    {
                        Type = "boolean",
                        Description = "Include child objects in result (default: true)"
                    }
                }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var includeChildren = GetBoolArg(arguments, "includeChildren", true);
            try
            {
                if (!TryResolveGameObject(arguments, "path", "instanceId", out var gameObject, out var failure))
                    return failure;

                var depth = includeChildren ? 3 : 0;
                return JsonResult(UnityHelper.GetGameObjectHierarchy(gameObject, 0, depth));
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to find GameObject: {ex.Message}");
            }
        }
    }

    public class ListComponentsToolDefinition : ToolDefinitionBase
    {
        public override string Name => "list_components";
        public override string Description => @"List all components attached to a GameObject.
Returns component type names, enabled state (for Behaviours), and instance IDs.";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["path"] = new ToolPropertySchema
                    {
                        Type = "string",
                        Description = "Path to the GameObject"
                    },
                    ["instanceId"] = new ToolPropertySchema
                    {
                        Type = "integer",
                        Description = "Instance ID of the GameObject (alternative to path)"
                    }
                }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            try
            {
                if (!TryResolveGameObject(arguments, "path", "instanceId", out var gameObject, out var failure))
                    return failure;

                var result = new List<Dictionary<string, object>>();
                foreach (var comp in UnityHelper.GetComponents(gameObject))
                {
                    var compInfo = new Dictionary<string, object>
                    {
                        ["type"] = comp.GetType().FullName,
                        ["typeName"] = comp.GetType().Name,
                        ["instanceId"] = UnityHelper.InvokeMethod(comp, "GetInstanceID")
                    };

                    var enabled = UnityHelper.GetProperty(comp, "enabled");
                    if (enabled != null) compInfo["enabled"] = enabled;

                    result.Add(compInfo);
                }

                return JsonResult(result);
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to list components: {ex.Message}");
            }
        }
    }

    public class InspectComponentToolDefinition : ToolDefinitionBase
    {
        public override string Name => "inspect_component";
        public override string Description => @"Inspect a component's properties, fields, and methods.
Returns all public members and their values. Use includePrivate to also show private members.";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["gameObjectPath"] = new ToolPropertySchema
                    {
                        Type = "string",
                        Description = "Path to the GameObject containing the component"
                    },
                    ["gameObjectId"] = new ToolPropertySchema
                    {
                        Type = "integer",
                        Description = "Instance ID of the GameObject (alternative to path)"
                    },
                    ["componentType"] = new ToolPropertySchema
                    {
                        Type = "string",
                        Description = "Name or full name of the component type to inspect"
                    },
                    ["componentIndex"] = new ToolPropertySchema
                    {
                        Type = "integer",
                        Description = "Index of the component if multiple of same type exist (default: 0)"
                    },
                    ["includePrivate"] = new ToolPropertySchema
                    {
                        Type = "boolean",
                        Description = "Include private members (default: false)"
                    }
                },
                Required = new List<string> { "componentType" }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var componentType = GetStringArg(arguments, "componentType");
            var componentIndex = GetIntArg(arguments, "componentIndex", 0);
            var includePrivate = GetBoolArg(arguments, "includePrivate", false);

            if (string.IsNullOrEmpty(componentType))
            {
                return ErrorResult("componentType is required");
            }

            try
            {
                if (!TryResolveGameObject(arguments, "gameObjectPath", "gameObjectId", out var gameObject, out var failure))
                    return failure;

                if (!TryResolveComponent(gameObject, componentType, componentIndex, out var targetComponent, out var compFailure))
                    return compFailure;

                return JsonResult(UnityHelper.InspectObject(targetComponent, includePrivate));
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to inspect component: {ex.Message}");
            }
        }
    }
}
