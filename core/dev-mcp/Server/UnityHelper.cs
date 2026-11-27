using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json;

namespace Uumc.DevMcp.Server
{
    /// <summary>
    /// Reflection helpers for accessing Unity scene objects and runtime APIs
    /// </summary>
    public static class UnityHelper
    {
        private static Assembly _unityEngine;
        private static Assembly _unityCoreModule;
        private static bool _isIl2Cpp;

        private static Type _gameObjectType;
        private static Type _componentType;
        private static Type _behaviourType;
        private static Type _transformType;
        private static Type _sceneManagerType;
        private static Type _sceneType;
        private static Type _objectType;
        private static Type _timeType;
        private static Type _screenType;
        private static Type _applicationType;
        private static Type _screenCaptureType;
        private static Type _texture2DType;
        private static Type _renderTextureType;
        private static Type _rectType;
        private static Type _textureFormatType;

        private static MethodInfo _findObjectsOfTypeMethod;
        private static MethodInfo _findObjectsOfTypeGenericMethod;
        private static MethodInfo _getActiveSceneMethod;
        private static MethodInfo _gameObjectFindMethod;

        static UnityHelper()
        {
            CacheUnityTypes();
        }

        private static void CacheUnityTypes()
        {
            try
            {
                var assemblies = AppDomain.CurrentDomain.GetAssemblies();
                _isIl2Cpp = assemblies.Any(a => a.GetName().Name.Contains("Il2Cpp"));

                if (_isIl2Cpp)
                {
                    CacheIl2CppTypes(assemblies);
                }
                else
                {
                    CacheMonoTypes(assemblies);
                }
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Failed to cache Unity types: {ex.Message}");
            }
        }

        private static void CacheIl2CppTypes(Assembly[] assemblies)
        {
            foreach (var asm in assemblies)
            {
                try
                {
                    if (asm.IsDynamic) continue;
                    var asmName = asm.GetName().Name;
                    if (!asmName.Contains("Unity") && !asmName.Contains("Il2Cpp")) continue;

                    foreach (var type in asm.GetExportedTypes())
                    {
                        var fullName = type.FullName;
                        if (fullName == null) continue;

                        if (fullName == "UnityEngine.Object" && _objectType == null) _objectType = type;
                        else if (fullName == "UnityEngine.GameObject" && _gameObjectType == null) _gameObjectType = type;
                        else if (fullName == "UnityEngine.Component" && _componentType == null) _componentType = type;
                        else if (fullName == "UnityEngine.Behaviour" && _behaviourType == null) _behaviourType = type;
                        else if (fullName == "UnityEngine.Transform" && _transformType == null) _transformType = type;
                        else if (fullName == "UnityEngine.Time" && _timeType == null) _timeType = type;
                        else if (fullName == "UnityEngine.Screen" && _screenType == null) _screenType = type;
                        else if (fullName == "UnityEngine.Application" && _applicationType == null) _applicationType = type;
                        else if (fullName == "UnityEngine.Texture2D" && _texture2DType == null) _texture2DType = type;
                        else if (fullName == "UnityEngine.RenderTexture" && _renderTextureType == null) _renderTextureType = type;
                        else if (fullName == "UnityEngine.Rect" && _rectType == null) _rectType = type;
                        else if (fullName == "UnityEngine.TextureFormat" && _textureFormatType == null) _textureFormatType = type;
                        else if (fullName == "UnityEngine.ScreenCapture" && _screenCaptureType == null) _screenCaptureType = type;
                        else if (fullName == "UnityEngine.SceneManagement.SceneManager" && _sceneManagerType == null) _sceneManagerType = type;
                        else if (fullName == "UnityEngine.SceneManagement.Scene" && _sceneType == null) _sceneType = type;
                    }
                }
                catch { }
            }

            CacheCommonMethods();
        }

        private static void CacheMonoTypes(Assembly[] assemblies)
        {
            _unityEngine = assemblies.FirstOrDefault(a => a.GetName().Name == "UnityEngine");
            _unityCoreModule = assemblies.FirstOrDefault(a => a.GetName().Name == "UnityEngine.CoreModule");

            var primaryAsm = _unityCoreModule ?? _unityEngine;
            if (primaryAsm != null)
            {
                _objectType = primaryAsm.GetType("UnityEngine.Object");
                _gameObjectType = primaryAsm.GetType("UnityEngine.GameObject");
                _componentType = primaryAsm.GetType("UnityEngine.Component");
                _behaviourType = primaryAsm.GetType("UnityEngine.Behaviour");
                _transformType = primaryAsm.GetType("UnityEngine.Transform");
                _timeType = primaryAsm.GetType("UnityEngine.Time");
                _screenType = primaryAsm.GetType("UnityEngine.Screen");
                _applicationType = primaryAsm.GetType("UnityEngine.Application");
                _texture2DType = primaryAsm.GetType("UnityEngine.Texture2D");
                _renderTextureType = primaryAsm.GetType("UnityEngine.RenderTexture");
                _rectType = primaryAsm.GetType("UnityEngine.Rect");
                _textureFormatType = primaryAsm.GetType("UnityEngine.TextureFormat");
            }

            var sceneManagementAsm = assemblies.FirstOrDefault(a => a.GetName().Name == "UnityEngine.SceneManagementModule");
            _sceneManagerType = sceneManagementAsm?.GetType("UnityEngine.SceneManagement.SceneManager");
            _sceneType = sceneManagementAsm?.GetType("UnityEngine.SceneManagement.Scene");

            if (_sceneManagerType == null && primaryAsm != null)
            {
                _sceneManagerType = primaryAsm.GetType("UnityEngine.SceneManagement.SceneManager");
                _sceneType = primaryAsm.GetType("UnityEngine.SceneManagement.Scene");
            }

            var screenshotAsm = assemblies.FirstOrDefault(a => a.GetName().Name == "UnityEngine.ScreenCaptureModule");
            _screenCaptureType = screenshotAsm?.GetType("UnityEngine.ScreenCapture");

            CacheCommonMethods();
        }

        private static void CacheCommonMethods()
        {
            if (_objectType != null)
            {
                _findObjectsOfTypeGenericMethod = _objectType.GetMethods(BindingFlags.Public | BindingFlags.Static)
                    .FirstOrDefault(m => m.Name == "FindObjectsOfType" && m.IsGenericMethodDefinition);

                _findObjectsOfTypeMethod = _objectType.GetMethod("FindObjectsOfType",
                    BindingFlags.Public | BindingFlags.Static,
                    null,
                    new[] { typeof(Type) },
                    null);

                if (_findObjectsOfTypeMethod == null)
                {
                    _findObjectsOfTypeMethod = _objectType.GetMethods(BindingFlags.Public | BindingFlags.Static)
                        .FirstOrDefault(m => m.Name == "FindObjectsOfType" && !m.IsGenericMethodDefinition && m.GetParameters().Length == 1);
                }
            }

            if (_sceneManagerType != null)
            {
                _getActiveSceneMethod = _sceneManagerType.GetMethod("GetActiveScene",
                    BindingFlags.Public | BindingFlags.Static);
            }

            if (_gameObjectType != null)
            {
                _gameObjectFindMethod = _gameObjectType.GetMethod("Find",
                    BindingFlags.Public | BindingFlags.Static,
                    null,
                    new[] { typeof(string) },
                    null);
            }
        }

        public static bool IsUnityAvailable => _gameObjectType != null;

        #region Scene Information

        public static string GetSceneHierarchyJson()
        {
            try
            {
                var hierarchy = new List<object>();
                foreach (var go in GetRootGameObjects())
                {
                    hierarchy.Add(GetGameObjectHierarchy(go, 0, 3));
                }
                return JsonConvert.SerializeObject(hierarchy, MCPProtocol.JsonSettings);
            }
            catch (Exception ex)
            {
                return JsonConvert.SerializeObject(new { error = ex.Message }, MCPProtocol.JsonSettings);
            }
        }

        public static object GetGameObjectHierarchy(object gameObject, int depth, int maxDepth)
        {
            if (gameObject == null || depth > maxDepth) return null;

            try
            {
                var name = GetProperty(gameObject, "name")?.ToString() ?? "Unknown";
                var activeSelf = (bool)(GetProperty(gameObject, "activeSelf") ?? true);
                var activeInHierarchy = (bool)(GetProperty(gameObject, "activeInHierarchy") ?? true);
                var tag = GetProperty(gameObject, "tag")?.ToString() ?? "Untagged";
                var layer = (int)(GetProperty(gameObject, "layer") ?? 0);

                var transform = GetProperty(gameObject, "transform");
                var childCount = transform != null ? (int)(GetProperty(transform, "childCount") ?? 0) : 0;

                var result = new Dictionary<string, object>
                {
                    ["name"] = name,
                    ["activeSelf"] = activeSelf,
                    ["activeInHierarchy"] = activeInHierarchy,
                    ["tag"] = tag,
                    ["layer"] = layer,
                    ["instanceId"] = InvokeMethod(gameObject, "GetInstanceID"),
                    ["childCount"] = childCount
                };

                var components = InvokeMethod(gameObject, "GetComponents", new object[] { _componentType }) as Array;
                if (components != null)
                {
                    var componentList = new List<string>();
                    foreach (var comp in components)
                    {
                        if (comp != null) componentList.Add(comp.GetType().Name);
                    }
                    result["components"] = componentList;
                }

                if (depth < maxDepth && transform != null && childCount > 0)
                {
                    var children = new List<object>();
                    for (int i = 0; i < Math.Min(childCount, 50); i++)
                    {
                        var childTransform = InvokeMethod(transform, "GetChild", new object[] { i });
                        if (childTransform != null)
                        {
                            var childGo = GetProperty(childTransform, "gameObject");
                            var childHierarchy = GetGameObjectHierarchy(childGo, depth + 1, maxDepth);
                            if (childHierarchy != null) children.Add(childHierarchy);
                        }
                    }
                    if (childCount > 50)
                    {
                        children.Add(new Dictionary<string, object> { ["note"] = $"... and {childCount - 50} more children" });
                    }
                    result["children"] = children;
                }

                return result;
            }
            catch (Exception ex)
            {
                return new Dictionary<string, object> { ["error"] = ex.Message };
            }
        }

        public static List<object> GetRootGameObjects()
        {
            var result = new List<object>();

            try
            {
                if (_getActiveSceneMethod != null)
                {
                    var scene = _getActiveSceneMethod.Invoke(null, null);
                    if (scene != null)
                    {
                        var sceneType = scene.GetType();
                        var getRootMethod = sceneType.GetMethod("GetRootGameObjects", BindingFlags.Public | BindingFlags.Instance, null, Type.EmptyTypes, null);

                        if (getRootMethod != null && getRootMethod.Invoke(scene, null) is IEnumerable enumerable)
                        {
                            foreach (var obj in enumerable)
                            {
                                if (obj != null && IsValidUnityObject(obj)) result.Add(obj);
                            }
                        }
                    }
                }

                if (result.Count == 0 && _gameObjectType != null)
                {
                    foreach (var go in FindObjectsOfType(_gameObjectType))
                    {
                        if (go == null) continue;
                        var transform = GetProperty(go, "transform");
                        if (transform != null)
                        {
                            var parent = GetProperty(transform, "parent");
                            if (parent == null || !IsValidUnityObject(parent))
                            {
                                result.Add(go);
                            }
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Failed to get root game objects: {ex.Message}");
            }

            return result;
        }

        private static bool IsValidUnityObject(object obj)
        {
            if (obj == null) return false;
            try
            {
                var getInstanceId = obj.GetType().GetMethod("GetInstanceID", BindingFlags.Public | BindingFlags.Instance);
                if (getInstanceId != null)
                {
                    var instanceId = getInstanceId.Invoke(obj, null);
                    return instanceId != null && (int)instanceId != 0;
                }
                return true;
            }
            catch
            {
                return false;
            }
        }

        #endregion

        #region Game Information

        public static string GetGameInfoJson()
        {
            try
            {
                var bepInExVersion = AppDomain.CurrentDomain.GetAssemblies()
                    .FirstOrDefault(a => string.Equals(a.GetName().Name, "BepInEx", StringComparison.OrdinalIgnoreCase))?
                    .GetName().Version?.ToString();

                var info = new Dictionary<string, object>
                {
                    ["gameName"] = _applicationType != null ? GetStaticProperty(_applicationType, "productName") : null,
                    ["gameDeveloper"] = _applicationType != null ? GetStaticProperty(_applicationType, "companyName") : null,
                    ["gameVersion"] = _applicationType != null ? GetStaticProperty(_applicationType, "version") : null,
                    ["engineVersion"] = _applicationType != null ? GetStaticProperty(_applicationType, "unityVersion") : null,
                    ["bepInExVersion"] = bepInExVersion,
                    ["devMcpVersion"] = BuildInfo.Version,
                    ["platform"] = Environment.OSVersion.Platform.ToString(),
                    ["is64Bit"] = Environment.Is64BitProcess
                };

                if (_applicationType != null)
                {
                    info["productName"] = GetStaticProperty(_applicationType, "productName");
                    info["companyName"] = GetStaticProperty(_applicationType, "companyName");
                    info["unityVersion"] = GetStaticProperty(_applicationType, "unityVersion");
                    info["targetFrameRate"] = GetStaticProperty(_applicationType, "targetFrameRate");
                    info["runInBackground"] = GetStaticProperty(_applicationType, "runInBackground");
                    info["isPlaying"] = GetStaticProperty(_applicationType, "isPlaying");
                    info["isFocused"] = GetStaticProperty(_applicationType, "isFocused");
                    info["dataPath"] = GetStaticProperty(_applicationType, "dataPath");
                    info["persistentDataPath"] = GetStaticProperty(_applicationType, "persistentDataPath");
                }

                if (_screenType != null)
                {
                    info["screenWidth"] = GetStaticProperty(_screenType, "width");
                    info["screenHeight"] = GetStaticProperty(_screenType, "height");
                    info["fullScreen"] = GetStaticProperty(_screenType, "fullScreen");
                }

                if (_getActiveSceneMethod != null)
                {
                    var scene = _getActiveSceneMethod.Invoke(null, null);
                    if (scene != null)
                    {
                        info["currentScene"] = new Dictionary<string, object>
                        {
                            ["name"] = GetProperty(scene, "name"),
                            ["buildIndex"] = GetProperty(scene, "buildIndex"),
                            ["isLoaded"] = GetProperty(scene, "isLoaded"),
                            ["rootCount"] = GetProperty(scene, "rootCount")
                        };
                    }
                }

                return JsonConvert.SerializeObject(info, MCPProtocol.JsonSettings);
            }
            catch (Exception ex)
            {
                return JsonConvert.SerializeObject(new { error = ex.Message }, MCPProtocol.JsonSettings);
            }
        }

        public static Dictionary<string, object> GetTimeInfo()
        {
            var info = new Dictionary<string, object>();
            try
            {
                if (_timeType != null)
                {
                    info["time"] = GetStaticProperty(_timeType, "time");
                    info["deltaTime"] = GetStaticProperty(_timeType, "deltaTime");
                    info["fixedDeltaTime"] = GetStaticProperty(_timeType, "fixedDeltaTime");
                    info["unscaledTime"] = GetStaticProperty(_timeType, "unscaledTime");
                    info["unscaledDeltaTime"] = GetStaticProperty(_timeType, "unscaledDeltaTime");
                    info["timeScale"] = GetStaticProperty(_timeType, "timeScale");
                    info["frameCount"] = GetStaticProperty(_timeType, "frameCount");
                    info["realtimeSinceStartup"] = GetStaticProperty(_timeType, "realtimeSinceStartup");
                    info["timeSinceLevelLoad"] = GetStaticProperty(_timeType, "timeSinceLevelLoad");
                }
            }
            catch (Exception ex)
            {
                info["error"] = ex.Message;
            }
            return info;
        }

        #endregion

        #region Object Finding

        public static object FindGameObject(string path)
        {
            try
            {
                if (_gameObjectFindMethod != null)
                {
                    return _gameObjectFindMethod.Invoke(null, new object[] { path });
                }
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Failed to find GameObject: {ex.Message}");
            }
            return null;
        }

        public static object[] FindObjectsOfType(Type type)
        {
            var results = new List<object>();

            try
            {
                if (type == null) return Array.Empty<object>();

                if (_findObjectsOfTypeGenericMethod != null)
                {
                    try
                    {
                        var genericMethod = _findObjectsOfTypeGenericMethod.MakeGenericMethod(type);
                        if (genericMethod.Invoke(null, null) is IEnumerable enumerable)
                        {
                            foreach (var obj in enumerable)
                            {
                                if (obj != null && IsValidUnityObject(obj)) results.Add(obj);
                            }
                            if (results.Count > 0) return results.ToArray();
                        }
                    }
                    catch { }
                }

                if (_findObjectsOfTypeMethod != null)
                {
                    try
                    {
                        var result = _findObjectsOfTypeMethod.Invoke(null, new object[] { type });
                        if (result is IEnumerable enumerable)
                        {
                            foreach (var obj in enumerable)
                            {
                                if (obj != null && IsValidUnityObject(obj)) results.Add(obj);
                            }
                        }
                        if (results.Count > 0) return results.ToArray();
                    }
                    catch { }
                }
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Failed to find objects of type {type?.Name}: {ex.Message}");
            }

            return results.ToArray();
        }

        public static object[] FindObjectsOfType(Type type, bool includeInactive)
        {
            if (!includeInactive) return FindObjectsOfType(type);

            var results = new List<object>();
            try
            {
                if (_objectType != null)
                {
                    var methods = _objectType.GetMethods(BindingFlags.Public | BindingFlags.Static)
                        .Where(m => m.Name == "FindObjectsOfType" && m.GetParameters().Length == 2);

                    foreach (var method in methods)
                    {
                        var parameters = method.GetParameters();
                        if (parameters.Length == 2 && parameters[1].ParameterType == typeof(bool) && method.IsGenericMethodDefinition)
                        {
                            try
                            {
                                var genericMethod = method.MakeGenericMethod(type);
                                if (genericMethod.Invoke(null, new object[] { includeInactive }) is IEnumerable enumerable)
                                {
                                    foreach (var obj in enumerable)
                                    {
                                        if (obj != null && IsValidUnityObject(obj)) results.Add(obj);
                                    }
                                    if (results.Count > 0) return results.ToArray();
                                }
                            }
                            catch { }
                        }
                    }
                }
            }
            catch { }

            return FindObjectsOfType(type);
        }

        public static object FindObjectByInstanceId(int instanceId)
        {
            try
            {
                if (_findObjectsOfTypeMethod != null && _gameObjectType != null)
                {
                    if (_findObjectsOfTypeMethod.Invoke(null, new object[] { _gameObjectType }) is Array allObjects)
                    {
                        foreach (var obj in allObjects)
                        {
                            if (obj != null && InvokeMethod(obj, "GetInstanceID") is int id && id == instanceId)
                            {
                                return obj;
                            }
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Failed to find object by instance ID: {ex.Message}");
            }
            return null;
        }

        #endregion

        #region Component Operations

        public static object[] GetComponents(object gameObject)
        {
            var result = new List<object>();
            try
            {
                if (gameObject == null) return Array.Empty<object>();

                if (_componentType != null)
                {
                    var goType = gameObject.GetType();
                    var genericMethod = goType.GetMethods(BindingFlags.Public | BindingFlags.Instance)
                        .FirstOrDefault(m => m.Name == "GetComponents" && m.IsGenericMethodDefinition && m.GetParameters().Length == 0);

                    if (genericMethod != null)
                    {
                        try
                        {
                            var typedMethod = genericMethod.MakeGenericMethod(_componentType);
                            if (typedMethod.Invoke(gameObject, null) is IEnumerable enumerable)
                            {
                                foreach (var comp in enumerable)
                                {
                                    if (comp != null && IsValidUnityObject(comp)) result.Add(comp);
                                }
                                if (result.Count > 0) return result.ToArray();
                            }
                        }
                        catch { }
                    }

                    if (InvokeMethod(gameObject, "GetComponents", new object[] { _componentType }) is IEnumerable compEnum)
                    {
                        foreach (var comp in compEnum)
                        {
                            if (comp != null && IsValidUnityObject(comp)) result.Add(comp);
                        }
                        if (result.Count > 0) return result.ToArray();
                    }
                }

                var transform = GetProperty(gameObject, "transform");
                if (transform != null && IsValidUnityObject(transform))
                {
                    result.Add(transform);
                }
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Failed to get components: {ex.Message}");
            }
            return result.ToArray();
        }

        public static object GetComponent(object gameObject, string typeName)
        {
            try
            {
                if (gameObject == null) return null;
                var goType = gameObject.GetType();
                var componentType = ResolveUnityType(typeName);

                if (componentType != null)
                {
                    var genericMethod = goType.GetMethods(BindingFlags.Public | BindingFlags.Instance)
                        .FirstOrDefault(m => m.Name == "GetComponent" && m.IsGenericMethodDefinition && m.GetParameters().Length == 0);

                    if (genericMethod != null)
                    {
                        try
                        {
                            var typedMethod = genericMethod.MakeGenericMethod(componentType);
                            var component = typedMethod.Invoke(gameObject, null);
                            if (component != null && IsValidUnityObject(component)) return component;
                        }
                        catch { }
                    }

                    var getCompMethod = goType.GetMethod("GetComponent", BindingFlags.Public | BindingFlags.Instance, null, new[] { typeof(Type) }, null);
                    if (getCompMethod != null)
                    {
                        var component = getCompMethod.Invoke(gameObject, new object[] { componentType });
                        if (component != null && IsValidUnityObject(component)) return component;
                    }
                }

                var getCompByName = goType.GetMethod("GetComponent", BindingFlags.Public | BindingFlags.Instance, null, new[] { typeof(string) }, null);
                if (getCompByName != null)
                {
                    var component = getCompByName.Invoke(gameObject, new object[] { typeName });
                    if (component != null && IsValidUnityObject(component)) return component;
                }
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Failed to get component {typeName}: {ex.Message}");
            }
            return null;
        }

        private static Type ResolveUnityType(string typeName)
        {
            if (typeName == "Transform" || typeName == "UnityEngine.Transform") return _transformType;
            if (typeName == "Component" || typeName == "UnityEngine.Component") return _componentType;
            if (typeName == "Behaviour" || typeName == "UnityEngine.Behaviour") return _behaviourType;
            if (typeName == "GameObject" || typeName == "UnityEngine.GameObject") return _gameObjectType;

            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (asm.IsDynamic) continue;
                try
                {
                    var type = asm.GetType(typeName, false);
                    if (type != null) return type;

                    if (!typeName.Contains("."))
                    {
                        type = asm.GetType("UnityEngine." + typeName, false);
                        if (type != null) return type;
                    }
                }
                catch { }
            }
            return null;
        }

        public static bool SetBehaviourEnabled(object behaviour, bool enabled)
        {
            try
            {
                if (behaviour != null && _behaviourType != null && _behaviourType.IsInstanceOfType(behaviour))
                {
                    SetProperty(behaviour, "enabled", enabled);
                    return true;
                }
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Failed to set behaviour enabled: {ex.Message}");
            }
            return false;
        }

        public static Dictionary<string, object> InspectObject(object obj, bool includePrivate = false)
        {
            var result = new Dictionary<string, object>();
            if (obj == null)
            {
                result["error"] = "Object is null";
                return result;
            }

            try
            {
                var type = obj.GetType();
                result["type"] = type.FullName;
                result["assembly"] = type.Assembly.GetName().Name;

                if (_objectType != null && _objectType.IsInstanceOfType(obj))
                {
                    result["instanceId"] = InvokeMethod(obj, "GetInstanceID");
                    result["name"] = GetProperty(obj, "name");
                }

                var flags = BindingFlags.Public | BindingFlags.Instance;
                if (includePrivate) flags |= BindingFlags.NonPublic;

                var properties = new Dictionary<string, object>();
                foreach (var prop in type.GetProperties(flags))
                {
                    try
                    {
                        if (prop.CanRead && prop.GetIndexParameters().Length == 0)
                        {
                            properties[prop.Name] = FormatValue(prop.GetValue(obj), prop.PropertyType);
                        }
                    }
                    catch (Exception ex)
                    {
                        properties[prop.Name] = $"<error: {ex.Message}>";
                    }
                }
                result["properties"] = properties;

                var fields = new Dictionary<string, object>();
                foreach (var field in type.GetFields(flags))
                {
                    try
                    {
                        fields[field.Name] = FormatValue(field.GetValue(obj), field.FieldType);
                    }
                    catch (Exception ex)
                    {
                        fields[field.Name] = $"<error: {ex.Message}>";
                    }
                }
                result["fields"] = fields;

                var methods = new List<string>();
                foreach (var method in type.GetMethods(flags))
                {
                    if (!method.IsSpecialName)
                    {
                        var paramStr = string.Join(", ", method.GetParameters().Select(p => $"{p.ParameterType.Name} {p.Name}"));
                        methods.Add($"{method.ReturnType.Name} {method.Name}({paramStr})");
                    }
                }
                result["methods"] = methods;
            }
            catch (Exception ex)
            {
                result["error"] = ex.Message;
            }

            return result;
        }

        private static object FormatValue(object value, Type type)
        {
            if (value == null) return null;
            if (type.IsPrimitive || type == typeof(string) || type == typeof(decimal)) return value;
            if (type.IsEnum) return value.ToString();

            var typeName = type.Name;
            if (typeName.StartsWith("Vector") || typeName == "Quaternion" || typeName == "Color" || typeName == "Color32")
            {
                return value.ToString();
            }

            return new Dictionary<string, object>
            {
                ["type"] = type.Name,
                ["value"] = value.ToString()
            };
        }

        #endregion

        #region Reflection Helpers

        public static object GetProperty(object obj, string propertyName)
        {
            try
            {
                var prop = obj?.GetType().GetProperty(propertyName, BindingFlags.Public | BindingFlags.Instance);
                return prop?.GetValue(obj);
            }
            catch
            {
                return null;
            }
        }

        public static void SetProperty(object obj, string propertyName, object value)
        {
            try
            {
                var prop = obj?.GetType().GetProperty(propertyName, BindingFlags.Public | BindingFlags.Instance);
                prop?.SetValue(obj, value);
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Failed to set property {propertyName}: {ex.Message}");
            }
        }

        public static object GetStaticProperty(Type type, string propertyName)
        {
            try
            {
                var prop = type?.GetProperty(propertyName, BindingFlags.Public | BindingFlags.Static);
                return prop?.GetValue(null);
            }
            catch
            {
                return null;
            }
        }

        public static void SetStaticProperty(Type type, string propertyName, object value)
        {
            try
            {
                var prop = type?.GetProperty(propertyName, BindingFlags.Public | BindingFlags.Static);
                prop?.SetValue(null, value);
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Failed to set static property {propertyName}: {ex.Message}");
            }
        }

        public static void SetRunInBackground(bool value)
        {
            try
            {
                var prop = _applicationType?.GetProperty("runInBackground", BindingFlags.Public | BindingFlags.Static);
                if (prop != null && prop.CanWrite)
                {
                    prop.SetValue(null, value);
                    DevMcpLog.Msg($"Set runInBackground via property setter: {value}");
                    return;
                }

                var setterMethod = _applicationType?.GetMethod("set_runInBackground", BindingFlags.Public | BindingFlags.Static);
                if (setterMethod != null)
                {
                    setterMethod.Invoke(null, new object[] { value });
                    DevMcpLog.Msg($"Set runInBackground via setter method: {value}");
                    return;
                }

                DevMcpLog.Warning("Could not find a way to set runInBackground");
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Failed to set runInBackground: {ex.Message}");
            }
        }

        public static object InvokeMethod(object obj, string methodName, object[] parameters = null)
        {
            try
            {
                var method = obj?.GetType().GetMethod(methodName,
                    BindingFlags.Public | BindingFlags.Instance,
                    null,
                    parameters?.Select(p => p?.GetType() ?? typeof(object)).ToArray() ?? Type.EmptyTypes,
                    null);
                return method?.Invoke(obj, parameters);
            }
            catch
            {
                return null;
            }
        }

        public static object InvokeStaticMethod(Type type, string methodName, object[] parameters = null)
        {
            try
            {
                var method = type?.GetMethod(methodName,
                    BindingFlags.Public | BindingFlags.Static,
                    null,
                    parameters?.Select(p => p?.GetType() ?? typeof(object)).ToArray() ?? Type.EmptyTypes,
                    null);
                return method?.Invoke(null, parameters);
            }
            catch
            {
                return null;
            }
        }

        #endregion

        #region Screenshot

        public static byte[] CaptureScreenshot()
        {
            try
            {
                if (_texture2DType == null || _screenType == null) return null;

                var width = (int)GetStaticProperty(_screenType, "width");
                var height = (int)GetStaticProperty(_screenType, "height");
                object texture = Activator.CreateInstance(_texture2DType, new object[] { width, height });

                if (texture == null)
                {
                    throw new Exception("Could not create Texture2D");
                }

                var readMethod = _texture2DType.GetMethod("ReadPixels", new[] { _rectType ?? typeof(object), typeof(int), typeof(int) })
                                 ?? _texture2DType.GetMethods().FirstOrDefault(m => m.Name == "ReadPixels" && m.GetParameters().Length >= 3);

                if (readMethod != null && _rectType != null)
                {
                    object rect = null;
                    foreach (var ctor in _rectType.GetConstructors())
                    {
                        var parameters = ctor.GetParameters();
                        if (parameters.Length == 4 && parameters.All(p => p.ParameterType == typeof(float) || p.ParameterType == typeof(int)))
                        {
                            try
                            {
                                rect = ctor.Invoke(new object[] { 0f, 0f, (float)width, (float)height });
                                break;
                            }
                            catch { }
                        }
                    }

                    if (rect != null)
                    {
                        readMethod.Invoke(texture, new object[] { rect, 0, 0 });
                    }
                }

                var applyMethod = _texture2DType.GetMethod("Apply", Type.EmptyTypes)
                                  ?? _texture2DType.GetMethods().FirstOrDefault(m => m.Name == "Apply" && m.GetParameters().Length == 0);
                applyMethod?.Invoke(texture, null);

                var encodeMethod = _texture2DType.GetMethod("EncodeToPNG");
                if (encodeMethod == null)
                {
                    var imageConversionType = AppDomain.CurrentDomain.GetAssemblies()
                        .SelectMany(a => { try { return a.GetExportedTypes(); } catch { return Array.Empty<Type>(); } })
                        .FirstOrDefault(t => t.FullName == "UnityEngine.ImageConversion");

                    encodeMethod = imageConversionType?.GetMethod("EncodeToPNG", BindingFlags.Public | BindingFlags.Static);
                }

                if (encodeMethod != null)
                {
                    return encodeMethod.IsStatic
                        ? encodeMethod.Invoke(null, new[] { texture }) as byte[]
                        : encodeMethod.Invoke(texture, null) as byte[];
                }
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Failed to capture screenshot: {ex.Message}");
            }

            return null;
        }

        #endregion
    }
}
