using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using Uumc.DevMcp.Server;

namespace Uumc.DevMcp.Tools
{
    public class ReadLogsToolDefinition : ToolDefinitionBase
    {
        private readonly Func<LogBuffer> _bufferProvider;

        public ReadLogsToolDefinition() : this(() => LogBuffer.Active) { }

        public ReadLogsToolDefinition(LogBuffer logBuffer)
            : this(() => logBuffer ?? LogBuffer.Active) { }

        private ReadLogsToolDefinition(Func<LogBuffer> bufferProvider)
        {
            _bufferProvider = bufferProvider;
        }

        public override string Name => "read_logs";
        public override string Description => "Read recent Unity/BepInEx log messages captured by DevMCP. Use this to see console output, errors, warnings, and mod logs.";
        public override bool RequiresMainThread => false;

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["count"] = new ToolPropertySchema
                    {
                        Type = "integer",
                        Description = "Number of recent log entries to retrieve (default: 100, max: 1000)",
                        Default = 100,
                        Minimum = 1,
                        Maximum = 1000
                    },
                    ["filter"] = new ToolPropertySchema
                    {
                        Type = "string",
                        Description = "Optional text filter - only returns logs containing this text (case-insensitive)"
                    },
                    ["level"] = new ToolPropertySchema
                    {
                        Type = "string",
                        Description = "Filter by log level",
                        Enum = new List<string> { "all", "error", "warning" }
                    }
                }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var count = GetIntArg(arguments, "count", 100);
            var filter = GetStringArg(arguments, "filter");
            var level = GetStringArg(arguments, "level", "all");

            if (count > 1000) count = 1000;
            if (count < 1) count = 1;

            var logs = _bufferProvider().GetLogs(count, filter, level);
            if (logs.Count == 0)
            {
                return TextResult("No log entries found matching the criteria.");
            }

            return TextResult(string.Join("\n", logs));
        }
    }

    public class ClearLogsToolDefinition : ToolDefinitionBase
    {
        private readonly Func<LogBuffer> _bufferProvider;

        public ClearLogsToolDefinition() : this(() => LogBuffer.Active) { }

        public ClearLogsToolDefinition(LogBuffer logBuffer)
            : this(() => logBuffer ?? LogBuffer.Active) { }

        private ClearLogsToolDefinition(Func<LogBuffer> bufferProvider)
        {
            _bufferProvider = bufferProvider;
        }

        public override string Name => "clear_logs";
        public override string Description => "Clear the DevMCP log buffer. This removes all stored log messages.";
        public override bool RequiresMainThread => false;

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>()
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            _bufferProvider().Clear();
            return TextResult("Log buffer cleared.");
        }
    }
}
