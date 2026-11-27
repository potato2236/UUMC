using System;
using System.Collections.Generic;

namespace Uumc.DevMcp.Server
{
    /// <summary>
    /// Thread-safe ring buffer for captured Unity/BepInEx log messages.
    /// </summary>
    public sealed class LogBuffer
    {
        public const int DefaultMaxSize = 1000;

        private readonly int _maxSize;
        private readonly List<string> _entries = new List<string>();
        private readonly object _lock = new object();

        /// <summary>
        /// Default shared log buffer used when no explicit instance is provided.
        /// </summary>
        public static LogBuffer Active { get; internal set; } = new LogBuffer();

        public LogBuffer(int maxSize = DefaultMaxSize)
        {
            if (maxSize < 1)
            {
                throw new ArgumentOutOfRangeException(nameof(maxSize), "Max buffer size must be >= 1");
            }
            _maxSize = maxSize;
        }

        public int Count
        {
            get
            {
                lock (_lock)
                {
                    return _entries.Count;
                }
            }
        }

        public void Add(string log)
        {
            if (log == null) return;
            lock (_lock)
            {
                _entries.Add(log);
                if (_entries.Count > _maxSize)
                {
                    _entries.RemoveAt(0);
                }
            }
        }

        public List<string> GetLogs(int count = 100, string filter = null, string level = null)
        {
            if (count <= 0) return new List<string>();

            string levelMarker = level?.ToLowerInvariant() switch
            {
                "error" => "[ERROR]",
                "warning" => "[WARNING]",
                _ => null
            };

            lock (_lock)
            {
                var logs = new List<string>();
                int start = Math.Max(0, _entries.Count - count);

                for (int i = start; i < _entries.Count; i++)
                {
                    string entry = _entries[i];
                    if (levelMarker != null && entry.IndexOf(levelMarker, StringComparison.OrdinalIgnoreCase) < 0)
                        continue;

                    if (string.IsNullOrEmpty(filter) || entry.IndexOf(filter, StringComparison.OrdinalIgnoreCase) >= 0)
                        logs.Add(entry);
                }

                return logs;
            }
        }

        public void Clear()
        {
            lock (_lock)
            {
                _entries.Clear();
            }
        }
    }
}
