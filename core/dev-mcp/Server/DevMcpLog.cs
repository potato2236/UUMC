using System;

namespace Uumc.DevMcp.Server
{
    /// <summary>
    /// Lightweight logging facade decoupling pure server/tool logic from BepInEx ManualLogSource.
    /// </summary>
    public static class DevMcpLog
    {
        public static Action<string> OnMsg { get; set; }
        public static Action<string> OnWarning { get; set; }
        public static Action<string> OnError { get; set; }

        public static void Msg(string message) => OnMsg?.Invoke(message);
        public static void Warning(string message) => OnWarning?.Invoke(message);
        public static void Error(string message) => OnError?.Invoke(message);
    }
}
