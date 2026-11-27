using System;
using System.Collections.Concurrent;

namespace Uumc.DevMcp.Server
{
    /// <summary>
    /// Dispatches actions to the Unity main thread from DevMcpPlugin.Update()
    /// </summary>
    public static class UnityMainThreadDispatcher
    {
        private static readonly ConcurrentQueue<Action> _executionQueue = new ConcurrentQueue<Action>();
        private static bool _initialized = false;

        public static bool IsInitialized => _initialized;

        public static void Initialize()
        {
            _initialized = true;
        }

        public static void Enqueue(Action action)
        {
            if (action == null) return;
            _executionQueue.Enqueue(action);
        }

        public static void ProcessQueue()
        {
            int processed = 0;
            while (_executionQueue.TryDequeue(out var action) && processed < 100)
            {
                try
                {
                    action();
                }
                catch (Exception ex)
                {
                    DevMcpLog.Error($"Error executing queued action: {ex}");
                }
                processed++;
            }
        }
    }
}
