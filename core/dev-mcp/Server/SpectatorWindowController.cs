using System;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using HarmonyLib;

namespace Uumc.DevMcp.Server
{
    /// <summary>
    /// Manages an ephemeral, non-intrusive spectator game window during automated UUMC test runs.
    /// Activated solely via process environment variables (UUMC_SPECTATOR_WINDOW=1) so manual game
    /// launches remain completely unaffected.
    /// </summary>
    public static class SpectatorWindowController
    {
        private const string EnvEnabled = "UUMC_SPECTATOR_WINDOW";
        private const string EnvWidth = "UUMC_SPECTATOR_WIDTH";
        private const string EnvHeight = "UUMC_SPECTATOR_HEIGHT";
        private const string EnvPos = "UUMC_SPECTATOR_POS";
        private const string EnvMute = "UUMC_SPECTATOR_MUTE";
        private const string EnvUnlockCursor = "UUMC_SPECTATOR_UNLOCK_CURSOR";
        private const string EnvInteractive = "UUMC_SPECTATOR_INTERACTIVE";
        private const string EnvTopmost = "UUMC_SPECTATOR_TOPMOST";

        private static bool _initialized;
        private static bool _enabled;
        private static int _targetWidth = 640;
        private static int _targetHeight = 360;
        private static string _targetPos = "bottom-right";
        private static bool _muteAudio = true;
        private static bool _unlockCursor = true;
        private static bool _interactive;
        private static bool _topmost = true;

        private static bool _isApplyingResolution;
        private static bool _initialInteractiveSyncDone;
        private static IntPtr _cachedHwnd = IntPtr.Zero;
        private static int _frameCounter;

        private static Type _screenType;
        private static Type _fullScreenModeType;
        private static object _windowedModeValue;
        private static PropertyInfo _fullScreenProp;
        private static PropertyInfo _fullScreenModeProp;
        private static PropertyInfo _screenWidthProp;
        private static PropertyInfo _screenHeightProp;
        private static MethodInfo _setResolutionBasic;

        private static PropertyInfo _audioVolumeProp;
        private static PropertyInfo _cursorLockProp;
        private static PropertyInfo _cursorVisibleProp;

        public static bool IsEnabled => _enabled;

        public static void Initialize(Harmony harmony)
        {
            if (_initialized) return;
            _initialized = true;

            _enabled = ParseEnvBool(EnvEnabled, defaultValue: false);
            if (!_enabled) return;

            _targetWidth = ClampInt(ParseEnvInt(EnvWidth, 640), 160, 3840);
            _targetHeight = ClampInt(ParseEnvInt(EnvHeight, 360), 90, 2160);
            _targetPos = (Environment.GetEnvironmentVariable(EnvPos) ?? "bottom-right").Trim().ToLowerInvariant();
            _muteAudio = ParseEnvBool(EnvMute, defaultValue: true);
            _interactive = ParseEnvBool(EnvInteractive, defaultValue: false);
            _unlockCursor = !_interactive && ParseEnvBool(EnvUnlockCursor, defaultValue: true);
            _topmost = !_interactive && ParseEnvBool(EnvTopmost, defaultValue: true);

            DevMcpPlugin.Logger?.Msg(
                $"Spectator window enabled ({_targetWidth}x{_targetHeight}, pos={_targetPos}, " +
                $"mute={_muteAudio}, interactive={_interactive}, unlockCursor={_unlockCursor})");

            if (harmony != null)
            {
                ApplyUnityPatches(harmony);
            }

            EnforceUnitySettings();
            if (IsWindows() && SyncNativeWindow())
            {
                _initialInteractiveSyncDone = true;
            }
        }

        public static void Tick()
        {
            if (!_enabled) return;

            _frameCounter++;
            if (_frameCounter % 15 != 1) return;

            EnforceUnitySettings();
            if (!IsWindows()) return;
            if (_interactive && _initialInteractiveSyncDone) return;

            if (SyncNativeWindow() && _interactive)
            {
                _initialInteractiveSyncDone = true;
            }
        }

        private static void ApplyUnityPatches(Harmony harmony)
        {
            CacheUnityTypes();
            PatchScreen(harmony);
            if (_muteAudio) PatchAudio(harmony);
            if (_unlockCursor) PatchCursor(harmony);
        }

        private static void CacheUnityTypes()
        {
            _screenType = FindType("UnityEngine.Screen");
            _fullScreenModeType = FindType("UnityEngine.FullScreenMode");
            if (_fullScreenModeType != null && _fullScreenModeType.IsEnum)
            {
                try { _windowedModeValue = Enum.Parse(_fullScreenModeType, "Windowed", ignoreCase: true); }
                catch { _windowedModeValue = Enum.ToObject(_fullScreenModeType, 3); }
            }

            if (_screenType == null) return;
            _fullScreenProp = _screenType.GetProperty("fullScreen", BindingFlags.Public | BindingFlags.Static);
            _fullScreenModeProp = _screenType.GetProperty("fullScreenMode", BindingFlags.Public | BindingFlags.Static);
            _screenWidthProp = _screenType.GetProperty("width", BindingFlags.Public | BindingFlags.Static);
            _screenHeightProp = _screenType.GetProperty("height", BindingFlags.Public | BindingFlags.Static);
            _setResolutionBasic = _screenType.GetMethod(
                "SetResolution",
                BindingFlags.Public | BindingFlags.Static,
                null,
                new[] { typeof(int), typeof(int), typeof(bool) },
                null);
        }

        private static void PatchScreen(Harmony harmony)
        {
            if (_screenType == null) return;
            try
            {
                if (_fullScreenProp?.GetSetMethod(true) is MethodInfo setFs)
                {
                    var prefix = typeof(SpectatorWindowController).GetMethod(nameof(ForceFalsePrefix), BindingFlags.Static | BindingFlags.NonPublic);
                    harmony.Patch(setFs, new HarmonyMethod(prefix));
                }
                if (_fullScreenModeProp?.GetSetMethod(true) is MethodInfo setFsm)
                {
                    var prefix = typeof(SpectatorWindowController).GetMethod(nameof(ForceWindowedModePrefix), BindingFlags.Static | BindingFlags.NonPublic);
                    harmony.Patch(setFsm, new HarmonyMethod(prefix));
                }

                var setResPrefix = typeof(SpectatorWindowController).GetMethod(nameof(SetResolutionArgsPrefix), BindingFlags.Static | BindingFlags.NonPublic);
                var setResPostfix = typeof(SpectatorWindowController).GetMethod(nameof(SetResolutionPostfix), BindingFlags.Static | BindingFlags.NonPublic);
                int patchedCount = 0;
                foreach (var method in _screenType.GetMethods(BindingFlags.Public | BindingFlags.Static))
                {
                    if (method.Name != "SetResolution") continue;
                    harmony.Patch(method, new HarmonyMethod(setResPrefix), new HarmonyMethod(setResPostfix));
                    patchedCount++;
                }
                DevMcpPlugin.Logger?.Msg($"Spectator patched Screen (SetResolution overloads={patchedCount})");
            }
            catch (Exception ex)
            {
                DevMcpPlugin.Logger?.Warning($"Spectator failed to patch Screen: {ex.Message}");
            }
        }

        private static void PatchAudio(Harmony harmony)
        {
            try
            {
                var audioType = FindType("UnityEngine.AudioListener");
                _audioVolumeProp = audioType?.GetProperty("volume", BindingFlags.Public | BindingFlags.Static);
                if (_audioVolumeProp?.GetSetMethod(true) is MethodInfo setVol)
                {
                    var prefix = typeof(SpectatorWindowController).GetMethod(nameof(ForceZeroFloatPrefix), BindingFlags.Static | BindingFlags.NonPublic);
                    harmony.Patch(setVol, new HarmonyMethod(prefix));
                    DevMcpPlugin.Logger?.Msg("Spectator patched AudioListener.set_volume (muted)");
                }
            }
            catch (Exception ex)
            {
                DevMcpPlugin.Logger?.Warning($"Spectator failed to patch AudioListener: {ex.Message}");
            }
        }

        private static void PatchCursor(Harmony harmony)
        {
            try
            {
                var cursorType = FindType("UnityEngine.Cursor");
                if (cursorType == null) return;
                _cursorLockProp = cursorType.GetProperty("lockState", BindingFlags.Public | BindingFlags.Static);
                _cursorVisibleProp = cursorType.GetProperty("visible", BindingFlags.Public | BindingFlags.Static);

                if (_cursorLockProp?.GetSetMethod(true) is MethodInfo setLock)
                {
                    var prefix = typeof(SpectatorWindowController).GetMethod(nameof(ForceCursorUnlockedPrefix), BindingFlags.Static | BindingFlags.NonPublic);
                    harmony.Patch(setLock, new HarmonyMethod(prefix));
                }
                if (_cursorVisibleProp?.GetSetMethod(true) is MethodInfo setVis)
                {
                    var prefix = typeof(SpectatorWindowController).GetMethod(nameof(ForceTruePrefix), BindingFlags.Static | BindingFlags.NonPublic);
                    harmony.Patch(setVis, new HarmonyMethod(prefix));
                }
                DevMcpPlugin.Logger?.Msg("Spectator patched Cursor lockState/visible");
            }
            catch (Exception ex)
            {
                DevMcpPlugin.Logger?.Warning($"Spectator failed to patch Cursor: {ex.Message}");
            }
        }

        private static void EnforceUnitySettings()
        {
            try
            {
                EnforceWindowedScreen();
                EnforceAudioMute();
                EnforceCursorUnlocked();
            }
            catch (Exception ex)
            {
                DevMcpPlugin.Logger?.Warning($"Spectator EnforceUnitySettings error: {ex.Message}");
            }
        }

        private static void EnforceWindowedScreen()
        {
            bool isFs = _fullScreenProp != null && _fullScreenProp.CanRead && _fullScreenProp.GetValue(null) is bool fs && fs;
            bool wrongMode = _fullScreenModeProp != null && _fullScreenModeProp.CanRead && _windowedModeValue != null
                && _fullScreenModeProp.GetValue(null) is object curMode && !curMode.Equals(_windowedModeValue);

            bool sizeMismatch = false;
            if (!_interactive || !_initialInteractiveSyncDone)
            {
                int curW = _screenWidthProp?.GetValue(null) is int w ? w : _targetWidth;
                int curH = _screenHeightProp?.GetValue(null) is int h ? h : _targetHeight;
                sizeMismatch = Math.Abs(curW - _targetWidth) > 2 || Math.Abs(curH - _targetHeight) > 2;
            }

            if ((!isFs && !wrongMode && !sizeMismatch) || _isApplyingResolution) return;

            _isApplyingResolution = true;
            try
            {
                if (_fullScreenModeProp != null && _fullScreenModeProp.CanWrite && _windowedModeValue != null)
                    _fullScreenModeProp.SetValue(null, _windowedModeValue);
                if (_fullScreenProp != null && _fullScreenProp.CanWrite)
                    _fullScreenProp.SetValue(null, false);
                if (sizeMismatch && _setResolutionBasic != null)
                    _setResolutionBasic.Invoke(null, new object[] { _targetWidth, _targetHeight, false });
            }
            finally
            {
                _isApplyingResolution = false;
            }
        }

        private static void EnforceAudioMute()
        {
            if (!_muteAudio || _audioVolumeProp == null || !_audioVolumeProp.CanWrite) return;
            if (!(_audioVolumeProp.GetValue(null) is float vol) || vol > 0.0001f)
            {
                _audioVolumeProp.SetValue(null, 0f);
            }
        }

        private static void EnforceCursorUnlocked()
        {
            if (!_unlockCursor) return;
            if (_cursorLockProp != null && _cursorLockProp.CanWrite)
            {
                var lockVal = _cursorLockProp.GetValue(null);
                if (lockVal != null && Convert.ToInt32(lockVal) != 0)
                {
                    _cursorLockProp.SetValue(null, Enum.ToObject(_cursorLockProp.PropertyType, 0));
                }
            }
            if (_cursorVisibleProp != null && _cursorVisibleProp.CanWrite && _cursorVisibleProp.GetValue(null) is bool vis && !vis)
            {
                _cursorVisibleProp.SetValue(null, true);
            }
        }

        private static void ForceFalsePrefix(ref bool __0) => __0 = false;
        private static void ForceTruePrefix(ref bool __0) => __0 = true;
        private static void ForceZeroFloatPrefix(ref float __0) => __0 = 0f;

        private static void ForceWindowedModePrefix(object[] __args)
        {
            if (__args != null && __args.Length > 0 && _windowedModeValue != null)
                __args[0] = _windowedModeValue;
        }

        private static void ForceCursorUnlockedPrefix(object[] __args)
        {
            if (__args != null && __args.Length > 0 && _cursorLockProp != null)
                __args[0] = Enum.ToObject(_cursorLockProp.PropertyType, 0);
        }

        private static void SetResolutionArgsPrefix(MethodBase __originalMethod, object[] __args)
        {
            if (__args == null || __args.Length < 3) return;
            var parameters = __originalMethod.GetParameters();
            if (parameters.Length >= 2 && parameters[0].ParameterType == typeof(int) && parameters[1].ParameterType == typeof(int))
            {
                __args[0] = _targetWidth;
                __args[1] = _targetHeight;
            }
            for (int i = 2; i < parameters.Length; i++)
            {
                var pType = parameters[i].ParameterType;
                if (pType == typeof(bool)) __args[i] = false;
                else if (_fullScreenModeType != null && pType == _fullScreenModeType && _windowedModeValue != null)
                    __args[i] = _windowedModeValue;
            }
        }

        private static void SetResolutionPostfix()
        {
            if (_enabled && IsWindows() && (!_interactive || !_initialInteractiveSyncDone))
            {
                if (SyncNativeWindow() && _interactive) _initialInteractiveSyncDone = true;
            }
        }

        private static bool SyncNativeWindow()
        {
            try
            {
                var hwnd = ResolveUnityMainWindow();
                if (hwnd == IntPtr.Zero) return false;

                bool styleChanged = EnsureNonIntrusiveExStyle(hwnd);
                ComputeTargetWindowBounds(hwnd, out int x, out int y, out int outerW, out int outerH);
                bool alreadyPlaced = IsWindowPlacedNear(hwnd, x, y, outerW, outerH);
                if (alreadyPlaced && !styleChanged) return true;

                IntPtr insertAfter = _topmost ? HWND_TOPMOST : HWND_NOTOPMOST;
                uint flags = SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED;
                if (alreadyPlaced) flags |= SWP_NOMOVE | SWP_NOSIZE;
                SetWindowPos(hwnd, insertAfter, x, y, outerW, outerH, flags);
                return true;
            }
            catch (Exception ex)
            {
                DevMcpPlugin.Logger?.Warning($"Spectator SyncNativeWindow error: {ex.Message}");
                return false;
            }
        }

        private static bool EnsureNonIntrusiveExStyle(IntPtr hwnd)
        {
            if (_interactive) return false;
            long exStyle = GetWindowLongPtrCompat(hwnd, GWL_EXSTYLE);
            long desiredEx = exStyle | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW;
            if (exStyle == desiredEx) return false;
            SetWindowLongPtrCompat(hwnd, GWL_EXSTYLE, desiredEx);
            return true;
        }

        private static void ComputeTargetWindowBounds(IntPtr hwnd, out int x, out int y, out int outerW, out int outerH)
        {
            long style = GetWindowLongPtrCompat(hwnd, GWL_STYLE);
            long curEx = GetWindowLongPtrCompat(hwnd, GWL_EXSTYLE);
            var rect = new RECT { Left = 0, Top = 0, Right = _targetWidth, Bottom = _targetHeight };
            AdjustWindowRectEx(ref rect, (uint)style, false, (uint)curEx);

            outerW = Math.Max(160, rect.Right - rect.Left);
            outerH = Math.Max(90, rect.Bottom - rect.Top);

            var workArea = new RECT { Left = 0, Top = 0, Right = 1920, Bottom = 1080 };
            SystemParametersInfo(SPI_GETWORKAREA, 0, ref workArea, 0);

            const int margin = 16;
            x = _targetPos.EndsWith("left", StringComparison.Ordinal)
                ? workArea.Left + margin
                : Math.Max(workArea.Left, workArea.Right - outerW - margin);
            y = _targetPos.StartsWith("top-", StringComparison.Ordinal)
                ? workArea.Top + margin
                : Math.Max(workArea.Top, workArea.Bottom - outerH - margin);
        }

        private static bool IsWindowPlacedNear(IntPtr hwnd, int x, int y, int outerW, int outerH)
        {
            if (!GetWindowRect(hwnd, out var curRect)) return false;
            return Math.Abs(curRect.Left - x) <= 2
                && Math.Abs(curRect.Top - y) <= 2
                && Math.Abs((curRect.Right - curRect.Left) - outerW) <= 4
                && Math.Abs((curRect.Bottom - curRect.Top) - outerH) <= 4;
        }

        private static IntPtr ResolveUnityMainWindow()
        {
            if (_cachedHwnd != IntPtr.Zero && IsWindow(_cachedHwnd) && IsWindowVisible(_cachedHwnd))
                return _cachedHwnd;

            uint currentPid = GetCurrentProcessId();
            IntPtr unityHwnd = IntPtr.Zero;
            IntPtr fallbackHwnd = IntPtr.Zero;
            int bestFallbackArea = 0;

            EnumWindows((hWnd, lParam) =>
            {
                if (!IsCandidateProcessWindow(hWnd, currentPid, out string cls, out int area)) return true;
                if (string.Equals(cls, "UnityWndClass", StringComparison.OrdinalIgnoreCase))
                {
                    unityHwnd = hWnd;
                    return false;
                }
                if (GetWindowTextLength(hWnd) > 0 && area > bestFallbackArea)
                {
                    bestFallbackArea = area;
                    fallbackHwnd = hWnd;
                }
                return true;
            }, IntPtr.Zero);

            IntPtr resolved = unityHwnd != IntPtr.Zero ? unityHwnd : fallbackHwnd;
            if (resolved != IntPtr.Zero)
            {
                _cachedHwnd = resolved;
                DevMcpPlugin.Logger?.Msg($"Spectator attached to window HWND=0x{resolved.ToInt64():X} (unityClass={unityHwnd != IntPtr.Zero})");
            }
            return resolved;
        }

        private static bool IsCandidateProcessWindow(IntPtr hWnd, uint currentPid, out string cls, out int area)
        {
            cls = string.Empty;
            area = 0;
            GetWindowThreadProcessId(hWnd, out uint pid);
            if (pid != currentPid || !IsWindowVisible(hWnd) || GetWindow(hWnd, GW_OWNER) != IntPtr.Zero)
                return false;

            var classBuf = new StringBuilder(128);
            GetClassName(hWnd, classBuf, classBuf.Capacity);
            cls = classBuf.ToString();
            if (string.Equals(cls, "ConsoleWindowClass", StringComparison.OrdinalIgnoreCase)
                || cls.StartsWith("UAC_", StringComparison.OrdinalIgnoreCase)
                || cls.StartsWith("UAC ", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }

            if (!GetWindowRect(hWnd, out var r)) return false;
            int w = r.Right - r.Left;
            int h = r.Bottom - r.Top;
            if (w <= 0 || h <= 0) return false;
            area = w * h;
            return true;
        }

        private static Type FindType(string fullName)
        {
            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (asm.IsDynamic) continue;
                try
                {
                    var t = asm.GetType(fullName, false);
                    if (t != null) return t;
                }
                catch { }
            }
            return null;
        }

        private static bool IsWindows() => RuntimeInformation.IsOSPlatform(OSPlatform.Windows);

        private static bool ParseEnvBool(string name, bool defaultValue)
        {
            var raw = Environment.GetEnvironmentVariable(name);
            if (string.IsNullOrWhiteSpace(raw)) return defaultValue;
            raw = raw.Trim().ToLowerInvariant();
            if (raw == "1" || raw == "true" || raw == "yes" || raw == "on") return true;
            if (raw == "0" || raw == "false" || raw == "no" || raw == "off") return false;
            return defaultValue;
        }

        private static int ParseEnvInt(string name, int defaultValue) =>
            int.TryParse(Environment.GetEnvironmentVariable(name), out int parsed) ? parsed : defaultValue;

        private static int ClampInt(int value, int min, int max) => Math.Max(min, Math.Min(max, value));

        #region Win32 P/Invoke

        private const int GWL_STYLE = -16;
        private const int GWL_EXSTYLE = -20;
        private const long WS_EX_TRANSPARENT = 0x00000020L;
        private const long WS_EX_TOOLWINDOW = 0x00000080L;
        private const long WS_EX_NOACTIVATE = 0x08000000L;
        private const uint GW_OWNER = 4;
        private const uint SPI_GETWORKAREA = 0x0030;
        private const uint SWP_NOSIZE = 0x0001;
        private const uint SWP_NOMOVE = 0x0002;
        private const uint SWP_NOACTIVATE = 0x0010;
        private const uint SWP_FRAMECHANGED = 0x0020;
        private const uint SWP_SHOWWINDOW = 0x0040;

        private static readonly IntPtr HWND_TOPMOST = new IntPtr(-1);
        private static readonly IntPtr HWND_NOTOPMOST = new IntPtr(-2);

        private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

        [StructLayout(LayoutKind.Sequential)]
        private struct RECT
        {
            public int Left;
            public int Top;
            public int Right;
            public int Bottom;
        }

        [DllImport("kernel32.dll")]
        private static extern uint GetCurrentProcessId();

        [DllImport("user32.dll")]
        private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

        [DllImport("user32.dll")]
        private static extern bool IsWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool IsWindowVisible(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern IntPtr GetWindow(IntPtr hWnd, uint uCmd);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetClassName(IntPtr hWnd, StringBuilder lpClassName, int nMaxCount);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowTextLength(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

        [DllImport("user32.dll")]
        private static extern bool AdjustWindowRectEx(ref RECT lpRect, uint dwStyle, bool bMenu, uint dwExStyle);

        [DllImport("user32.dll")]
        private static extern bool SystemParametersInfo(uint uiAction, uint uiParam, ref RECT pvParam, uint fWinIni);

        [DllImport("user32.dll")]
        private static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);

        [DllImport("user32.dll", EntryPoint = "GetWindowLong")]
        private static extern int GetWindowLong32(IntPtr hWnd, int nIndex);

        [DllImport("user32.dll", EntryPoint = "GetWindowLongPtr")]
        private static extern IntPtr GetWindowLongPtr64(IntPtr hWnd, int nIndex);

        [DllImport("user32.dll", EntryPoint = "SetWindowLong")]
        private static extern int SetWindowLong32(IntPtr hWnd, int nIndex, int dwNewLong);

        [DllImport("user32.dll", EntryPoint = "SetWindowLongPtr")]
        private static extern IntPtr SetWindowLongPtr64(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

        private static long GetWindowLongPtrCompat(IntPtr hWnd, int nIndex) =>
            IntPtr.Size == 8 ? GetWindowLongPtr64(hWnd, nIndex).ToInt64() : GetWindowLong32(hWnd, nIndex);

        private static void SetWindowLongPtrCompat(IntPtr hWnd, int nIndex, long value)
        {
            if (IntPtr.Size == 8) SetWindowLongPtr64(hWnd, nIndex, new IntPtr(value));
            else SetWindowLong32(hWnd, nIndex, unchecked((int)value));
        }

        #endregion
    }
}
