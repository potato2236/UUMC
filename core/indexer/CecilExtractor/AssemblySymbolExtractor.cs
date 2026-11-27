using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Mono.Cecil;
using Mono.Cecil.Cil;

namespace Uumc.Indexer.CecilExtractor;

public class AssemblySymbolExtractor
{
    private readonly string _assemblyPath;
    private readonly bool _filterCompilerGenerated;
    private readonly bool _filterUnityGenerated;

    public AssemblySymbolExtractor(string assemblyPath, bool filterCompilerGenerated = true, bool filterUnityGenerated = true)
    {
        _assemblyPath = assemblyPath;
        _filterCompilerGenerated = filterCompilerGenerated;
        _filterUnityGenerated = filterUnityGenerated;
    }

    public AssemblySymbols Extract()
    {
        var readerParams = new ReaderParameters();
        using var resolver = new DefaultAssemblyResolver();
        string managedDir = Path.GetDirectoryName(Path.GetFullPath(_assemblyPath))!;
        resolver.AddSearchDirectory(managedDir);
        readerParams.AssemblyResolver = resolver;

        using var assembly = AssemblyDefinition.ReadAssembly(_assemblyPath, readerParams);

        var symbols = new AssemblySymbols();
        symbols.Meta.AssemblyName = assembly.Name.Name;
        symbols.Meta.AssemblyVersion = assembly.Name.Version.ToString();
        symbols.Meta.ExtractedAtUtc = DateTime.UtcNow.ToString("o");

        var allTypes = new List<TypeDefinition>();
        void CollectTypes(TypeDefinition td)
        {
            if (ShouldSkipType(td)) return;
            allTypes.Add(td);
            foreach (var nested in td.NestedTypes)
            {
                CollectTypes(nested);
            }
        }

        foreach (var t in assembly.MainModule.Types)
        {
            CollectTypes(t);
        }

        allTypes.Sort((a, b) => string.Compare(TypeNameFormatter.GetTypeId(a), TypeNameFormatter.GetTypeId(b), StringComparison.Ordinal));

        int totalFields = 0;
        int totalProperties = 0;
        int totalMethods = 0;

        foreach (var td in allTypes)
        {
            var typeSymbol = ExtractType(td, assembly.MainModule);
            symbols.Types[typeSymbol.TypeId] = typeSymbol;

            totalFields += typeSymbol.Fields.Count;
            totalProperties += typeSymbol.Properties.Count;
            totalMethods += typeSymbol.Methods.Count;
        }

        symbols.Meta.TotalTypes = symbols.Types.Count;
        symbols.Meta.TotalFields = totalFields;
        symbols.Meta.TotalProperties = totalProperties;
        symbols.Meta.TotalMethods = totalMethods;

        return symbols;
    }

    private bool ShouldSkipType(TypeDefinition td)
    {
        if (td.Name == "<Module>") return true;

        if (_filterCompilerGenerated)
        {
            if (td.Name.StartsWith("<", StringComparison.Ordinal) ||
                td.Name.Contains("CompilerGenerated", StringComparison.Ordinal) ||
                td.Name.Contains("<>", StringComparison.Ordinal))
            {
                return true;
            }
        }

        if (_filterUnityGenerated && td.Name.StartsWith("UnitySourceGenerated", StringComparison.Ordinal))
        {
            return true;
        }

        return false;
    }

    private TypeSymbol ExtractType(TypeDefinition td, ModuleDefinition mainModule)
    {
        string typeId = TypeNameFormatter.GetTypeId(td);
        string key = $"T:{typeId}";

        var symbol = new TypeSymbol
        {
            TypeId = typeId,
            Key = key,
            Name = td.Name,
            Namespace = td.Namespace,
            BaseType = td.BaseType != null ? TypeNameFormatter.Format(td.BaseType) : null,
            IsEnum = td.IsEnum,
            IsInterface = td.IsInterface,
            IsValueType = td.IsValueType && !td.IsEnum,
            IsDelegate = td.BaseType?.FullName == "System.MulticastDelegate",
            IsAbstract = td.IsAbstract && !td.IsInterface,
            IsStatic = td.IsAbstract && td.IsSealed,
            SigHash = SigHash.Compute(key)
        };

        if (td.HasInterfaces)
        {
            symbol.Interfaces = td.Interfaces
                .Select(i => TypeNameFormatter.Format(i.InterfaceType))
                .OrderBy(s => s, StringComparer.Ordinal)
                .ToList();
        }

        var classSingletons = new HashSet<string>(StringComparer.Ordinal);
        var classCalls = new HashSet<string>(StringComparer.Ordinal);

        ExtractFields(td, typeId, symbol);
        ExtractProperties(td, typeId, symbol);
        ExtractMethods(td, typeId, mainModule, symbol, classSingletons, classCalls);

        symbol.Singletons = classSingletons.OrderBy(s => s, StringComparer.Ordinal).ToList();
        symbol.Calls = classCalls.OrderBy(c => c, StringComparer.Ordinal).ToList();

        return symbol;
    }

    private static void ExtractFields(TypeDefinition td, string typeId, TypeSymbol symbol)
    {
        if (!td.HasFields) return;

        foreach (var f in td.Fields)
        {
            if (f.Name.StartsWith("<", StringComparison.Ordinal) || (td.IsEnum && f.Name == "value__"))
                continue;

            string fKey = $"F:{typeId}.{f.Name}";
            symbol.Fields.Add(new FieldSymbol
            {
                Name = f.Name,
                Key = fKey,
                Type = TypeNameFormatter.Format(f.FieldType),
                IsStatic = f.IsStatic,
                IsPublic = f.IsPublic,
                IsLiteral = f.IsLiteral,
                ConstantValue = f.HasConstant ? f.Constant?.ToString() : null,
                SigHash = SigHash.Compute(fKey)
            });
        }
        symbol.Fields.Sort((a, b) => string.Compare(a.Name, b.Name, StringComparison.Ordinal));
    }

    private static void ExtractProperties(TypeDefinition td, string typeId, TypeSymbol symbol)
    {
        if (!td.HasProperties) return;

        foreach (var p in td.Properties)
        {
            if (p.Name.StartsWith("<", StringComparison.Ordinal)) continue;

            string pKey = $"P:{typeId}.{p.Name}";
            symbol.Properties.Add(new PropertySymbol
            {
                Name = p.Name,
                Key = pKey,
                Type = TypeNameFormatter.Format(p.PropertyType),
                IsStatic = (p.GetMethod ?? p.SetMethod)?.IsStatic ?? false,
                HasGetter = p.GetMethod != null,
                HasSetter = p.SetMethod != null,
                SigHash = SigHash.Compute(pKey)
            });
        }
        symbol.Properties.Sort((a, b) => string.Compare(a.Name, b.Name, StringComparison.Ordinal));
    }

    private static void ExtractMethods(
        TypeDefinition td,
        string typeId,
        ModuleDefinition mainModule,
        TypeSymbol symbol,
        HashSet<string> classSingletons,
        HashSet<string> classCalls)
    {
        if (!td.HasMethods) return;

        string mainAsmName = mainModule.Assembly.Name.Name;

        foreach (var m in td.Methods)
        {
            if (m.Name.StartsWith("<", StringComparison.Ordinal)) continue;

            string methodName = m.Name switch
            {
                ".ctor" => "#ctor",
                ".cctor" => "#cctor",
                _ => m.Name
            };
            bool isCtor = m.Name == ".ctor" || m.Name == ".cctor";

            var paramSymbols = new List<ParamSymbol>();
            var paramTypeStrings = new List<string>();
            if (m.HasParameters)
            {
                foreach (var p in m.Parameters)
                {
                    string pType = TypeNameFormatter.FormatParameter(p);
                    paramSymbols.Add(new ParamSymbol { Name = p.Name, Type = pType });
                    paramTypeStrings.Add(pType);
                }
            }

            string mKey = $"M:{typeId}.{methodName}({string.Join(",", paramTypeStrings)})";
            var ms = new MethodSymbol
            {
                Name = methodName,
                Key = mKey,
                ReturnType = TypeNameFormatter.Format(m.ReturnType),
                IsStatic = m.IsStatic,
                IsPublic = m.IsPublic,
                IsVirtual = m.IsVirtual,
                IsConstructor = isCtor,
                Parameters = paramSymbols,
                SigHash = SigHash.Compute(mKey)
            };

            if (m.HasBody)
            {
                AnalyzeMethodIl(m, td.Name, mainModule.Name, mainAsmName, ms, classSingletons, classCalls);
            }

            symbol.Methods.Add(ms);
        }

        symbol.Methods.Sort((a, b) => string.Compare(a.Key, b.Key, StringComparison.Ordinal));
    }

    private static void AnalyzeMethodIl(
        MethodDefinition m,
        string ownerName,
        string mainModuleName,
        string mainAsmName,
        MethodSymbol ms,
        HashSet<string> classSingletons,
        HashSet<string> classCalls)
    {
        var methodCalls = new HashSet<string>(StringComparer.Ordinal);

        foreach (var instr in m.Body.Instructions)
        {
            if ((instr.OpCode == OpCodes.Call || instr.OpCode == OpCodes.Callvirt || instr.OpCode == OpCodes.Newobj) &&
                instr.Operand is MethodReference mr)
            {
                string targetType = mr.DeclaringType.Name;
                string scopeName = mr.DeclaringType.Scope?.Name ?? string.Empty;
                bool isGameAssembly = scopeName == mainAsmName ||
                                      scopeName == mainModuleName ||
                                      scopeName == "Assembly-CSharp" ||
                                      scopeName == "Assembly-CSharp.dll";

                if (isGameAssembly && !targetType.StartsWith("<", StringComparison.Ordinal) && targetType != ownerName)
                {
                    string callSig = $"{targetType}.{mr.Name}";
                    methodCalls.Add(callSig);
                    classCalls.Add(callSig);
                }
            }
            else if ((instr.OpCode == OpCodes.Ldsfld || instr.OpCode == OpCodes.Stsfld) &&
                     instr.Operand is FieldReference fr)
            {
                if (fr.Name.Equals("main", StringComparison.OrdinalIgnoreCase) ||
                    fr.Name.Equals("instance", StringComparison.OrdinalIgnoreCase) ||
                    fr.Name.Equals("singleton", StringComparison.OrdinalIgnoreCase))
                {
                    classSingletons.Add($"{fr.DeclaringType.Name}.{fr.Name}");
                }
            }
        }

        ms.Calls = methodCalls.OrderBy(c => c, StringComparer.Ordinal).ToList();
    }
}
