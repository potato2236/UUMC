using System;
using System.Collections.Generic;
using System.Reflection;
using BepInEx;
using BepInEx.Configuration;
using BepInEx.Logging;
using HarmonyLib;
using UnityEngine;
using Uumc.DevMcp.Server;
using Uumc.DevMcp.Tools;

namespace Uumc.DevMcp
{
    public class McpLogger
    {
        private readonly ManualLogSource _log;
        public McpLogger(ManualLogSource log) => _log = log;
        public void Msg(string msg) => _log.LogInfo(msg);
        public void Warning(string msg) => _log.LogWarning(msg);
        public void Error(string msg) => _log.LogError(msg);
    }

    [BepInPlugin(PLUGIN_GUID, PLUGIN_NAME, PLUGIN_VERSION)]
    public class DevMcpPlugin : BaseUnityPlugin
    {
        public const string PLUGIN_GUID = "com.uumc.devmcp";
        public const string PLUGIN_NAME = "UUMC DevMCP";
        public const string PLUGIN_VERSION = "1.0.0";

        public static DevMcpPlugin Instance { get; private set; }
        public static new McpLogger Logger { get; private set; }
        public Harmony HarmonyInstance { get; private set; }

        private ConfigEntry<int> _port;
        private ConfigEntry<string> _bindAddress;
        private ConfigEntry<bool> _keepRunningInBackground;
        private ConfigEntry<string> _focusPatchType;
        private ConfigEntry<string> _focusPatchMethod;
        private ConfigEntry<bool> _blockUnfocusedPause;

        private MCPServer _server;
        private GameObject _hostObject;
        private bool _isShuttingDown;
        private readonly LogBuffer _logBuffer = new LogBuffer();

        private void Awake()
        {
            Instance = this;
            Logger = new McpLogger(base.Logger);
            DevMcpLog.OnMsg = Logger.Msg;
            DevMcpLog.OnWarning = Logger.Warning;
            DevMcpLog.OnError = Logger.Error;
            LogBuffer.Active = _logBuffer;
            BindConfiguration();

            Logger.Msg($"DevMCP initializing (port={_port.Value}, bind={_bindAddress.Value})...");
            Application.logMessageReceived += OnUnityLog;

            try
            {
                HarmonyInstance = new Harmony(PLUGIN_GUID);
                ApplyRuntimePatches();
                SpectatorWindowController.Initialize(HarmonyInstance);
                UnityMainThreadDispatcher.Initialize();
                EnsureHostObject();

                _server = new MCPServer(_port.Value, _bindAddress.Value, _logBuffer.GetLogs);
                RegisterTools();
                _server.Start();

                Logger.Msg($"DevMCP Server started on {_bindAddress.Value}:{_port.Value}");
            }
            catch (Exception ex)
            {
                Logger.Error($"Failed to start DevMCP server: {ex}");
            }
        }

        /// <summary>
        /// Creates an isolated HideAndDontSave host so DevMCP survives scene loads when
        /// BepInEx HideManagerGameObject is false (default BepInEx 5 configuration).
        /// </summary>
        private void EnsureHostObject()
        {
            if (_hostObject != null) return;

            _hostObject = new GameObject("UumcDevMcpHost");
            _hostObject.hideFlags = HideFlags.HideAndDontSave;
            DontDestroyOnLoad(_hostObject);
            _hostObject.AddComponent<DevMcpHostBehaviour>();
        }

        private void BindConfiguration()
        {
            _port = Config.Bind("Server", "Port", 27015, "TCP port for the MCP JSON-RPC server");
            _bindAddress = Config.Bind("Server", "BindAddress", "127.0.0.1", "Bind IP address (127.0.0.1 for local only, 0.0.0.0 for all interfaces)");
            _keepRunningInBackground = Config.Bind("Runtime", "KeepRunningInBackground", true, "Force Application.runInBackground = true");
            _focusPatchType = Config.Bind("Runtime", "FocusPatchType", "", "Optional full or simple type name whose focus method should be patched (empty = disabled)");
            _focusPatchMethod = Config.Bind("Runtime", "FocusPatchMethod", "OnApplicationFocus", "Target method name on FocusPatchType");
            _blockUnfocusedPause = Config.Bind("Runtime", "BlockUnfocusedPause", false, "When FocusPatchType is set, suppress calls where focus argument is false");
        }

        private void ApplyRuntimePatches()
        {
            if (_keepRunningInBackground.Value)
            {
                RunInBackgroundPatch.ApplyPatch(HarmonyInstance);
            }

            var focusType = (_focusPatchType.Value ?? string.Empty).Trim();
            var focusMethod = (_focusPatchMethod.Value ?? "OnApplicationFocus").Trim();
            if (!string.IsNullOrEmpty(focusType))
            {
                ConfigurableFocusPatch.ApplyPatch(
                    HarmonyInstance,
                    focusType,
                    focusMethod,
                    _blockUnfocusedPause.Value);
            }
        }

        internal void TickRuntime()
        {
            UnityMainThreadDispatcher.ProcessQueue();
            SpectatorWindowController.Tick();

            if (_keepRunningInBackground.Value && !Application.runInBackground)
            {
                Application.runInBackground = true;
            }
        }

        internal void Shutdown()
        {
            if (_isShuttingDown) return;
            _isShuttingDown = true;

            Application.logMessageReceived -= OnUnityLog;
            _server?.Stop();
            HarmonyInstance?.UnpatchSelf();
            Logger?.Msg("DevMCP Server stopped");
        }

        private void OnDestroy()
        {
            // When BepInEx HideManagerGameObject=false, Unity destroys BepInEx_Manager on first scene load.
            // Keep the server alive via _hostObject until ApplicationQuit unless the host itself is gone.
            if (_hostObject == null)
            {
                Shutdown();
            }
            else
            {
                Logger?.Msg("BepInEx ManagerObject destroyed; DevMCP continues running on isolated host.");
            }
        }

        private sealed class DevMcpHostBehaviour : MonoBehaviour
        {
            private void Update()
            {
                Instance?.TickRuntime();
            }

            private void OnApplicationQuit()
            {
                Instance?.Shutdown();
            }

            private void OnDestroy()
            {
                Instance?.Shutdown();
            }
        }

        private void RegisterTools()
        {
            IToolDefinition[] tools =
            {
                new ReadLogsToolDefinition(_logBuffer),
                new ClearLogsToolDefinition(_logBuffer),
                new ExecuteCSharpToolDefinition(),
                new EvaluateExpressionToolDefinition(),
                new GetSceneInfoToolDefinition(),
                new ListGameObjectsToolDefinition(),
                new FindGameObjectToolDefinition(),
                new ListComponentsToolDefinition(),
                new InspectComponentToolDefinition(),
                new ToggleBehaviourToolDefinition(),
                new SetPropertyToolDefinition(),
                new InvokeMethodToolDefinition(),
                new GetGameInfoToolDefinition(),
                new TakeScreenshotToolDefinition(),
                new GetTimeInfoToolDefinition(),
                new ListAssembliesToolDefinition(),
                new ListTypesToolDefinition(),
                new GetTypeInfoToolDefinition(),
                new FindObjectsOfTypeToolDefinition(),
                new SetTimeScaleToolDefinition(),
                new CursorControlToolDefinition(),
                new LoadSceneToolDefinition(),
                new InstantiateObjectToolDefinition(),
                new CreatePrimitiveToolDefinition(),
                new SetTransformToolDefinition(),
                new InspectMaterialToolDefinition(),
                new DestroyObjectToolDefinition(),
                new AddGameKnowledgeToolDefinition(),
                new GetGameKnowledgeToolDefinition(),
                new GetGameSummaryToolDefinition(),
                new SetPseudocodePathToolDefinition(),
                new SearchPseudocodeToolDefinition(),
                new ReadPseudocodeFileToolDefinition()
            };

            foreach (var tool in tools)
            {
                _server.RegisterTool(tool);
            }

            Logger.Msg($"Registered {_server.ToolCount} MCP tools");
        }

        private void OnUnityLog(string condition, string stackTrace, LogType type)
        {
            string prefix = type switch
            {
                LogType.Error or LogType.Exception or LogType.Assert => "[ERROR]",
                LogType.Warning => "[WARNING]",
                _ => "[MSG]"
            };

            AddToLogBuffer($"[{DateTime.Now:HH:mm:ss}] {prefix} {condition}");
        }

        public void AddToLogBuffer(string log) => _logBuffer.Add(log);

        public List<string> GetLogs(int count = 100, string filter = null, string level = null) =>
            _logBuffer.GetLogs(count, filter, level);

        public void ClearLogs() => _logBuffer.Clear();
    }

    public static class RunInBackgroundPatch
    {
        private static bool _patchApplied;

        public static void ApplyPatch(Harmony harmony)
        {
            if (_patchApplied) return;

            try
            {
                var setter = typeof(Application).GetMethod("set_runInBackground", BindingFlags.Public | BindingFlags.Static);
                if (setter != null)
                {
                    var prefix = typeof(RunInBackgroundPatch).GetMethod(nameof(Prefix), BindingFlags.Static | BindingFlags.NonPublic);
                    harmony.Patch(setter, new HarmonyMethod(prefix));
                    _patchApplied = true;
                    DevMcpPlugin.Logger?.Msg("Patched Application.set_runInBackground");
                }

                Application.runInBackground = true;
            }
            catch (Exception ex)
            {
                DevMcpPlugin.Logger?.Warning($"Failed to patch set_runInBackground: {ex.Message}");
            }
        }

        private static bool Prefix(bool value) => value;
    }

    public static class ConfigurableFocusPatch
    {
        private static bool _patchApplied;
        private static bool _blockUnfocusedPause;

        public static void ApplyPatch(Harmony harmony, string typeName, string methodName, bool blockUnfocusedPause)
        {
            if (_patchApplied || string.IsNullOrWhiteSpace(typeName) || string.IsNullOrWhiteSpace(methodName))
                return;

            _blockUnfocusedPause = blockUnfocusedPause;

            try
            {
                var targetType = FindType(typeName);
                if (targetType == null)
                {
                    DevMcpPlugin.Logger?.Warning($"FocusPatchType '{typeName}' not found in loaded assemblies");
                    return;
                }

                var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
                var targetMethod = targetType.GetMethod(methodName, flags);
                if (targetMethod == null)
                {
                    DevMcpPlugin.Logger?.Warning($"Method '{methodName}' not found on '{targetType.FullName}'");
                    return;
                }

                var parameters = targetMethod.GetParameters();
                if (parameters.Length == 0 || parameters[0].ParameterType != typeof(bool))
                {
                    DevMcpPlugin.Logger?.Warning(
                        $"Method '{targetType.FullName}.{methodName}' does not have a bool first parameter; skipping focus patch");
                    return;
                }

                var prefix = typeof(ConfigurableFocusPatch).GetMethod(nameof(FocusPrefix), BindingFlags.Static | BindingFlags.NonPublic);
                harmony.Patch(targetMethod, new HarmonyMethod(prefix));
                _patchApplied = true;
                DevMcpPlugin.Logger?.Msg($"Patched {targetType.FullName}.{methodName} (blockUnfocused={blockUnfocusedPause})");
            }
            catch (Exception ex)
            {
                DevMcpPlugin.Logger?.Warning($"Failed to apply focus patch: {ex.Message}");
            }
        }

        private static Type FindType(string typeName)
        {
            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (asm.IsDynamic) continue;
                try
                {
                    var exact = asm.GetType(typeName, false);
                    if (exact != null) return exact;
                }
                catch { }
            }

            if (!typeName.Contains('.'))
            {
                foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    if (asm.IsDynamic) continue;
                    try
                    {
                        foreach (var t in asm.GetTypes())
                        {
                            if (t.Name == typeName) return t;
                        }
                    }
                    catch { }
                }
            }

            return null;
        }

        private static bool FocusPrefix(bool __0) => !(_blockUnfocusedPause && !__0);
    }
}
