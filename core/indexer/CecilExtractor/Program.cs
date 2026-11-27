using System;
using System.IO;
using System.Linq;
using System.Text.Encodings.Web;
using System.Text.Json;

namespace Uumc.Indexer.CecilExtractor;

internal static class Program
{
    private sealed record CliOptions(
        string? AssemblyPath,
        string? GameDir,
        string? OutputPath,
        bool FilterCompilerGenerated,
        bool FilterUnityGenerated,
        bool ShowHelp);

    static int Main(string[] args)
    {
        if (!TryParseArgs(args, out var opts, out var parseError))
        {
            Console.Error.WriteLine($"[ОШИБКА] {parseError}");
            return 1;
        }

        if (opts.ShowHelp)
        {
            PrintHelp();
            return 0;
        }

        string? assemblyPath = ResolveAssemblyPath(opts.AssemblyPath, opts.GameDir);
        if (string.IsNullOrEmpty(assemblyPath) || !File.Exists(assemblyPath))
        {
            Console.Error.WriteLine(
                "[ОШИБКА] Не найдена сборка для анализа. Укажите --assembly <путь> или --game-dir <папка_игры> (либо переменную UUMC_GAME_DIR).");
            return 1;
        }

        string outputPath = Path.GetFullPath(
            string.IsNullOrWhiteSpace(opts.OutputPath) ? "symbols_raw.json" : opts.OutputPath);

        return RunExtraction(assemblyPath, outputPath, opts.FilterCompilerGenerated, opts.FilterUnityGenerated);
    }

    private static int RunExtraction(string assemblyPath, string outputPath, bool filterCompiler, bool filterUnity)
    {
        try
        {
            Console.WriteLine($"[CecilExtractor] Извлечение метаданных из: {assemblyPath}");
            var extractor = new AssemblySymbolExtractor(assemblyPath, filterCompiler, filterUnity);
            var symbols = extractor.Extract();

            Console.WriteLine("[CecilExtractor] Успешно извлечено:");
            Console.WriteLine($"  - Типов:       {symbols.Meta.TotalTypes}");
            Console.WriteLine($"  - Полей:       {symbols.Meta.TotalFields}");
            Console.WriteLine($"  - Свойств:     {symbols.Meta.TotalProperties}");
            Console.WriteLine($"  - Методов:     {symbols.Meta.TotalMethods}");

            string? outDir = Path.GetDirectoryName(outputPath);
            if (!string.IsNullOrEmpty(outDir))
            {
                Directory.CreateDirectory(outDir);
            }

            var jsonOptions = new JsonSerializerOptions
            {
                WriteIndented = true,
                Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
            };

            string json = JsonSerializer.Serialize(symbols, jsonOptions);
            File.WriteAllText(outputPath, json);

            long sizeKb = new FileInfo(outputPath).Length / 1024;
            Console.WriteLine($"[CecilExtractor] Результат сохранён в: {outputPath} ({sizeKb} КБ)");
            return 0;
        }
        catch (Exception ex) when (ex is BadImageFormatException or IOException or UnauthorizedAccessException or InvalidOperationException)
        {
            Console.Error.WriteLine($"[ОШИБКА] Не удалось извлечь метаданные из '{assemblyPath}': {ex.Message}");
            return 1;
        }
    }

    private static bool TryParseArgs(string[] args, out CliOptions options, out string error)
    {
        string? assemblyPath = null;
        string? gameDir = null;
        string? outputPath = null;
        bool filterCompiler = true;
        bool filterUnity = true;
        bool showHelp = false;

        for (int i = 0; i < args.Length; i++)
        {
            string arg = args[i];
            switch (arg)
            {
                case "-h":
                case "--help":
                    showHelp = true;
                    break;
                case "-a":
                case "--assembly":
                    if (i + 1 >= args.Length)
                    {
                        options = default!;
                        error = $"Не указано значение для параметра {arg}.";
                        return false;
                    }
                    assemblyPath = args[++i];
                    break;
                case "-g":
                case "--game-dir":
                    if (i + 1 >= args.Length)
                    {
                        options = default!;
                        error = $"Не указано значение для параметра {arg}.";
                        return false;
                    }
                    gameDir = args[++i];
                    break;
                case "-o":
                case "--output":
                    if (i + 1 >= args.Length)
                    {
                        options = default!;
                        error = $"Не указано значение для параметра {arg}.";
                        return false;
                    }
                    outputPath = args[++i];
                    break;
                case "--no-filter-compiler":
                    filterCompiler = false;
                    break;
                case "--no-filter-unity":
                    filterUnity = false;
                    break;
                default:
                    options = default!;
                    error = $"Неизвестный аргумент: {arg}. Используйте --help для справки.";
                    return false;
            }
        }

        options = new CliOptions(assemblyPath, gameDir, outputPath, filterCompiler, filterUnity, showHelp);
        error = string.Empty;
        return true;
    }

    private static string? ResolveAssemblyPath(string? explicitAssembly, string? explicitGameDir)
    {
        if (!string.IsNullOrWhiteSpace(explicitAssembly))
        {
            return Path.GetFullPath(explicitAssembly);
        }

        string? gameDir = !string.IsNullOrWhiteSpace(explicitGameDir)
            ? explicitGameDir
            : Environment.GetEnvironmentVariable("UUMC_GAME_DIR");

        if (string.IsNullOrWhiteSpace(gameDir) || !Directory.Exists(gameDir))
        {
            return null;
        }

        string fullGameDir = Path.GetFullPath(gameDir);
        string? managedDir = Directory.GetDirectories(fullGameDir, "*_Data")
            .Select(d => Path.Combine(d, "Managed"))
            .FirstOrDefault(Directory.Exists);

        return managedDir != null ? Path.Combine(managedDir, "Assembly-CSharp.dll") : null;
    }

    private static void PrintHelp()
    {
        Console.WriteLine("CecilExtractor — извлечение CLR-метаданных и IL-графа из сборки Unity");
        Console.WriteLine();
        Console.WriteLine("Использование:");
        Console.WriteLine("  dotnet run --project core/indexer/CecilExtractor -- [параметры]");
        Console.WriteLine();
        Console.WriteLine("Параметры:");
        Console.WriteLine("  -a, --assembly <путь>     Путь к целевой .dll (обычно Assembly-CSharp.dll)");
        Console.WriteLine("  -g, --game-dir <путь>     Корневая папка игры (альтернатива --assembly; или env UUMC_GAME_DIR)");
        Console.WriteLine("  -o, --output <путь>       Путь к выходному JSON (по умолчанию: ./symbols_raw.json)");
        Console.WriteLine("  --no-filter-compiler      Не фильтровать компиляторные классы (<...> / closure)");
        Console.WriteLine("  --no-filter-unity         Не фильтровать UnitySourceGenerated* классы");
        Console.WriteLine("  -h, --help                Показать справку");
    }
}
