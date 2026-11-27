using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Uumc.DevMcp.Server;

namespace Uumc.DevMcp.Tools
{
    public class GameKnowledge
    {
        [JsonProperty("gameName")]
        public string GameName { get; set; }

        [JsonProperty("unityVersion")]
        public string UnityVersion { get; set; }

        [JsonProperty("lastUpdated")]
        public DateTime LastUpdated { get; set; }

        [JsonProperty("pseudocodePath")]
        public string PseudocodePath { get; set; }

        [JsonProperty("discoveries")]
        public Dictionary<string, List<KnowledgeEntry>> Discoveries { get; set; } = new Dictionary<string, List<KnowledgeEntry>>();
    }

    public class KnowledgeEntry
    {
        [JsonProperty("key")]
        public string Key { get; set; }

        [JsonProperty("value")]
        public string Value { get; set; }

        [JsonProperty("description")]
        public string Description { get; set; }

        [JsonProperty("example")]
        public string Example { get; set; }

        [JsonProperty("addedAt")]
        public DateTime AddedAt { get; set; }

        [JsonProperty("tags")]
        public List<string> Tags { get; set; } = new List<string>();
    }

    public static class GameKnowledgeManager
    {
        private static readonly object _syncLock = new object();
        private static Dictionary<string, GameKnowledge> _knowledgeBase = new Dictionary<string, GameKnowledge>();
        private static string _knowledgeFilePath;
        private static bool _initialized;

        public static void Initialize()
        {
            lock (_syncLock)
            {
                if (_initialized) return;

                var userDataPath = Path.Combine(BepInEx.Paths.ConfigPath, "UumcDevMcp");
                Directory.CreateDirectory(userDataPath);
                _knowledgeFilePath = Path.Combine(userDataPath, "game_knowledge.json");

                LoadKnowledgeUnsafe();
                _initialized = true;
            }
        }

        private static void LoadKnowledgeUnsafe()
        {
            try
            {
                if (File.Exists(_knowledgeFilePath))
                {
                    var json = File.ReadAllText(_knowledgeFilePath);
                    _knowledgeBase = JsonConvert.DeserializeObject<Dictionary<string, GameKnowledge>>(json)
                        ?? new Dictionary<string, GameKnowledge>();
                    DevMcpPlugin.Logger?.Msg($"[GameKnowledge] Loaded knowledge for {_knowledgeBase.Count} games");
                }
            }
            catch (Exception ex)
            {
                DevMcpPlugin.Logger?.Error($"[GameKnowledge] Failed to load knowledge: {ex.Message}");
                _knowledgeBase = new Dictionary<string, GameKnowledge>();
            }
        }

        private static void SaveKnowledgeUnsafe()
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(_knowledgeFilePath));
                File.WriteAllText(_knowledgeFilePath, JsonConvert.SerializeObject(_knowledgeBase, Formatting.Indented));
            }
            catch (Exception ex)
            {
                DevMcpPlugin.Logger?.Error($"[GameKnowledge] Failed to save knowledge: {ex.Message}");
            }
        }

        public static string GetCurrentGameName()
        {
            try { return UnityEngine.Application.productName ?? "Unknown"; }
            catch { return "Unknown"; }
        }

        public static string GetCurrentUnityVersion()
        {
            try { return UnityEngine.Application.unityVersion ?? "Unknown"; }
            catch { return "Unknown"; }
        }

        private static GameKnowledge GetOrCreateUnsafe(string gameName)
        {
            gameName ??= GetCurrentGameName();
            if (!_knowledgeBase.TryGetValue(gameName, out var knowledge))
            {
                knowledge = new GameKnowledge
                {
                    GameName = gameName,
                    UnityVersion = GetCurrentUnityVersion(),
                    LastUpdated = DateTime.UtcNow
                };
                _knowledgeBase[gameName] = knowledge;
            }
            return knowledge;
        }

        public static GameKnowledge GetOrCreateGameKnowledge(string gameName = null)
        {
            Initialize();
            lock (_syncLock)
            {
                return GetOrCreateUnsafe(gameName);
            }
        }

        public static void AddDiscovery(string category, KnowledgeEntry entry, string gameName = null)
        {
            Initialize();
            lock (_syncLock)
            {
                var knowledge = GetOrCreateUnsafe(gameName);
                if (!knowledge.Discoveries.TryGetValue(category, out var entries))
                {
                    entries = new List<KnowledgeEntry>();
                    knowledge.Discoveries[category] = entries;
                }

                var existing = entries.FirstOrDefault(e => e.Key == entry.Key);
                if (existing != null) entries.Remove(existing);

                entry.AddedAt = DateTime.UtcNow;
                entries.Add(entry);
                knowledge.LastUpdated = DateTime.UtcNow;

                SaveKnowledgeUnsafe();
            }
            DevMcpPlugin.Logger?.Msg($"[GameKnowledge] Added discovery: {category}/{entry.Key}");
        }

        public static Dictionary<string, GameKnowledge> GetAllKnowledge()
        {
            Initialize();
            lock (_syncLock)
            {
                return new Dictionary<string, GameKnowledge>(_knowledgeBase);
            }
        }

        public static GameKnowledge GetGameKnowledge(string gameName)
        {
            Initialize();
            lock (_syncLock)
            {
                return _knowledgeBase.TryGetValue(gameName, out var knowledge) ? knowledge : null;
            }
        }

        public static string GetKnowledgeFilePath()
        {
            Initialize();
            return _knowledgeFilePath;
        }

        public static void SetPseudocodePath(string path, string gameName = null)
        {
            Initialize();
            lock (_syncLock)
            {
                var knowledge = GetOrCreateUnsafe(gameName);
                knowledge.PseudocodePath = path;
                knowledge.LastUpdated = DateTime.UtcNow;
                SaveKnowledgeUnsafe();
            }
            DevMcpPlugin.Logger?.Msg($"[GameKnowledge] Set pseudocode path: {path}");
        }

        public static string GetPseudocodePath(string gameName = null)
        {
            Initialize();
            lock (_syncLock)
            {
                return GetOrCreateUnsafe(gameName).PseudocodePath;
            }
        }
    }

    public class AddGameKnowledgeToolDefinition : ToolDefinitionBase
    {
        public override string Name => "add_game_knowledge";
        public override string Description => @"Add a discovery to the game knowledge base.
Categories include: 'console_commands', 'static_accessors', 'cheats', 'type_info', 'useful_methods', 'config', 'notes'.";
        public override bool RequiresMainThread => false;

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["category"] = new ToolPropertySchema { Type = "string", Description = "Category of the discovery" },
                    ["key"] = new ToolPropertySchema { Type = "string", Description = "Unique identifier within the category" },
                    ["value"] = new ToolPropertySchema { Type = "string", Description = "The actual value/code/command that works" },
                    ["description"] = new ToolPropertySchema { Type = "string", Description = "Human-readable description" },
                    ["example"] = new ToolPropertySchema { Type = "string", Description = "Example usage (optional)" },
                    ["tags"] = new ToolPropertySchema { Type = "array", Description = "Tags (optional)", Items = new ToolPropertySchema { Type = "string" } }
                },
                Required = new List<string> { "category", "key", "value", "description" }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var category = GetStringArg(arguments, "category");
            var key = GetStringArg(arguments, "key");
            var value = GetStringArg(arguments, "value");
            var description = GetStringArg(arguments, "description");
            var example = GetStringArg(arguments, "example", "");
            var tags = GetArg<List<string>>(arguments, "tags", new List<string>());

            if (string.IsNullOrEmpty(category) || string.IsNullOrEmpty(key) || string.IsNullOrEmpty(value))
                return ErrorResult("category, key, and value are required");

            GameKnowledgeManager.AddDiscovery(category, new KnowledgeEntry
            {
                Key = key,
                Value = value,
                Description = description,
                Example = example,
                Tags = tags ?? new List<string>()
            });

            return JsonResult(new
            {
                success = true,
                message = $"Added discovery to {category}: {key}",
                gameName = GameKnowledgeManager.GetCurrentGameName(),
                filePath = GameKnowledgeManager.GetKnowledgeFilePath()
            });
        }
    }

    public class GetGameKnowledgeToolDefinition : ToolDefinitionBase
    {
        public override string Name => "get_game_knowledge";
        public override string Description => "Query the game knowledge base by category, search term, or list all stored games.";
        public override bool RequiresMainThread => false;

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["category"] = new ToolPropertySchema { Type = "string", Description = "Specific category (optional)" },
                    ["search"] = new ToolPropertySchema { Type = "string", Description = "Search term (optional)" },
                    ["gameName"] = new ToolPropertySchema { Type = "string", Description = "Game name (optional, defaults to current game)" },
                    ["listGames"] = new ToolPropertySchema { Type = "boolean", Description = "List all games with stored knowledge" }
                }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var category = GetStringArg(arguments, "category");
            var search = GetStringArg(arguments, "search");
            var gameName = GetStringArg(arguments, "gameName");
            var listGames = GetBoolArg(arguments, "listGames", false);

            if (listGames)
            {
                var allKnowledge = GameKnowledgeManager.GetAllKnowledge();
                var gameList = allKnowledge.Select(kvp => new
                {
                    gameName = kvp.Key,
                    unityVersion = kvp.Value.UnityVersion,
                    lastUpdated = kvp.Value.LastUpdated,
                    categoryCount = kvp.Value.Discoveries.Count,
                    totalEntries = kvp.Value.Discoveries.Values.Sum(l => l.Count)
                }).ToList();

                return JsonResult(new { games = gameList, totalGames = gameList.Count, knowledgeFilePath = GameKnowledgeManager.GetKnowledgeFilePath() });
            }

            var knowledge = gameName != null
                ? GameKnowledgeManager.GetGameKnowledge(gameName)
                : GameKnowledgeManager.GetOrCreateGameKnowledge();

            if (knowledge == null)
                return JsonResult(new { found = false, message = $"No knowledge found for game: {gameName}" });

            var results = !string.IsNullOrEmpty(category)
                ? (knowledge.Discoveries.TryGetValue(category, out var entries)
                    ? new Dictionary<string, List<KnowledgeEntry>> { [category] = entries }
                    : new Dictionary<string, List<KnowledgeEntry>>())
                : knowledge.Discoveries;

            if (!string.IsNullOrEmpty(search))
            {
                var searchLower = search.ToLowerInvariant();
                results = results.ToDictionary(
                    kvp => kvp.Key,
                    kvp => kvp.Value.Where(e =>
                        (e.Key?.ToLowerInvariant().Contains(searchLower) ?? false) ||
                        (e.Value?.ToLowerInvariant().Contains(searchLower) ?? false) ||
                        (e.Description?.ToLowerInvariant().Contains(searchLower) ?? false) ||
                        (e.Tags?.Any(t => t.ToLowerInvariant().Contains(searchLower)) ?? false)
                    ).ToList()
                ).Where(kvp => kvp.Value.Count > 0).ToDictionary(kvp => kvp.Key, kvp => kvp.Value);
            }

            return JsonResult(new
            {
                gameName = knowledge.GameName,
                unityVersion = knowledge.UnityVersion,
                lastUpdated = knowledge.LastUpdated,
                discoveries = results,
                totalEntries = results.Values.Sum(l => l.Count)
            });
        }
    }

    public class GetGameSummaryToolDefinition : ToolDefinitionBase
    {
        public override string Name => "get_game_summary";
        public override string Description => "Get a quick summary of all stored knowledge for the current game.";
        public override bool RequiresMainThread => false;

        protected override ToolInputSchema GetInputSchema() =>
            new ToolInputSchema { Properties = new Dictionary<string, ToolPropertySchema>() };

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var gameName = GameKnowledgeManager.GetCurrentGameName();
            var knowledge = GameKnowledgeManager.GetOrCreateGameKnowledge();

            var quickRef = new Dictionary<string, string>();
            AddQuickRef(knowledge, "console_commands", "cmd", quickRef);
            AddQuickRef(knowledge, "static_accessors", "accessor", quickRef);
            AddQuickRef(knowledge, "cheats", "cheat", quickRef);

            return JsonResult(new Dictionary<string, object>
            {
                ["gameName"] = gameName,
                ["unityVersion"] = knowledge.UnityVersion,
                ["lastUpdated"] = knowledge.LastUpdated,
                ["pseudocodePath"] = knowledge.PseudocodePath,
                ["hasKnowledge"] = knowledge.Discoveries.Count > 0,
                ["categories"] = knowledge.Discoveries.ToDictionary(
                    kvp => kvp.Key,
                    kvp => new { count = kvp.Value.Count, keys = kvp.Value.Select(e => e.Key).ToList() }),
                ["quickReference"] = quickRef
            });
        }

        private static void AddQuickRef(GameKnowledge knowledge, string cat, string prefix, Dictionary<string, string> target)
        {
            if (knowledge.Discoveries.TryGetValue(cat, out var items))
            {
                foreach (var item in items.Take(5)) target[$"{prefix}:{item.Key}"] = item.Value;
            }
        }
    }

    public class SetPseudocodePathToolDefinition : ToolDefinitionBase
    {
        public override string Name => "set_pseudocode_path";
        public override string Description => "Set the path to optional decompiled/pseudocode source for the current game.";
        public override bool RequiresMainThread => false;

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["path"] = new ToolPropertySchema { Type = "string", Description = "Path to decompiled source directory (relative to game root or absolute)" }
                },
                Required = new List<string> { "path" }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var path = GetStringArg(arguments, "path");
            if (string.IsNullOrEmpty(path)) return ErrorResult("path is required");

            string resolvedPath = Path.IsPathRooted(path)
                ? Path.GetFullPath(path)
                : Path.GetFullPath(Path.Combine(BepInEx.Paths.GameRootPath, path));

            if (!Directory.Exists(resolvedPath) && !Path.IsPathRooted(path))
            {
                var parentDir = Directory.GetParent(BepInEx.Paths.GameRootPath)?.FullName;
                if (!string.IsNullOrEmpty(parentDir))
                {
                    var candidate = Path.GetFullPath(Path.Combine(parentDir, path));
                    if (Directory.Exists(candidate)) resolvedPath = candidate;
                }
            }

            if (!Directory.Exists(resolvedPath)) return ErrorResult($"Directory does not exist: {resolvedPath}");

            GameKnowledgeManager.SetPseudocodePath(resolvedPath);
            int csFileCount = 0;
            try { csFileCount = Directory.GetFiles(resolvedPath, "*.cs", SearchOption.AllDirectories).Length; }
            catch { }

            return JsonResult(new
            {
                success = true,
                message = $"Set pseudocode path for {GameKnowledgeManager.GetCurrentGameName()}",
                path = resolvedPath,
                csFileCount,
                knowledgeFilePath = GameKnowledgeManager.GetKnowledgeFilePath()
            });
        }
    }

    public class SearchPseudocodeToolDefinition : ToolDefinitionBase
    {
        public override string Name => "search_pseudocode";
        public override string Description => "Search the game's optional decompiled source for type definitions, methods, or patterns.";
        public override bool RequiresMainThread => false;

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["query"] = new ToolPropertySchema { Type = "string", Description = "Search query" },
                    ["filePattern"] = new ToolPropertySchema { Type = "string", Description = "File name pattern (default: '*.cs')" },
                    ["maxResults"] = new ToolPropertySchema { Type = "integer", Description = "Max results (default: 10)", Default = 10, Maximum = 50 },
                    ["contextLines"] = new ToolPropertySchema { Type = "integer", Description = "Context lines around matches (default: 3)", Default = 3, Maximum = 20 }
                },
                Required = new List<string> { "query" }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var query = GetStringArg(arguments, "query");
            var filePattern = GetStringArg(arguments, "filePattern", "*.cs");
            var maxResults = GetIntArg(arguments, "maxResults", 10);
            var contextLines = GetIntArg(arguments, "contextLines", 3);

            if (string.IsNullOrEmpty(query)) return ErrorResult("query is required");

            var pseudocodePath = GameKnowledgeManager.GetPseudocodePath();
            if (string.IsNullOrEmpty(pseudocodePath)) return ErrorResult("Pseudocode path not set. Use set_pseudocode_path first.");
            if (!Directory.Exists(pseudocodePath)) return ErrorResult($"Pseudocode directory not found: {pseudocodePath}");

            var results = new List<object>();
            try
            {
                string[] files = filePattern == "*.cs"
                    ? Directory.GetFiles(pseudocodePath, "*.cs", SearchOption.AllDirectories)
                        .Concat(Directory.GetFiles(pseudocodePath, "*.cpp", SearchOption.AllDirectories)).ToArray()
                    : Directory.GetFiles(pseudocodePath, filePattern, SearchOption.AllDirectories);

                foreach (var file in files)
                {
                    if (results.Count >= maxResults) break;
                    SearchFile(file, pseudocodePath, query, contextLines, maxResults, results);
                }
            }
            catch (Exception ex)
            {
                return ErrorResult($"Search failed: {ex.Message}");
            }

            return JsonResult(new { query, pseudocodePath, resultsCount = results.Count, results });
        }

        private static void SearchFile(string file, string baseDir, string query, int contextLines, int maxResults, List<object> results)
        {
            try
            {
                var lines = File.ReadAllLines(file);
                for (int i = 0; i < lines.Length; i++)
                {
                    if (lines[i].IndexOf(query, StringComparison.OrdinalIgnoreCase) < 0) continue;

                    int start = Math.Max(0, i - contextLines);
                    int end = Math.Min(lines.Length - 1, i + contextLines);
                    var contextList = new List<string>();
                    for (int j = start; j <= end; j++)
                    {
                        contextList.Add($"{j + 1:D4}{(j == i ? ">>> " : "    ")}{lines[j]}");
                    }

                    results.Add(new
                    {
                        file = Path.GetRelativePath(baseDir, file),
                        line = i + 1,
                        matchedLine = lines[i].Trim(),
                        context = string.Join("\n", contextList)
                    });

                    if (results.Count >= maxResults) break;
                    i += contextLines;
                }
            }
            catch { }
        }
    }

    public class ReadPseudocodeFileToolDefinition : ToolDefinitionBase
    {
        public override string Name => "read_pseudocode_file";
        public override string Description => "Read the contents of a specific file within the optional pseudocode directory.";
        public override bool RequiresMainThread => false;

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["relativePath"] = new ToolPropertySchema { Type = "string", Description = "Relative path within pseudocode directory" },
                    ["startLine"] = new ToolPropertySchema { Type = "integer", Description = "Starting line number (1-based)" },
                    ["lineCount"] = new ToolPropertySchema { Type = "integer", Description = "Number of lines to read (max: 500)", Default = 500, Maximum = 500 }
                },
                Required = new List<string> { "relativePath" }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var relativePath = GetStringArg(arguments, "relativePath");
            var startLine = GetIntArg(arguments, "startLine", 1);
            var lineCount = GetIntArg(arguments, "lineCount", 500);

            if (string.IsNullOrEmpty(relativePath)) return ErrorResult("relativePath is required");

            var pseudocodePath = GameKnowledgeManager.GetPseudocodePath();
            if (string.IsNullOrEmpty(pseudocodePath)) return ErrorResult("Pseudocode path not set. Use set_pseudocode_path first.");

            var normalizedBase = Path.GetFullPath(pseudocodePath).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
            var normalizedTarget = Path.GetFullPath(Path.Combine(pseudocodePath, relativePath));
            if (!normalizedTarget.StartsWith(normalizedBase, StringComparison.OrdinalIgnoreCase))
                return ErrorResult("Invalid path - must be within pseudocode directory");

            if (!File.Exists(normalizedTarget)) return ErrorResult($"File not found: {relativePath}");

            try
            {
                var allLines = File.ReadAllLines(normalizedTarget);
                int clampedCount = Math.Max(1, Math.Min(500, lineCount));
                int start = Math.Max(0, startLine - 1);
                int count = start >= allLines.Length ? 0 : Math.Min(clampedCount, allLines.Length - start);

                var numberedLines = new List<string>();
                for (int i = 0; i < count; i++)
                {
                    numberedLines.Add($"{start + i + 1,5}: {allLines[start + i]}");
                }

                return JsonResult(new
                {
                    file = relativePath,
                    totalLines = allLines.Length,
                    startLine = start + 1,
                    endLine = count > 0 ? start + count : start,
                    content = string.Join("\n", numberedLines)
                });
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to read file: {ex.Message}");
            }
        }
    }
}
