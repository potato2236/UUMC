using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace Uumc.Indexer.CecilExtractor;

public class AssemblySymbols
{
    [JsonPropertyName("meta")]
    public Metadata Meta { get; set; } = new();

    [JsonPropertyName("types")]
    public Dictionary<string, TypeSymbol> Types { get; set; } = new();
}

public class Metadata
{
    [JsonPropertyName("assembly_name")]
    public string AssemblyName { get; set; } = "";

    [JsonPropertyName("assembly_version")]
    public string AssemblyVersion { get; set; } = "";

    [JsonPropertyName("extracted_at_utc")]
    public string ExtractedAtUtc { get; set; } = "";

    [JsonPropertyName("total_types")]
    public int TotalTypes { get; set; }

    [JsonPropertyName("total_fields")]
    public int TotalFields { get; set; }

    [JsonPropertyName("total_properties")]
    public int TotalProperties { get; set; }

    [JsonPropertyName("total_methods")]
    public int TotalMethods { get; set; }
}

public class TypeSymbol
{
    [JsonPropertyName("type_id")]
    public string TypeId { get; set; } = "";

    [JsonPropertyName("key")]
    public string Key { get; set; } = "";

    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("namespace")]
    public string Namespace { get; set; } = "";

    [JsonPropertyName("base_type")]
    public string? BaseType { get; set; }

    [JsonPropertyName("interfaces")]
    public List<string> Interfaces { get; set; } = new();

    [JsonPropertyName("is_enum")]
    public bool IsEnum { get; set; }

    [JsonPropertyName("is_interface")]
    public bool IsInterface { get; set; }

    [JsonPropertyName("is_value_type")]
    public bool IsValueType { get; set; }

    [JsonPropertyName("is_delegate")]
    public bool IsDelegate { get; set; }

    [JsonPropertyName("is_abstract")]
    public bool IsAbstract { get; set; }

    [JsonPropertyName("is_static")]
    public bool IsStatic { get; set; }

    [JsonPropertyName("sig_hash")]
    public string SigHash { get; set; } = "";

    [JsonPropertyName("singletons")]
    public List<string> Singletons { get; set; } = new();

    [JsonPropertyName("calls")]
    public List<string> Calls { get; set; } = new();

    [JsonPropertyName("fields")]
    public List<FieldSymbol> Fields { get; set; } = new();

    [JsonPropertyName("properties")]
    public List<PropertySymbol> Properties { get; set; } = new();

    [JsonPropertyName("methods")]
    public List<MethodSymbol> Methods { get; set; } = new();
}

public class FieldSymbol
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("key")]
    public string Key { get; set; } = "";

    [JsonPropertyName("type")]
    public string Type { get; set; } = "";

    [JsonPropertyName("is_static")]
    public bool IsStatic { get; set; }

    [JsonPropertyName("is_public")]
    public bool IsPublic { get; set; }

    [JsonPropertyName("is_literal")]
    public bool IsLiteral { get; set; }

    [JsonPropertyName("constant_value")]
    public string? ConstantValue { get; set; }

    [JsonPropertyName("sig_hash")]
    public string SigHash { get; set; } = "";
}

public class PropertySymbol
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("key")]
    public string Key { get; set; } = "";

    [JsonPropertyName("type")]
    public string Type { get; set; } = "";

    [JsonPropertyName("is_static")]
    public bool IsStatic { get; set; }

    [JsonPropertyName("has_getter")]
    public bool HasGetter { get; set; }

    [JsonPropertyName("has_setter")]
    public bool HasSetter { get; set; }

    [JsonPropertyName("sig_hash")]
    public string SigHash { get; set; } = "";
}

public class ParamSymbol
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("type")]
    public string Type { get; set; } = "";
}

public class MethodSymbol
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("key")]
    public string Key { get; set; } = "";

    [JsonPropertyName("return_type")]
    public string ReturnType { get; set; } = "";

    [JsonPropertyName("is_static")]
    public bool IsStatic { get; set; }

    [JsonPropertyName("is_public")]
    public bool IsPublic { get; set; }

    [JsonPropertyName("is_virtual")]
    public bool IsVirtual { get; set; }

    [JsonPropertyName("is_constructor")]
    public bool IsConstructor { get; set; }

    [JsonPropertyName("parameters")]
    public List<ParamSymbol> Parameters { get; set; } = new();

    [JsonPropertyName("sig_hash")]
    public string SigHash { get; set; } = "";

    [JsonPropertyName("calls")]
    public List<string> Calls { get; set; } = new();
}
