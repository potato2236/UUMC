using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using Uumc.DevMcp.Server;

namespace Uumc.DevMcp.Tools
{
    public class FindObjectsOfTypeToolDefinition : ToolDefinitionBase
    {
        public override string Name => "find_objects_of_type";
        public override string Description => @"Find all Unity objects of a specific type in the scene.
Useful for finding all instances of a component type like 'Camera', 'Light', 'AudioSource', or custom game types.";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["typeName"] = new ToolPropertySchema { Type = "string", Description = "The type name to search for (e.g., 'Camera', 'Light', 'MonoBehaviour')" },
                    ["includeInactive"] = new ToolPropertySchema { Type = "boolean", Description = "Include inactive objects (default: false)" },
                    ["limit"] = new ToolPropertySchema { Type = "integer", Description = "Maximum number of results (default: 50)", Default = 50, Minimum = 1, Maximum = 500 }
                },
                Required = new List<string> { "typeName" }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var typeName = GetStringArg(arguments, "typeName");
            var includeInactive = GetBoolArg(arguments, "includeInactive", false);
            var limit = GetIntArg(arguments, "limit", 50);

            if (string.IsNullOrEmpty(typeName)) return ErrorResult("typeName is required");

            try
            {
                var type = CSharpEvaluator.ResolveType(typeName) ?? CSharpEvaluator.ResolveType("UnityEngine." + typeName);
                if (type == null) return ErrorResult($"Type '{typeName}' not found. Try the full type name.");

                var objects = UnityHelper.FindObjectsOfType(type, includeInactive);
                var results = new List<Dictionary<string, object>>();

                foreach (var obj in objects.Take(limit))
                {
                    if (obj == null) continue;

                    var info = new Dictionary<string, object>
                    {
                        ["type"] = obj.GetType().Name,
                        ["instanceId"] = UnityHelper.InvokeMethod(obj, "GetInstanceID"),
                        ["name"] = UnityHelper.GetProperty(obj, "name")
                    };

                    var gameObject = UnityHelper.GetProperty(obj, "gameObject");
                    if (gameObject != null)
                    {
                        info["gameObjectName"] = UnityHelper.GetProperty(gameObject, "name");
                        info["gameObjectActive"] = UnityHelper.GetProperty(gameObject, "activeInHierarchy");
                        var transform = UnityHelper.GetProperty(gameObject, "transform");
                        if (transform != null) info["path"] = GetTransformPath(transform);
                    }

                    var enabled = UnityHelper.GetProperty(obj, "enabled");
                    if (enabled != null) info["enabled"] = enabled;

                    results.Add(info);
                }

                return JsonResult(new
                {
                    typeName = type.FullName,
                    count = results.Count,
                    totalFound = objects.Length,
                    objects = results
                });
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to find objects: {ex.Message}");
            }
        }

        private static string GetTransformPath(object transform)
        {
            var path = new List<string>();
            var current = transform;
            while (current != null)
            {
                path.Insert(0, UnityHelper.GetProperty(current, "name")?.ToString() ?? "");
                current = UnityHelper.GetProperty(current, "parent");
            }
            return string.Join("/", path);
        }
    }

    public class SetTimeScaleToolDefinition : ToolDefinitionBase
    {
        public override string Name => "set_time_scale";
        public override string Description => @"Set the Unity time scale.
0 = paused, 1 = normal speed, 0.5 = half speed, 2 = double speed.";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["timeScale"] = new ToolPropertySchema { Type = "number", Description = "Time scale value (0 = pause, 1 = normal)" }
                },
                Required = new List<string> { "timeScale" }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var timeScale = (float)GetDoubleArg(arguments, "timeScale", 1.0);
            try
            {
                var timeType = CSharpEvaluator.ResolveType("UnityEngine.Time");
                var prop = timeType?.GetProperty("timeScale", BindingFlags.Public | BindingFlags.Static);
                if (prop == null) return ErrorResult("Could not find UnityEngine.Time.timeScale property");

                prop.SetValue(null, timeScale);
                return TextResult($"Time scale set to {timeScale}");
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to set time scale: {ex.Message}");
            }
        }
    }

    public class CursorControlToolDefinition : ToolDefinitionBase
    {
        public override string Name => "cursor_control";
        public override string Description => "Control the mouse cursor visibility and lock state.";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["visible"] = new ToolPropertySchema { Type = "boolean", Description = "Set cursor visibility" },
                    ["lockState"] = new ToolPropertySchema
                    {
                        Type = "string",
                        Description = "Set cursor lock state",
                        Enum = new List<string> { "None", "Locked", "Confined" }
                    }
                }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var hasVisible = arguments.ContainsKey("visible");
            var visible = GetBoolArg(arguments, "visible", true);
            var lockState = GetStringArg(arguments, "lockState");

            try
            {
                var cursorType = CSharpEvaluator.ResolveType("UnityEngine.Cursor");
                if (cursorType == null) return ErrorResult("Could not find UnityEngine.Cursor type");

                var results = new List<string>();
                if (hasVisible)
                {
                    var visibleProp = cursorType.GetProperty("visible", BindingFlags.Public | BindingFlags.Static);
                    visibleProp?.SetValue(null, visible);
                    results.Add($"Cursor visible: {visible}");
                }

                if (!string.IsNullOrEmpty(lockState))
                {
                    var lockStateProp = cursorType.GetProperty("lockState", BindingFlags.Public | BindingFlags.Static);
                    var lockStateType = CSharpEvaluator.ResolveType("UnityEngine.CursorLockMode");
                    if (lockStateProp != null && lockStateType != null)
                    {
                        lockStateProp.SetValue(null, Enum.Parse(lockStateType, lockState));
                        results.Add($"Cursor lock state: {lockState}");
                    }
                }

                if (results.Count == 0)
                {
                    return JsonResult(new
                    {
                        visible = cursorType.GetProperty("visible", BindingFlags.Public | BindingFlags.Static)?.GetValue(null),
                        lockState = cursorType.GetProperty("lockState", BindingFlags.Public | BindingFlags.Static)?.GetValue(null)?.ToString()
                    });
                }

                return TextResult(string.Join(", ", results));
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to control cursor: {ex.Message}");
            }
        }
    }

    public class LoadSceneToolDefinition : ToolDefinitionBase
    {
        public override string Name => "load_scene";
        public override string Description => "Load a Unity scene by name or build index (single or additive).";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["sceneName"] = new ToolPropertySchema { Type = "string", Description = "Name of the scene to load" },
                    ["buildIndex"] = new ToolPropertySchema { Type = "integer", Description = "Build index of the scene (alternative to sceneName)" },
                    ["additive"] = new ToolPropertySchema { Type = "boolean", Description = "Load additively instead of replacing current scene (default: false)" }
                }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var sceneName = GetStringArg(arguments, "sceneName");
            var buildIndex = GetIntArg(arguments, "buildIndex", -1);
            var additive = GetBoolArg(arguments, "additive", false);

            if (string.IsNullOrEmpty(sceneName) && buildIndex < 0)
                return ErrorResult("Either 'sceneName' or 'buildIndex' must be provided");

            try
            {
                var sceneManagerType = CSharpEvaluator.ResolveType("UnityEngine.SceneManagement.SceneManager");
                var loadSceneModeType = CSharpEvaluator.ResolveType("UnityEngine.SceneManagement.LoadSceneMode");
                if (sceneManagerType == null || loadSceneModeType == null) return ErrorResult("Could not find SceneManager types");

                var loadMode = Enum.Parse(loadSceneModeType, additive ? "Additive" : "Single");
                MethodInfo loadMethod;
                object[] args;

                if (!string.IsNullOrEmpty(sceneName))
                {
                    loadMethod = sceneManagerType.GetMethod("LoadScene", new[] { typeof(string), loadSceneModeType });
                    args = new object[] { sceneName, loadMode };
                }
                else
                {
                    loadMethod = sceneManagerType.GetMethod("LoadScene", new[] { typeof(int), loadSceneModeType });
                    args = new object[] { buildIndex, loadMode };
                }

                if (loadMethod == null) return ErrorResult("Could not find LoadScene method");
                loadMethod.Invoke(null, args);
                return TextResult($"Loading scene: {sceneName ?? buildIndex.ToString()} (additive: {additive})");
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to load scene: {ex.Message}");
            }
        }
    }

    public class InstantiateObjectToolDefinition : ToolDefinitionBase
    {
        public override string Name => "instantiate_object";
        public override string Description => "Clone an existing GameObject at the same or specified position.";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["sourceGameObjectPath"] = new ToolPropertySchema { Type = "string", Description = "Path to the GameObject to clone" },
                    ["sourceGameObjectId"] = new ToolPropertySchema { Type = "integer", Description = "Instance ID of the GameObject to clone (alternative to path)" },
                    ["position"] = new ToolPropertySchema { Type = "string", Description = "Position for the new object as 'x,y,z' (optional)" },
                    ["newName"] = new ToolPropertySchema { Type = "string", Description = "Name for the new object (optional)" }
                }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var position = GetStringArg(arguments, "position");
            var newName = GetStringArg(arguments, "newName");

            try
            {
                if (!TryResolveGameObject(arguments, "sourceGameObjectPath", "sourceGameObjectId", out var gameObject, out var failure))
                    return failure;

                var objectType = CSharpEvaluator.ResolveType("UnityEngine.Object");
                var instantiateMethod = objectType?.GetMethod("Instantiate", new[] { objectType });
                if (instantiateMethod == null) return ErrorResult("Could not find Instantiate method");

                var newObject = instantiateMethod.Invoke(null, new[] { gameObject });
                if (newObject == null) return ErrorResult("Instantiate returned null");

                if (!string.IsNullOrEmpty(newName)) UnityHelper.SetProperty(newObject, "name", newName);

                if (!string.IsNullOrEmpty(position))
                {
                    var transform = UnityHelper.GetProperty(newObject, "transform");
                    var vector3Type = CSharpEvaluator.ResolveType("UnityEngine.Vector3");
                    var ctor = vector3Type?.GetConstructor(new[] { typeof(float), typeof(float), typeof(float) });
                    var parts = position.Split(',').Select(p => float.Parse(p.Trim(), CultureInfo.InvariantCulture)).ToArray();
                    if (transform != null && ctor != null && parts.Length >= 3)
                    {
                        UnityHelper.SetProperty(transform, "position", ctor.Invoke(new object[] { parts[0], parts[1], parts[2] }));
                    }
                }

                return JsonResult(new
                {
                    success = true,
                    name = UnityHelper.GetProperty(newObject, "name")?.ToString(),
                    instanceId = UnityHelper.InvokeMethod(newObject, "GetInstanceID")
                });
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to instantiate object: {ex.Message}");
            }
        }
    }

    public class CreatePrimitiveToolDefinition : ToolDefinitionBase
    {
        public override string Name => "create_primitive";
        public override string Description => "Create a new primitive GameObject (Cube, Sphere, Capsule, Cylinder, Plane, Quad).";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["type"] = new ToolPropertySchema
                    {
                        Type = "string",
                        Description = "Type of primitive to create",
                        Enum = new List<string> { "Cube", "Sphere", "Capsule", "Cylinder", "Plane", "Quad" }
                    },
                    ["name"] = new ToolPropertySchema { Type = "string", Description = "Name for the new object (optional)" },
                    ["position"] = new ToolPropertySchema { Type = "string", Description = "Position as 'x,y,z' (default: 0,0,0)" },
                    ["scale"] = new ToolPropertySchema { Type = "string", Description = "Scale as 'x,y,z' (default: 1,1,1)" }
                },
                Required = new List<string> { "type" }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var primitiveType = GetStringArg(arguments, "type");
            var name = GetStringArg(arguments, "name");
            var positionStr = GetStringArg(arguments, "position");
            var scaleStr = GetStringArg(arguments, "scale");

            if (string.IsNullOrEmpty(primitiveType)) return ErrorResult("type is required");

            try
            {
                var gameObjectType = CSharpEvaluator.ResolveType("UnityEngine.GameObject");
                var primitiveTypeEnum = CSharpEvaluator.ResolveType("UnityEngine.PrimitiveType");
                var vector3Type = CSharpEvaluator.ResolveType("UnityEngine.Vector3");

                if (gameObjectType == null || primitiveTypeEnum == null) return ErrorResult("Could not resolve required Unity types");

                var createMethod = gameObjectType.GetMethod("CreatePrimitive", BindingFlags.Public | BindingFlags.Static, null, new[] { primitiveTypeEnum }, null);
                if (createMethod == null) return ErrorResult("Could not find GameObject.CreatePrimitive method");

                var newObject = createMethod.Invoke(null, new[] { Enum.Parse(primitiveTypeEnum, primitiveType) });
                if (newObject == null) return ErrorResult("CreatePrimitive returned null");

                if (!string.IsNullOrEmpty(name)) UnityHelper.SetProperty(newObject, "name", name);

                var transform = UnityHelper.GetProperty(newObject, "transform");
                var ctor = vector3Type?.GetConstructor(new[] { typeof(float), typeof(float), typeof(float) });
                if (transform != null && ctor != null)
                {
                    SetVec3(transform, ctor, positionStr, "position");
                    SetVec3(transform, ctor, scaleStr, "localScale");
                }

                return JsonResult(new
                {
                    success = true,
                    name = UnityHelper.GetProperty(newObject, "name")?.ToString(),
                    instanceId = UnityHelper.InvokeMethod(newObject, "GetInstanceID"),
                    type = primitiveType
                });
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to create primitive: {ex.Message}");
            }
        }

        private static void SetVec3(object transform, ConstructorInfo ctor, string raw, string propName)
        {
            if (string.IsNullOrEmpty(raw)) return;
            var parts = raw.Split(',').Select(p => float.Parse(p.Trim(), CultureInfo.InvariantCulture)).ToArray();
            if (parts.Length >= 3)
            {
                UnityHelper.SetProperty(transform, propName, ctor.Invoke(new object[] { parts[0], parts[1], parts[2] }));
            }
        }
    }

    public class SetTransformToolDefinition : ToolDefinitionBase
    {
        public override string Name => "set_transform";
        public override string Description => "Set the position, rotation (euler angles in degrees), or scale of a GameObject's transform.";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["gameObjectPath"] = new ToolPropertySchema { Type = "string", Description = "Path to the GameObject" },
                    ["gameObjectId"] = new ToolPropertySchema { Type = "integer", Description = "Instance ID of the GameObject (alternative to path)" },
                    ["position"] = new ToolPropertySchema { Type = "string", Description = "New world position as 'x,y,z'" },
                    ["localPosition"] = new ToolPropertySchema { Type = "string", Description = "New local position as 'x,y,z'" },
                    ["rotation"] = new ToolPropertySchema { Type = "string", Description = "New euler angles as 'x,y,z' (degrees)" },
                    ["localRotation"] = new ToolPropertySchema { Type = "string", Description = "New local euler angles as 'x,y,z' (degrees)" },
                    ["scale"] = new ToolPropertySchema { Type = "string", Description = "New local scale as 'x,y,z'" }
                }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var position = GetStringArg(arguments, "position");
            var localPosition = GetStringArg(arguments, "localPosition");
            var rotation = GetStringArg(arguments, "rotation");
            var localRotation = GetStringArg(arguments, "localRotation");
            var scale = GetStringArg(arguments, "scale");

            try
            {
                if (!TryResolveGameObject(arguments, "gameObjectPath", "gameObjectId", out var gameObject, out var failure))
                    return failure;

                var transform = UnityHelper.GetProperty(gameObject, "transform");
                if (transform == null) return ErrorResult("Transform not found on GameObject");

                var vector3Type = CSharpEvaluator.ResolveType("UnityEngine.Vector3");
                var quaternionType = CSharpEvaluator.ResolveType("UnityEngine.Quaternion");
                var vector3Ctor = vector3Type?.GetConstructor(new[] { typeof(float), typeof(float), typeof(float) });
                if (vector3Type == null || vector3Ctor == null) return ErrorResult("Could not resolve Vector3 type");

                var changes = new List<string>();
                ApplyVector3(transform, vector3Ctor, position, "position", changes);
                ApplyVector3(transform, vector3Ctor, localPosition, "localPosition", changes);
                ApplyVector3(transform, vector3Ctor, scale, "localScale", changes);
                ApplyEuler(transform, vector3Type, vector3Ctor, quaternionType, rotation, "rotation", changes);
                ApplyEuler(transform, vector3Type, vector3Ctor, quaternionType, localRotation, "localRotation", changes);

                if (changes.Count == 0)
                {
                    return JsonResult(new
                    {
                        position = UnityHelper.GetProperty(transform, "position")?.ToString(),
                        localPosition = UnityHelper.GetProperty(transform, "localPosition")?.ToString(),
                        eulerAngles = UnityHelper.GetProperty(transform, "eulerAngles")?.ToString(),
                        localScale = UnityHelper.GetProperty(transform, "localScale")?.ToString()
                    });
                }

                return TextResult($"Transform updated: {string.Join(", ", changes)}");
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to set transform: {ex.Message}");
            }
        }

        private static void ApplyVector3(object transform, ConstructorInfo ctor, string value, string propName, List<string> changes)
        {
            if (string.IsNullOrEmpty(value)) return;
            var parts = value.Split(',').Select(p => float.Parse(p.Trim(), CultureInfo.InvariantCulture)).ToArray();
            if (parts.Length >= 3)
            {
                UnityHelper.SetProperty(transform, propName, ctor.Invoke(new object[] { parts[0], parts[1], parts[2] }));
                changes.Add($"{propName} = ({parts[0]}, {parts[1]}, {parts[2]})");
            }
        }

        private static void ApplyEuler(
            object transform,
            Type vector3Type,
            ConstructorInfo vector3Ctor,
            Type quaternionType,
            string value,
            string propName,
            List<string> changes)
        {
            if (string.IsNullOrEmpty(value) || quaternionType == null) return;
            var parts = value.Split(',').Select(p => float.Parse(p.Trim(), CultureInfo.InvariantCulture)).ToArray();
            if (parts.Length < 3) return;

            var euler = vector3Ctor.Invoke(new object[] { parts[0], parts[1], parts[2] });
            var eulerMethod = quaternionType.GetMethod("Euler", BindingFlags.Public | BindingFlags.Static, null, new[] { vector3Type }, null);
            if (eulerMethod != null)
            {
                UnityHelper.SetProperty(transform, propName, eulerMethod.Invoke(null, new[] { euler }));
                changes.Add($"{propName} = Euler({parts[0]}, {parts[1]}, {parts[2]})");
            }
        }
    }

    public class InspectMaterialToolDefinition : ToolDefinitionBase
    {
        public override string Name => "inspect_material";
        public override string Description => "Inspect the material(s) on a GameObject's renderer (shader, color, texture info).";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["gameObjectPath"] = new ToolPropertySchema { Type = "string", Description = "Path to the GameObject" },
                    ["gameObjectId"] = new ToolPropertySchema { Type = "integer", Description = "Instance ID of the GameObject (alternative to path)" },
                    ["materialIndex"] = new ToolPropertySchema { Type = "integer", Description = "Index of the material to inspect (default: all materials)" }
                }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var materialIndex = GetIntArg(arguments, "materialIndex", -1);

            try
            {
                if (!TryResolveGameObject(arguments, "gameObjectPath", "gameObjectId", out var gameObject, out var failure))
                    return failure;

                var renderer = UnityHelper.GetComponent(gameObject, "Renderer")
                               ?? UnityHelper.GetComponent(gameObject, "MeshRenderer")
                               ?? UnityHelper.GetComponent(gameObject, "SkinnedMeshRenderer");

                if (renderer == null) return TextResult("No renderer found on GameObject");

                var materials = UnityHelper.GetProperty(renderer, "materials") ?? UnityHelper.GetProperty(renderer, "sharedMaterials");
                var materialList = new List<Dictionary<string, object>>();

                if (materials is System.Collections.IEnumerable matsToProcess)
                {
                    int index = 0;
                    foreach (var mat in matsToProcess)
                    {
                        if ((materialIndex < 0 || index == materialIndex) && mat != null)
                        {
                            materialList.Add(InspectMaterial(mat, index));
                        }
                        index++;
                    }
                }

                return JsonResult(new
                {
                    rendererType = renderer.GetType().Name,
                    materialCount = materialList.Count,
                    materials = materialList
                });
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to inspect material: {ex.Message}");
            }
        }

        private static Dictionary<string, object> InspectMaterial(object material, int index)
        {
            var info = new Dictionary<string, object>
            {
                ["index"] = index,
                ["name"] = UnityHelper.GetProperty(material, "name"),
                ["instanceId"] = UnityHelper.InvokeMethod(material, "GetInstanceID"),
                ["renderQueue"] = UnityHelper.GetProperty(material, "renderQueue")
            };

            var shader = UnityHelper.GetProperty(material, "shader");
            if (shader != null) info["shaderName"] = UnityHelper.GetProperty(shader, "name");

            try
            {
                var getColorMethod = material.GetType().GetMethod("GetColor", BindingFlags.Public | BindingFlags.Instance, null, new[] { typeof(string) }, null);
                var mainColor = getColorMethod?.Invoke(material, new object[] { "_Color" });
                if (mainColor != null) info["mainColor"] = mainColor.ToString();
            }
            catch { }

            try
            {
                var mainTexture = UnityHelper.GetProperty(material, "mainTexture");
                if (mainTexture != null)
                {
                    info["mainTexture"] = new Dictionary<string, object>
                    {
                        ["name"] = UnityHelper.GetProperty(mainTexture, "name"),
                        ["width"] = UnityHelper.GetProperty(mainTexture, "width"),
                        ["height"] = UnityHelper.GetProperty(mainTexture, "height")
                    };
                }
            }
            catch { }

            var scale = UnityHelper.GetProperty(material, "mainTextureScale");
            var offset = UnityHelper.GetProperty(material, "mainTextureOffset");
            if (scale != null) info["mainTextureScale"] = scale.ToString();
            if (offset != null) info["mainTextureOffset"] = offset.ToString();

            try
            {
                if (UnityHelper.GetProperty(material, "shaderKeywords") is string[] keywordArray && keywordArray.Length > 0)
                    info["shaderKeywords"] = keywordArray;
            }
            catch { }

            return info;
        }
    }

    public class DestroyObjectToolDefinition : ToolDefinitionBase
    {
        public override string Name => "destroy_object";
        public override string Description => "Destroy a GameObject or component. WARNING: This cannot be undone!";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["gameObjectPath"] = new ToolPropertySchema { Type = "string", Description = "Path to the GameObject to destroy" },
                    ["gameObjectId"] = new ToolPropertySchema { Type = "integer", Description = "Instance ID of the GameObject (alternative to path)" },
                    ["componentType"] = new ToolPropertySchema { Type = "string", Description = "If specified, only destroy this component instead of the whole GameObject" }
                }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var componentType = GetStringArg(arguments, "componentType");

            try
            {
                if (!TryResolveGameObject(arguments, "gameObjectPath", "gameObjectId", out var gameObject, out var failure))
                    return failure;

                var objectType = CSharpEvaluator.ResolveType("UnityEngine.Object");
                var destroyMethod = objectType?.GetMethod("Destroy", new[] { objectType });
                if (destroyMethod == null) return ErrorResult("Could not find Destroy method");

                object toDestroy = gameObject;
                if (!string.IsNullOrEmpty(componentType))
                {
                    if (!TryResolveComponent(gameObject, componentType, 0, out toDestroy, out var compFailure))
                        return compFailure;
                }

                var targetName = UnityHelper.GetProperty(toDestroy, "name")?.ToString();
                destroyMethod.Invoke(null, new[] { toDestroy });
                return TextResult($"Destroyed: {targetName ?? componentType ?? "object"}");
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to destroy object: {ex.Message}");
            }
        }
    }
}
