using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json.Linq;
using Uumc.DevMcp.Server;

namespace Uumc.DevMcp.Tools
{
    public class GetGameInfoToolDefinition : ToolDefinitionBase
    {
        public override string Name => "get_game_info";
        public override string Description => @"Get information about the running game including:
- Game name, developer, version
- Unity version and BepInEx version
- Platform information
- Current scene information
- Screen resolution";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>()
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            try
            {
                return TextResult(UnityHelper.GetGameInfoJson());
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to get game info: {ex.Message}");
            }
        }
    }

    public class GetTimeInfoToolDefinition : ToolDefinitionBase
    {
        public override string Name => "get_time_info";
        public override string Description => @"Get Unity time information including:
- Current time, delta time, fixed delta time
- Time scale (for slow-motion or pause detection)
- Frame count
- Real time since startup";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>()
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            try
            {
                return JsonResult(UnityHelper.GetTimeInfo());
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to get time info: {ex.Message}");
            }
        }
    }

    public class TakeScreenshotToolDefinition : ToolDefinitionBase
    {
        public override string Name => "take_screenshot";
        public override string Description => "Capture a screenshot of the current game view. Returns the image as base64-encoded PNG data.";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["saveToFile"] = new ToolPropertySchema
                    {
                        Type = "string",
                        Description = "Optional file path to save the screenshot (must resolve inside the game root directory)"
                    }
                }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var saveToFile = GetStringArg(arguments, "saveToFile");

            try
            {
                var screenshotData = UnityHelper.CaptureScreenshot();
                if (screenshotData == null || screenshotData.Length == 0)
                {
                    return ErrorResult("Failed to capture screenshot - screenshot API may not be available");
                }

                if (!string.IsNullOrEmpty(saveToFile))
                {
                    if (!TrySaveScreenshot(saveToFile, screenshotData, out var saveError))
                    {
                        return ErrorResult(saveError);
                    }
                }

                return ImageResult(screenshotData, "image/png");
            }
            catch (Exception ex)
            {
                return ErrorResult($"Failed to take screenshot: {ex.Message}");
            }
        }

        private static bool TrySaveScreenshot(string saveToFile, byte[] screenshotData, out string error)
        {
            try
            {
                var basePath = Path.GetFullPath(BepInEx.Paths.GameRootPath)
                    .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
                var candidate = Path.IsPathRooted(saveToFile)
                    ? saveToFile
                    : Path.Combine(BepInEx.Paths.GameRootPath, saveToFile);
                var targetPath = Path.GetFullPath(candidate);

                if (!targetPath.StartsWith(basePath, StringComparison.OrdinalIgnoreCase))
                {
                    error = $"Invalid saveToFile path '{saveToFile}': must stay within game root directory";
                    DevMcpPlugin.Logger?.Warning(error);
                    return false;
                }

                var dir = Path.GetDirectoryName(targetPath);
                if (!string.IsNullOrEmpty(dir))
                {
                    Directory.CreateDirectory(dir);
                }

                File.WriteAllBytes(targetPath, screenshotData);
                DevMcpPlugin.Logger?.Msg($"Screenshot saved to: {targetPath}");
                error = null;
                return true;
            }
            catch (Exception ex)
            {
                error = $"Failed to save screenshot: {ex.Message}";
                DevMcpPlugin.Logger?.Warning(error);
                return false;
            }
        }
    }
}
