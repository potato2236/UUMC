using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Uumc.DevMcp.Tools;

namespace Uumc.DevMcp.Server
{
    /// <summary>
    /// MCP Server implementation using TCP with JSON-RPC 2.0
    /// </summary>
    public class MCPServer
    {
        private readonly int _port;
        private readonly IPAddress _bindAddress;
        private TcpListener _listener;
        private CancellationTokenSource _cancellation;
        private readonly ConcurrentDictionary<Guid, MCPClientHandler> _clients = new ConcurrentDictionary<Guid, MCPClientHandler>();
        private readonly Dictionary<string, IToolDefinition> _tools = new Dictionary<string, IToolDefinition>(StringComparer.OrdinalIgnoreCase);
        private readonly Func<int, string, string, List<string>> _logProvider;

        private bool _isRunning;

        public int ToolCount => _tools.Count;
        public bool IsRunning => _isRunning;

        public MCPServer(int port, string bindAddress = "127.0.0.1", Func<int, string, string, List<string>> logProvider = null)
        {
            _port = port;
            _logProvider = logProvider ?? ((count, filter, level) => LogBuffer.Active.GetLogs(count, filter, level));
            if (!IPAddress.TryParse(bindAddress, out _bindAddress))
            {
                _bindAddress = IPAddress.Loopback;
            }
        }

        public void RegisterTool(IToolDefinition tool)
        {
            _tools[tool.Name] = tool;
            DevMcpLog.Msg($"Registered tool: {tool.Name}");
        }

        public void Start()
        {
            if (_isRunning) return;

            _cancellation = new CancellationTokenSource();
            _listener = new TcpListener(_bindAddress, _port);
            _listener.Start();
            _isRunning = true;

            Task.Run(AcceptClientsAsync);
        }

        public void Stop()
        {
            if (!_isRunning) return;

            _isRunning = false;
            _cancellation?.Cancel();

            foreach (var client in _clients.Values)
            {
                client.Disconnect();
            }
            _clients.Clear();

            try
            {
                _listener?.Stop();
            }
            catch { }

            _listener = null;
            _cancellation?.Dispose();
            _cancellation = null;
        }

        private async Task AcceptClientsAsync()
        {
            while (_isRunning && !_cancellation.Token.IsCancellationRequested)
            {
                try
                {
                    var client = await _listener.AcceptTcpClientAsync();
                    var clientId = Guid.NewGuid();
                    var handler = new MCPClientHandler(clientId, client, this);
                    _clients[clientId] = handler;

                    DevMcpLog.Msg($"Client connected: {clientId}");
                    _ = Task.Run(() => handler.HandleClientAsync(_cancellation.Token));
                }
                catch (ObjectDisposedException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    if (_isRunning)
                    {
                        DevMcpLog.Warning($"Error accepting client: {ex.Message}");
                    }
                }
            }
        }

        internal void OnClientDisconnected(Guid clientId)
        {
            _clients.TryRemove(clientId, out _);
            DevMcpLog.Msg($"Client disconnected: {clientId}");
        }

        internal JsonRpcResponse HandleRequest(JsonRpcRequest request)
        {
            if (request == null || string.IsNullOrEmpty(request.Method))
            {
                return new JsonRpcResponse
                {
                    Id = request?.Id,
                    Error = JsonRpcError.InvalidRequest("Invalid or null JSON-RPC request")
                };
            }

            try
            {
                var response = request.Method switch
                {
                    "initialize" => HandleInitialize(request),
                    "initialized" => HandleInitialized(),
                    "notifications/initialized" => HandleInitialized(),
                    "ping" => new JsonRpcResponse { Id = request.Id, Result = new { } },
                    "tools/list" => HandleListTools(request),
                    "tools/call" => HandleCallTool(request),
                    "resources/list" => HandleListResources(request),
                    "resources/read" => HandleReadResource(request),
                    _ => new JsonRpcResponse
                    {
                        Id = request.Id,
                        Error = JsonRpcError.MethodNotFound($"Unknown method: {request.Method}")
                    }
                };
                return request.Id == null ? null : response;
            }
            catch (Exception ex)
            {
                DevMcpLog.Error($"Error handling request {request.Method}: {ex}");
                if (request.Id == null) return null;
                return new JsonRpcResponse
                {
                    Id = request.Id,
                    Error = JsonRpcError.InternalError(ex.Message)
                };
            }
        }

        private JsonRpcResponse HandleInitialize(JsonRpcRequest request)
        {
            var result = new InitializeResult
            {
                ProtocolVersion = MCPProtocol.MCP_VERSION,
                Capabilities = new ServerCapabilities
                {
                    Tools = new ToolsCapability { ListChanged = false },
                    Resources = new ResourcesCapability { Subscribe = false, ListChanged = false },
                    Logging = new LoggingCapability()
                },
                ServerInfo = new ServerInfo
                {
                    Name = BuildInfo.Name,
                    Version = BuildInfo.Version
                },
                Instructions = "Uumc.DevMcp provides tools to interact with running Unity (BepInEx 5) games: " +
                               "read logs, execute C# at runtime, inspect/control MonoBehaviours, and explore scene hierarchies."
            };

            return new JsonRpcResponse { Id = request.Id, Result = result };
        }

        private static JsonRpcResponse HandleInitialized() => null;

        private JsonRpcResponse HandleListTools(JsonRpcRequest request)
        {
            var result = new ListToolsResult();
            foreach (var tool in _tools.Values)
            {
                result.Tools.Add(tool.GetInfo());
            }
            return new JsonRpcResponse { Id = request.Id, Result = result };
        }

        private JsonRpcResponse HandleCallTool(JsonRpcRequest request)
        {
            CallToolParams callParams;
            try
            {
                callParams = request.Params?.ToObject<CallToolParams>(JsonSerializer.Create(MCPProtocol.JsonSettings));
                if (callParams == null || string.IsNullOrWhiteSpace(callParams.Name))
                {
                    return new JsonRpcResponse
                    {
                        Id = request.Id,
                        Error = JsonRpcError.InvalidParams("Missing tool call params or tool name")
                    };
                }
            }
            catch (Exception ex)
            {
                return new JsonRpcResponse
                {
                    Id = request.Id,
                    Error = JsonRpcError.InvalidParams($"Failed to parse tool call params: {ex.Message}")
                };
            }

            if (!_tools.TryGetValue(callParams.Name, out var tool))
            {
                return new JsonRpcResponse
                {
                    Id = request.Id,
                    Error = JsonRpcError.MethodNotFound($"Tool not found: {callParams.Name}")
                };
            }

            return ExecuteTool(request.Id, tool, NormalizeArguments(callParams.Arguments));
        }

        internal static Dictionary<string, JToken> NormalizeArguments(Dictionary<string, JToken> rawArgs)
        {
            var normalized = new Dictionary<string, JToken>(StringComparer.OrdinalIgnoreCase);
            if (rawArgs == null) return normalized;

            foreach (var kvp in rawArgs)
            {
                if (!string.IsNullOrEmpty(kvp.Key))
                {
                    normalized[kvp.Key] = kvp.Value;
                }
            }

            foreach (var kvp in rawArgs)
            {
                if (string.IsNullOrEmpty(kvp.Key) || kvp.Key.IndexOf('_') < 0) continue;
                var collapsed = kvp.Key.Replace("_", string.Empty);
                if (!string.IsNullOrEmpty(collapsed) && !normalized.ContainsKey(collapsed))
                {
                    normalized[collapsed] = kvp.Value;
                }
            }

            return normalized;
        }

        private static JsonRpcResponse ExecuteTool(object requestId, IToolDefinition tool, Dictionary<string, JToken> args)
        {
            try
            {
                CallToolResult result = null;
                Exception toolException = null;

                if (tool.RequiresMainThread)
                {
                    var tcs = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                    UnityMainThreadDispatcher.Enqueue(() =>
                    {
                        try
                        {
                            result = tool.Execute(args);
                        }
                        catch (Exception ex)
                        {
                            toolException = ex;
                        }
                        finally
                        {
                            tcs.TrySetResult(true);
                        }
                    });

                    if (!tcs.Task.Wait(TimeSpan.FromSeconds(30)))
                    {
                        return new JsonRpcResponse
                        {
                            Id = requestId,
                            Result = new CallToolResult
                            {
                                Content = new List<ToolContent>
                                {
                                    ToolContent.TextContent("Tool execution timed out after 30 seconds. The game may be paused or loading.")
                                },
                                IsError = true
                            }
                        };
                    }
                }
                else
                {
                    try
                    {
                        result = tool.Execute(args);
                    }
                    catch (Exception ex)
                    {
                        toolException = ex;
                    }
                }

                if (toolException != null)
                {
                    return new JsonRpcResponse
                    {
                        Id = requestId,
                        Result = new CallToolResult
                        {
                            Content = new List<ToolContent> { ToolContent.TextContent($"Tool execution error: {toolException.Message}\n{toolException.StackTrace}") },
                            IsError = true
                        }
                    };
                }

                return new JsonRpcResponse { Id = requestId, Result = result };
            }
            catch (Exception ex)
            {
                return new JsonRpcResponse
                {
                    Id = requestId,
                    Result = new CallToolResult
                    {
                        Content = new List<ToolContent> { ToolContent.TextContent($"Error: {ex.Message}") },
                        IsError = true
                    }
                };
            }
        }

        private JsonRpcResponse HandleListResources(JsonRpcRequest request)
        {
            var result = new ListResourcesResult
            {
                Resources = new List<ResourceInfo>
                {
                    new ResourceInfo
                    {
                        Uri = "uumc://logs/unity",
                        Name = "Unity Logs",
                        Description = "Recent log output captured from Unity",
                        MimeType = "text/plain"
                    },
                    new ResourceInfo
                    {
                        Uri = "uumc://scene/hierarchy",
                        Name = "Scene Hierarchy",
                        Description = "Current Unity scene object hierarchy",
                        MimeType = "application/json"
                    },
                    new ResourceInfo
                    {
                        Uri = "uumc://game/info",
                        Name = "Game Information",
                        Description = "Information about the running Unity game",
                        MimeType = "application/json"
                    }
                }
            };

            return new JsonRpcResponse { Id = request.Id, Result = result };
        }

        private JsonRpcResponse HandleReadResource(JsonRpcRequest request)
        {
            ReadResourceParams readParams;
            try
            {
                readParams = request.Params?.ToObject<ReadResourceParams>(JsonSerializer.Create(MCPProtocol.JsonSettings));
                if (readParams == null || string.IsNullOrWhiteSpace(readParams.Uri))
                {
                    return new JsonRpcResponse
                    {
                        Id = request.Id,
                        Error = JsonRpcError.InvalidParams("Missing resource read params or URI")
                    };
                }
            }
            catch (Exception ex)
            {
                return new JsonRpcResponse
                {
                    Id = request.Id,
                    Error = JsonRpcError.InvalidParams($"Failed to parse resource read params: {ex.Message}")
                };
            }

            var result = new ReadResourceResult();
            switch (readParams.Uri)
            {
                case "uumc://logs/unity":
                    var logs = _logProvider(200, null, null) ?? new List<string>();
                    result.Contents.Add(new ResourceContent
                    {
                        Uri = readParams.Uri,
                        MimeType = "text/plain",
                        Text = string.Join("\n", logs)
                    });
                    break;

                case "uumc://scene/hierarchy":
                case "uumc://game/info":
                    string content = null;
                    var tcs = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                    UnityMainThreadDispatcher.Enqueue(() =>
                    {
                        try
                        {
                            content = readParams.Uri == "uumc://scene/hierarchy"
                                ? UnityHelper.GetSceneHierarchyJson()
                                : UnityHelper.GetGameInfoJson();
                        }
                        finally
                        {
                            tcs.TrySetResult(true);
                        }
                    });
                    tcs.Task.Wait(TimeSpan.FromSeconds(10));

                    result.Contents.Add(new ResourceContent
                    {
                        Uri = readParams.Uri,
                        MimeType = "application/json",
                        Text = content ?? "{}"
                    });
                    break;

                default:
                    return new JsonRpcResponse
                    {
                        Id = request.Id,
                        Error = JsonRpcError.InvalidParams($"Unknown resource: {readParams.Uri}")
                    };
            }

            return new JsonRpcResponse { Id = request.Id, Result = result };
        }
    }

    internal class MCPClientHandler
    {
        private readonly Guid _clientId;
        private readonly TcpClient _client;
        private readonly MCPServer _server;
        private NetworkStream _stream;

        public MCPClientHandler(Guid clientId, TcpClient client, MCPServer server)
        {
            _clientId = clientId;
            _client = client;
            _server = server;
        }

        public async Task HandleClientAsync(CancellationToken cancellationToken)
        {
            try
            {
                _stream = _client.GetStream();
                using var reader = new StreamReader(_stream, Encoding.UTF8, true, 1024, true);

                while (!cancellationToken.IsCancellationRequested && _client.Connected)
                {
                    var line = await reader.ReadLineAsync();
                    if (line == null) break;
                    if (string.IsNullOrWhiteSpace(line)) continue;

                    try
                    {
                        var request = JsonConvert.DeserializeObject<JsonRpcRequest>(line, MCPProtocol.JsonSettings);
                        var response = _server.HandleRequest(request);
                        if (response != null)
                        {
                            await SendResponseAsync(response);
                        }
                    }
                    catch (JsonException ex)
                    {
                        await SendResponseAsync(new JsonRpcResponse
                        {
                            Id = null,
                            Error = JsonRpcError.ParseError($"Invalid JSON: {ex.Message}")
                        });
                    }
                }
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Client handler error: {ex.Message}");
            }
            finally
            {
                Disconnect();
                _server.OnClientDisconnected(_clientId);
            }
        }

        private async Task SendResponseAsync(JsonRpcResponse response)
        {
            try
            {
                var json = JsonConvert.SerializeObject(response, MCPProtocol.JsonSettings);
                var bytes = Encoding.UTF8.GetBytes(json + "\n");
                await _stream.WriteAsync(bytes, 0, bytes.Length);
                await _stream.FlushAsync();
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Failed to send response: {ex.Message}");
            }
        }

        public void Disconnect()
        {
            try
            {
                _stream?.Close();
                _client?.Close();
            }
            catch { }
        }
    }
}
