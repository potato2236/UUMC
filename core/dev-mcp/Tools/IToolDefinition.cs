using System;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Uumc.DevMcp.Server;

namespace Uumc.DevMcp.Tools
{
    public interface IToolDefinition
    {
        string Name { get; }
        bool RequiresMainThread { get; }
        ToolInfo GetInfo();
        CallToolResult Execute(Dictionary<string, JToken> arguments);
    }

    public abstract class ToolDefinitionBase : IToolDefinition
    {
        public abstract string Name { get; }
        public abstract string Description { get; }
        public virtual bool RequiresMainThread => true;

        public virtual ToolInfo GetInfo()
        {
            return new ToolInfo
            {
                Name = Name,
                Description = Description,
                InputSchema = GetInputSchema()
            };
        }

        protected abstract ToolInputSchema GetInputSchema();

        public abstract CallToolResult Execute(Dictionary<string, JToken> arguments);

        #region Argument Helpers

        protected string GetStringArg(Dictionary<string, JToken> arguments, string name, string defaultValue = null)
        {
            if (arguments.TryGetValue(name, out var token) && token.Type == JTokenType.String)
            {
                return token.Value<string>();
            }
            return defaultValue;
        }

        protected int GetIntArg(Dictionary<string, JToken> arguments, string name, int defaultValue = 0)
        {
            if (arguments.TryGetValue(name, out var token) && token.Type == JTokenType.Integer)
            {
                return token.Value<int>();
            }
            return defaultValue;
        }

        protected bool GetBoolArg(Dictionary<string, JToken> arguments, string name, bool defaultValue = false)
        {
            if (arguments.TryGetValue(name, out var token) && token.Type == JTokenType.Boolean)
            {
                return token.Value<bool>();
            }
            return defaultValue;
        }

        protected double GetDoubleArg(Dictionary<string, JToken> arguments, string name, double defaultValue = 0.0)
        {
            if (arguments.TryGetValue(name, out var token) && (token.Type == JTokenType.Float || token.Type == JTokenType.Integer))
            {
                return token.Value<double>();
            }
            return defaultValue;
        }

        protected T GetArg<T>(Dictionary<string, JToken> arguments, string name, T defaultValue = default)
        {
            if (arguments.TryGetValue(name, out var token))
            {
                try
                {
                    return token.ToObject<T>();
                }
                catch { }
            }
            return defaultValue;
        }

        #endregion

        #region Unity Lookup Helpers

        protected bool TryResolveGameObject(
            Dictionary<string, JToken> arguments,
            string pathKey,
            string idKey,
            out object gameObject,
            out CallToolResult failure)
        {
            var path = GetStringArg(arguments, pathKey);
            var instanceId = GetIntArg(arguments, idKey, 0);

            if (!string.IsNullOrEmpty(path))
            {
                gameObject = UnityHelper.FindGameObject(path);
            }
            else if (instanceId != 0)
            {
                gameObject = UnityHelper.FindObjectByInstanceId(instanceId);
            }
            else
            {
                gameObject = null;
                failure = ErrorResult($"Either '{pathKey}' or '{idKey}' must be provided");
                return false;
            }

            if (gameObject == null)
            {
                failure = TextResult($"GameObject not found: {path ?? instanceId.ToString()}");
                return false;
            }

            failure = null;
            return true;
        }

        protected bool TryResolveComponent(
            object gameObject,
            string componentType,
            int componentIndex,
            out object component,
            out CallToolResult failure)
        {
            var components = UnityHelper.GetComponents(gameObject);
            int matchIndex = 0;

            foreach (var comp in components)
            {
                var typeName = comp.GetType().Name;
                var fullName = comp.GetType().FullName;

                if (typeName.Equals(componentType, StringComparison.OrdinalIgnoreCase) ||
                    (fullName != null && fullName.Equals(componentType, StringComparison.OrdinalIgnoreCase)))
                {
                    if (matchIndex == componentIndex)
                    {
                        component = comp;
                        failure = null;
                        return true;
                    }
                    matchIndex++;
                }
            }

            component = null;
            failure = TextResult($"Component '{componentType}' not found on GameObject");
            return false;
        }

        #endregion

        #region Result Helpers

        protected CallToolResult TextResult(string text)
        {
            return new CallToolResult
            {
                Content = new List<ToolContent> { ToolContent.TextContent(text) }
            };
        }

        protected CallToolResult JsonResult(object obj)
        {
            var json = JsonConvert.SerializeObject(obj, MCPProtocol.JsonSettings);
            return new CallToolResult
            {
                Content = new List<ToolContent> { ToolContent.TextContent(json) }
            };
        }

        protected CallToolResult ErrorResult(string message)
        {
            return new CallToolResult
            {
                Content = new List<ToolContent> { ToolContent.TextContent($"Error: {message}") },
                IsError = true
            };
        }

        protected CallToolResult ImageResult(byte[] imageData, string mimeType = "image/png")
        {
            return new CallToolResult
            {
                Content = new List<ToolContent>
                {
                    ToolContent.ImageContent(Convert.ToBase64String(imageData), mimeType)
                }
            };
        }

        #endregion
    }
}
