using System;
using System.Collections.Generic;
using System.Linq;
using Mono.Cecil;

namespace Uumc.Indexer.CecilExtractor;

public static class TypeNameFormatter
{
    private static readonly Dictionary<string, string> BuiltInTypes = new(StringComparer.Ordinal)
    {
        ["System.Void"] = "void",
        ["System.Boolean"] = "bool",
        ["System.Byte"] = "byte",
        ["System.SByte"] = "sbyte",
        ["System.Char"] = "char",
        ["System.Decimal"] = "decimal",
        ["System.Double"] = "double",
        ["System.Single"] = "float",
        ["System.Int16"] = "short",
        ["System.Int32"] = "int",
        ["System.Int64"] = "long",
        ["System.UInt16"] = "ushort",
        ["System.UInt32"] = "uint",
        ["System.UInt64"] = "ulong",
        ["System.String"] = "string",
        ["System.Object"] = "object"
    };

    public static string Format(TypeReference typeRef)
    {
        if (typeRef == null) return "void";

        if (typeRef is ByReferenceType byRef)
        {
            return Format(byRef.ElementType);
        }

        if (typeRef is ArrayType arr)
        {
            string element = Format(arr.ElementType);
            string rank = new(',', arr.Rank - 1);
            return $"{element}[{rank}]";
        }

        if (typeRef is GenericInstanceType git)
        {
            if (git.ElementType.FullName == "System.Nullable`1" && git.GenericArguments.Count == 1)
            {
                return $"{Format(git.GenericArguments[0])}?";
            }

            string baseName = GetSimpleName(git.ElementType);
            string args = string.Join(",", git.GenericArguments.Select(Format));
            return $"{baseName}<{args}>";
        }

        if (BuiltInTypes.TryGetValue(typeRef.FullName, out var alias))
        {
            return alias;
        }

        if (typeRef.IsNested)
        {
            return $"{Format(typeRef.DeclaringType)}.{GetSimpleName(typeRef)}";
        }

        return GetSimpleName(typeRef);
    }

    public static string FormatParameter(ParameterDefinition param)
    {
        string typeStr = Format(param.ParameterType);

        if (param.ParameterType is ByReferenceType)
        {
            typeStr = (param.IsOut && !param.IsIn) ? $"out {typeStr}" : $"ref {typeStr}";
        }

        return typeStr;
    }

    public static string GetSimpleName(TypeReference typeRef)
    {
        string name = typeRef.Name;
        int tickIdx = name.IndexOf('`');
        return tickIdx >= 0 ? name.Substring(0, tickIdx) : name;
    }

    public static string GetTypeId(TypeDefinition typeDef)
    {
        if (typeDef.IsNested)
        {
            return $"{GetTypeId(typeDef.DeclaringType)}+{typeDef.Name}";
        }
        return string.IsNullOrEmpty(typeDef.Namespace) ? typeDef.Name : $"{typeDef.Namespace}.{typeDef.Name}";
    }
}
