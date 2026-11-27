using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using Uumc.DevMcp.Server;

namespace Uumc.DevMcp.Tools
{
    public class ExecuteCSharpToolDefinition : ToolDefinitionBase
    {
        public override string Name => "execute_csharp";
        public override string Description => @"Evaluate reflection-based C# member chains, property assignments, and method calls at runtime in the Unity game context.
Has access to all Unity namespaces and loaded game/mod assemblies.

Supported syntax (reflection evaluator, NOT a Roslyn compiler):
- Member/property/method chains: return UnityEngine.SceneManagement.SceneManager.GetActiveScene().name;
- Property/field assignment: UnityEngine.Time.timeScale = 1.0f
- Variables from member calls: var go = UnityEngine.GameObject.Find(""Player""); return go.transform.position;
- Generic static methods: UnityEngine.Object.FindObjectsOfType<UnityEngine.Camera>()

Limitations: no arithmetic/boolean operators (+, &&, ?:), no lambdas/LINQ delegates, and no bare literal assignments (e.g. var x = 10;).";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["code"] = new ToolPropertySchema { Type = "string", Description = "C# code to execute. Should return a value or perform an action." },
                    ["timeout"] = new ToolPropertySchema { Type = "integer", Description = "Execution timeout in milliseconds (default: 5000, max: 30000)", Default = 5000, Minimum = 100, Maximum = 30000 }
                },
                Required = new List<string> { "code" }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var code = GetStringArg(arguments, "code");
            var timeout = GetIntArg(arguments, "timeout", 5000);
            if (string.IsNullOrWhiteSpace(code)) return ErrorResult("Code parameter is required");

            try
            {
                var result = CSharpEvaluator.Execute(code, timeout);
                return TextResult(result ?? "Execution completed (null result)");
            }
            catch (Exception ex)
            {
                return ErrorResult($"Execution failed: {ex.Message}\n{ex.StackTrace}");
            }
        }
    }

    public class EvaluateExpressionToolDefinition : ToolDefinitionBase
    {
        public override string Name => "evaluate_expression";
        public override string Description => @"Evaluate a simple C# expression (property access, method call, or assignment) and return the result.

Examples:
- ""UnityEngine.Time.time""
- ""UnityEngine.Application.productName""
- ""UnityEngine.QualitySettings.GetQualityLevel()""";

        protected override ToolInputSchema GetInputSchema()
        {
            return new ToolInputSchema
            {
                Properties = new Dictionary<string, ToolPropertySchema>
                {
                    ["expression"] = new ToolPropertySchema { Type = "string", Description = "C# expression to evaluate" }
                },
                Required = new List<string> { "expression" }
            };
        }

        public override CallToolResult Execute(Dictionary<string, JToken> arguments)
        {
            var expression = GetStringArg(arguments, "expression");
            if (string.IsNullOrWhiteSpace(expression)) return ErrorResult("Expression parameter is required");

            try
            {
                var result = CSharpEvaluator.EvaluateExpression(expression);
                return TextResult(result?.ToString() ?? "null");
            }
            catch (Exception ex)
            {
                return ErrorResult($"Evaluation failed: {ex.Message}");
            }
        }
    }

    public static class CSharpEvaluator
    {
        private static readonly Dictionary<string, Type> _typeCache = new Dictionary<string, Type>(StringComparer.OrdinalIgnoreCase);
        private static readonly object _lock = new object();

        private static readonly Dictionary<string, string> TypeAliases = new Dictionary<string, string>
        {
            ["string"] = "System.String",
            ["int"] = "System.Int32",
            ["long"] = "System.Int64",
            ["float"] = "System.Single",
            ["double"] = "System.Double",
            ["bool"] = "System.Boolean",
            ["object"] = "System.Object",
            ["void"] = "System.Void",
            ["byte"] = "System.Byte",
            ["sbyte"] = "System.SByte",
            ["short"] = "System.Int16",
            ["ushort"] = "System.UInt16",
            ["uint"] = "System.UInt32",
            ["ulong"] = "System.UInt64",
            ["char"] = "System.Char",
            ["decimal"] = "System.Decimal"
        };

        private static readonly Regex GenericMethodPattern = new Regex(@"^(\w+)<(.+)>\s*\((.*)\)$", RegexOptions.Compiled);
        private static readonly Regex VariableDeclarationPattern = new Regex(@"^\s*(var|(?:[\w\.]+(?:<[\w\.,\s]+>)?(?:\[\])?(?:\?)?))?\s+(\w+)\s*=\s*(.+)$", RegexOptions.Compiled);

        static CSharpEvaluator()
        {
            CacheAssemblyTypes();
        }

        private static void CacheAssemblyTypes()
        {
            try
            {
                foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    if (asm.IsDynamic) continue;
                    try
                    {
                        foreach (var type in asm.GetExportedTypes())
                        {
                            _typeCache[type.FullName] = type;
                            if (type.Namespace != null &&
                                (type.Namespace.StartsWith("UnityEngine") || type.Namespace.StartsWith("System")) &&
                                !_typeCache.ContainsKey(type.Name))
                            {
                                _typeCache[type.Name] = type;
                            }
                        }
                    }
                    catch { }
                }
            }
            catch (Exception ex)
            {
                DevMcpLog.Warning($"Failed to cache assembly types: {ex.Message}");
            }
        }

        public static void RefreshTypeCache()
        {
            _typeCache.Clear();
            CacheAssemblyTypes();
        }

        public static Type ResolveType(string typeName)
        {
            if (string.IsNullOrEmpty(typeName)) return null;
            typeName = typeName.Trim();

            if (TypeAliases.TryGetValue(typeName, out var aliasedType)) typeName = aliasedType;
            if (_typeCache.TryGetValue(typeName, out var cachedType)) return cachedType;

            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (asm.IsDynamic) continue;
                try
                {
                    var type = asm.GetType(typeName, false);
                    if (type != null)
                    {
                        _typeCache[typeName] = type;
                        return type;
                    }
                }
                catch { }
            }

            string[] commonNamespaces =
            {
                "UnityEngine", "UnityEngine.SceneManagement", "UnityEngine.UI", "UnityEngine.Events",
                "System", "System.Linq", "System.Collections.Generic"
            };
            foreach (var ns in commonNamespaces)
            {
                var fullName = $"{ns}.{typeName}";
                foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    if (asm.IsDynamic) continue;
                    try
                    {
                        var type = asm.GetType(fullName, false);
                        if (type != null)
                        {
                            _typeCache[typeName] = type;
                            return type;
                        }
                    }
                    catch { }
                }
            }

            return null;
        }

        public static object EvaluateExpression(string expression)
        {
            expression = expression.Trim();

            var assignmentIndex = FindAssignmentOperator(expression);
            if (assignmentIndex > 0)
            {
                var leftSide = expression.Substring(0, assignmentIndex).Trim();
                var rightSide = expression.Substring(assignmentIndex + 1).Trim();
                return EvaluateAssignment(leftSide, rightSide);
            }

            var parts = ParseExpression(expression);
            if (parts.Count == 0) throw new ArgumentException("Empty expression");
            return EvaluateExpressionParts(parts);
        }

        private static int FindAssignmentOperator(string expression)
        {
            int depth = 0;
            bool inString = false;
            char stringChar = '\0';

            for (int i = 0; i < expression.Length; i++)
            {
                char c = expression[i];
                if (!inString && (c == '"' || c == '\''))
                {
                    inString = true;
                    stringChar = c;
                }
                else if (inString && c == stringChar && (i == 0 || expression[i - 1] != '\\'))
                {
                    inString = false;
                }
                else if (!inString)
                {
                    if (c == '(' || c == '[' || c == '<' || c == '{') depth++;
                    else if (c == ')' || c == ']' || c == '>' || c == '}') depth--;
                    else if (depth == 0 && c == '=' && i > 0 && i < expression.Length - 1)
                    {
                        char prev = expression[i - 1];
                        char next = expression[i + 1];
                        if (prev != '=' && prev != '!' && prev != '<' && prev != '>' && next != '=')
                            return i;
                    }
                }
            }
            return -1;
        }

        private static object EvaluateAssignment(string target, string valueExpr)
        {
            var value = ParseLiteralOrExpression(valueExpr);
            var parts = ParseExpression(target);
            if (parts.Count < 2) throw new ArgumentException("Invalid assignment target");

            var propertyName = parts[parts.Count - 1];
            var targetParts = parts.Take(parts.Count - 1).ToList();

            object targetObj = null;
            Type targetType = null;

            for (int typeEndIndex = targetParts.Count - 1; typeEndIndex >= 0; typeEndIndex--)
            {
                targetType = ResolveType(string.Join(".", targetParts.Take(typeEndIndex + 1)));
                if (targetType != null)
                {
                    for (int i = typeEndIndex + 1; i < targetParts.Count; i++)
                    {
                        var result = EvaluateMember(targetObj, targetType, targetParts[i]);
                        targetObj = result.Value;
                        targetType = result.Type ?? targetObj?.GetType();
                    }
                    break;
                }
            }

            if (targetType == null) throw new ArgumentException($"Could not resolve target type: {string.Join(".", targetParts)}");

            var flags = BindingFlags.Public | BindingFlags.NonPublic | (targetObj == null ? BindingFlags.Static : BindingFlags.Instance);
            var prop = targetType.GetProperty(propertyName, flags);
            if (prop != null && prop.CanWrite)
            {
                var convertedValue = ConvertArgument(value, prop.PropertyType);
                prop.SetValue(targetObj, convertedValue);
                return $"Set {target} = {convertedValue}";
            }

            var field = targetType.GetField(propertyName, flags);
            if (field != null && !field.IsInitOnly)
            {
                var convertedValue = ConvertArgument(value, field.FieldType);
                field.SetValue(targetObj, convertedValue);
                return $"Set {target} = {convertedValue}";
            }

            throw new MemberAccessException($"Property '{propertyName}' not found or not writable on type '{targetType.FullName}'");
        }

        private static List<string> ParseExpression(string expression)
        {
            var parts = new List<string>();
            var current = new StringBuilder();
            int parenDepth = 0, angleDepth = 0, bracketDepth = 0;
            bool inString = false;
            char stringChar = '\0';

            for (int i = 0; i < expression.Length; i++)
            {
                char c = expression[i];
                if (!inString && (c == '"' || c == '\''))
                {
                    inString = true;
                    stringChar = c;
                    current.Append(c);
                }
                else if (inString && c == stringChar && (i == 0 || expression[i - 1] != '\\'))
                {
                    inString = false;
                    current.Append(c);
                }
                else if (inString)
                {
                    current.Append(c);
                }
                else
                {
                    if (c == '(') parenDepth++;
                    else if (c == ')') parenDepth--;
                    else if (c == '<') angleDepth++;
                    else if (c == '>') angleDepth--;
                    else if (c == '[') bracketDepth++;
                    else if (c == ']') bracketDepth--;

                    if (c == '.' && parenDepth == 0 && angleDepth == 0 && bracketDepth == 0)
                    {
                        if (current.Length > 0)
                        {
                            parts.Add(current.ToString());
                            current.Clear();
                        }
                    }
                    else
                    {
                        current.Append(c);
                    }
                }
            }

            if (current.Length > 0) parts.Add(current.ToString());
            return parts;
        }

        private static object EvaluateExpressionParts(List<string> parts)
        {
            object currentObj = null;
            for (int typeEndIndex = parts.Count - 1; typeEndIndex >= 0; typeEndIndex--)
            {
                var currentType = ResolveType(string.Join(".", parts.Take(typeEndIndex + 1)));
                if (currentType != null)
                {
                    for (int i = typeEndIndex + 1; i < parts.Count; i++)
                    {
                        var result = EvaluateMember(currentObj, currentType, parts[i]);
                        currentObj = result.Value;
                        currentType = result.Type ?? currentObj?.GetType();
                    }
                    return currentObj;
                }
            }

            throw new ArgumentException($"Could not resolve type from expression: {string.Join(".", parts)}");
        }

        private static (object Value, Type Type) EvaluateMember(object obj, Type type, string memberExpression)
        {
            var genericMatch = GenericMethodPattern.Match(memberExpression);
            if (genericMatch.Success)
            {
                return InvokeGenericMethod(obj, type, genericMatch.Groups[1].Value, genericMatch.Groups[2].Value, genericMatch.Groups[3].Value);
            }

            int parenIndex = memberExpression.IndexOf('(');
            if (parenIndex > 0)
            {
                var methodName = memberExpression.Substring(0, parenIndex);
                var argsStr = memberExpression.Substring(parenIndex + 1, memberExpression.Length - parenIndex - 2);
                return InvokeMethod(obj, type, methodName, ParseArguments(argsStr));
            }

            int bracketIndex = memberExpression.IndexOf('[');
            if (bracketIndex > 0)
            {
                var propName = memberExpression.Substring(0, bracketIndex);
                var indexStr = memberExpression.Substring(bracketIndex + 1, memberExpression.Length - bracketIndex - 2);
                var propResult = GetProperty(obj, type, propName);
                return ApplyIndexer(propResult.Value, propResult.Type, indexStr);
            }

            return GetProperty(obj, type, memberExpression);
        }

        private static (object Value, Type Type) InvokeGenericMethod(object obj, Type type, string methodName, string typeArgsStr, string argsStr)
        {
            var typeArgs = new List<Type>();
            foreach (var typeArgName in ParseTypeArguments(typeArgsStr))
            {
                var typeArg = ResolveType(typeArgName.Trim());
                if (typeArg == null) throw new ArgumentException($"Could not resolve generic type argument: {typeArgName}");
                typeArgs.Add(typeArg);
            }

            var args = ParseArguments(argsStr);
            var flags = BindingFlags.Public | BindingFlags.NonPublic | (obj == null ? BindingFlags.Static : BindingFlags.Instance);
            var methods = type.GetMethods(flags)
                .Where(m => m.Name == methodName && m.IsGenericMethodDefinition && m.GetGenericArguments().Length == typeArgs.Count)
                .ToList();

            foreach (var genericMethod in methods)
            {
                try
                {
                    var method = genericMethod.MakeGenericMethod(typeArgs.ToArray());
                    var parameters = method.GetParameters();
                    if (parameters.Length != args.Length && !parameters.All(p => p.HasDefaultValue || args.Length >= Array.IndexOf(parameters, p) + 1))
                        continue;

                    var convertedArgs = new object[parameters.Length];
                    bool match = true;
                    for (int i = 0; i < parameters.Length && match; i++)
                    {
                        if (i < args.Length) convertedArgs[i] = ConvertArgument(args[i], parameters[i].ParameterType);
                        else if (parameters[i].HasDefaultValue) convertedArgs[i] = parameters[i].DefaultValue;
                        else match = false;
                    }

                    if (match) return (method.Invoke(obj, convertedArgs), method.ReturnType);
                }
                catch { }
            }

            throw new MemberAccessException($"Generic method '{methodName}<{typeArgsStr}>' not found or arguments don't match on type '{type.FullName}'");
        }

        private static List<string> ParseTypeArguments(string typeArgsStr)
        {
            var result = new List<string>();
            var current = new StringBuilder();
            int depth = 0;

            foreach (char c in typeArgsStr)
            {
                if (c == '<') depth++;
                else if (c == '>') depth--;

                if (c == ',' && depth == 0)
                {
                    result.Add(current.ToString().Trim());
                    current.Clear();
                }
                else
                {
                    current.Append(c);
                }
            }

            if (current.Length > 0) result.Add(current.ToString().Trim());
            return result;
        }

        private static (object Value, Type Type) GetProperty(object obj, Type type, string name)
        {
            var flags = BindingFlags.Public | BindingFlags.NonPublic | (obj == null ? BindingFlags.Static : BindingFlags.Instance);
            var prop = type.GetProperty(name, flags);
            if (prop != null) return (prop.GetValue(obj), prop.PropertyType);

            var field = type.GetField(name, flags);
            if (field != null) return (field.GetValue(obj), field.FieldType);

            throw new MemberAccessException($"Member '{name}' not found on type '{type.FullName}'");
        }

        private static (object Value, Type Type) InvokeMethod(object obj, Type type, string methodName, object[] args)
        {
            var flags = BindingFlags.Public | BindingFlags.NonPublic | (obj == null ? BindingFlags.Static : BindingFlags.Instance);
            var methods = type.GetMethods(flags).Where(m => m.Name == methodName && !m.IsGenericMethodDefinition).ToList();

            foreach (var method in methods)
            {
                var parameters = method.GetParameters();
                if (parameters.Length != args.Length && !(parameters.Length > 0 && parameters.Last().IsDefined(typeof(ParamArrayAttribute))))
                    continue;

                try
                {
                    var convertedArgs = new object[parameters.Length];
                    bool match = true;
                    for (int i = 0; i < parameters.Length && match; i++)
                    {
                        if (i < args.Length) convertedArgs[i] = ConvertArgument(args[i], parameters[i].ParameterType);
                        else if (parameters[i].HasDefaultValue) convertedArgs[i] = parameters[i].DefaultValue;
                        else match = false;
                    }

                    if (match) return (method.Invoke(obj, convertedArgs), method.ReturnType);
                }
                catch { }
            }

            if (args.Length == 0)
            {
                var method = methods.FirstOrDefault(m => m.GetParameters().Length == 0);
                if (method != null) return (method.Invoke(obj, null), method.ReturnType);
            }

            throw new MemberAccessException($"Method '{methodName}' not found or arguments don't match on type '{type.FullName}'");
        }

        private static (object Value, Type Type) ApplyIndexer(object obj, Type type, string indexStr)
        {
            if (obj == null) throw new NullReferenceException("Cannot apply indexer to null");
            var index = ParseLiteralOrExpression(indexStr.Trim());

            if (obj is Array array)
            {
                return (array.GetValue(Convert.ToInt32(index)), type.GetElementType());
            }

            var indexer = type.GetProperty("Item");
            if (indexer != null)
            {
                var indexParams = indexer.GetIndexParameters();
                if (indexParams.Length > 0)
                {
                    return (indexer.GetValue(obj, new[] { ConvertArgument(index, indexParams[0].ParameterType) }), indexer.PropertyType);
                }
            }

            var itemGetter = type.GetMethod("get_Item");
            if (itemGetter != null)
            {
                var parameters = itemGetter.GetParameters();
                if (parameters.Length > 0)
                {
                    return (itemGetter.Invoke(obj, new[] { ConvertArgument(index, parameters[0].ParameterType) }), itemGetter.ReturnType);
                }
            }

            throw new InvalidOperationException($"Type '{type.FullName}' does not have an indexer");
        }

        private static object[] ParseArguments(string argsStr)
        {
            if (string.IsNullOrWhiteSpace(argsStr)) return Array.Empty<object>();

            var args = new List<object>();
            var current = new StringBuilder();
            int depth = 0;
            bool inString = false;
            char stringChar = '\0';

            for (int i = 0; i < argsStr.Length; i++)
            {
                char c = argsStr[i];
                if (!inString && (c == '"' || c == '\'')) { inString = true; stringChar = c; current.Append(c); }
                else if (inString && c == stringChar && (i == 0 || argsStr[i - 1] != '\\')) { inString = false; current.Append(c); }
                else if (!inString && (c == '(' || c == '[' || c == '<' || c == '{')) { depth++; current.Append(c); }
                else if (!inString && (c == ')' || c == ']' || c == '>' || c == '}')) { depth--; current.Append(c); }
                else if (!inString && c == ',' && depth == 0)
                {
                    args.Add(ParseLiteralOrExpression(current.ToString().Trim()));
                    current.Clear();
                }
                else current.Append(c);
            }

            if (current.Length > 0) args.Add(ParseLiteralOrExpression(current.ToString().Trim()));
            return args.ToArray();
        }

        private static object ParseLiteralOrExpression(string str)
        {
            if (string.IsNullOrEmpty(str)) return null;
            str = str.Trim();

            if ((str.StartsWith("\"") && str.EndsWith("\"")) || (str.StartsWith("'") && str.EndsWith("'")))
                return UnescapeString(str.Substring(1, str.Length - 2));

            if (str.Equals("true", StringComparison.OrdinalIgnoreCase)) return true;
            if (str.Equals("false", StringComparison.OrdinalIgnoreCase)) return false;
            if (str.Equals("null", StringComparison.OrdinalIgnoreCase)) return null;

            if (int.TryParse(str, NumberStyles.Integer, CultureInfo.InvariantCulture, out int intVal)) return intVal;
            if (long.TryParse(str, NumberStyles.Integer, CultureInfo.InvariantCulture, out long longVal)) return longVal;
            if (str.EndsWith("f", StringComparison.OrdinalIgnoreCase) && float.TryParse(str.Substring(0, str.Length - 1), NumberStyles.Float, CultureInfo.InvariantCulture, out float floatVal)) return floatVal;
            if (str.EndsWith("d", StringComparison.OrdinalIgnoreCase) && double.TryParse(str.Substring(0, str.Length - 1), NumberStyles.Float, CultureInfo.InvariantCulture, out double dVal)) return dVal;
            if (str.EndsWith("m", StringComparison.OrdinalIgnoreCase) && decimal.TryParse(str.Substring(0, str.Length - 1), NumberStyles.Float, CultureInfo.InvariantCulture, out decimal decVal)) return decVal;
            if (double.TryParse(str, NumberStyles.Float, CultureInfo.InvariantCulture, out double doubleVal)) return doubleVal;

            if (str.StartsWith("typeof(") && str.EndsWith(")"))
            {
                var type = ResolveType(str.Substring(7, str.Length - 8));
                if (type != null) return type;
            }

            try { return EvaluateExpression(str); }
            catch { return str; }
        }

        private static string UnescapeString(string str) =>
            str.Replace("\\n", "\n").Replace("\\r", "\r").Replace("\\t", "\t").Replace("\\\\", "\\").Replace("\\\"", "\"").Replace("\\'", "'");

        private static object ConvertArgument(object arg, Type targetType)
        {
            if (arg == null) return targetType.IsValueType ? Activator.CreateInstance(targetType) : null;

            var argType = arg.GetType();
            if (targetType.IsAssignableFrom(argType)) return arg;
            if (targetType == typeof(Type) && arg is Type) return arg;
            if (targetType == typeof(string)) return arg.ToString();
            if (targetType == typeof(int)) return Convert.ToInt32(arg, CultureInfo.InvariantCulture);
            if (targetType == typeof(long)) return Convert.ToInt64(arg, CultureInfo.InvariantCulture);
            if (targetType == typeof(float)) return Convert.ToSingle(arg, CultureInfo.InvariantCulture);
            if (targetType == typeof(double)) return Convert.ToDouble(arg, CultureInfo.InvariantCulture);
            if (targetType == typeof(bool)) return Convert.ToBoolean(arg, CultureInfo.InvariantCulture);
            if (targetType == typeof(byte)) return Convert.ToByte(arg, CultureInfo.InvariantCulture);
            if (targetType == typeof(short)) return Convert.ToInt16(arg, CultureInfo.InvariantCulture);
            if (targetType == typeof(char) && arg is string s && s.Length == 1) return s[0];

            if (targetType.IsEnum)
                return arg is string enumStr ? Enum.Parse(targetType, enumStr, true) : Enum.ToObject(targetType, arg);

            return Convert.ChangeType(arg, targetType, CultureInfo.InvariantCulture);
        }

        public static string Execute(string code, int timeoutMs = 5000)
        {
            lock (_lock)
            {
                try
                {
                    code = code.Trim();
                    if (code.Contains(";") && (code.Contains("\n") || code.Contains("var ") || code.Contains("return ")))
                        return ExecuteMultiStatement(code);

                    if (code.StartsWith("return ") && code.EndsWith(";"))
                        return FormatResult(EvaluateExpression(code.Substring(7, code.Length - 8).Trim()));

                    return FormatResult(EvaluateExpression(code.TrimEnd(';')));
                }
                catch (Exception ex)
                {
                    throw new Exception($"Execution error: {ex.Message}", ex);
                }
            }
        }

        private static string ExecuteMultiStatement(string code)
        {
            var variables = new Dictionary<string, object>();
            object lastResult = null;

            foreach (var statement in SplitStatements(code))
            {
                var stmt = statement.Trim();
                if (string.IsNullOrEmpty(stmt)) continue;

                if (stmt.StartsWith("return "))
                {
                    lastResult = EvaluateWithVariables(stmt.Substring(7).TrimEnd(';').Trim(), variables);
                    return FormatResult(lastResult);
                }

                var varMatch = VariableDeclarationPattern.Match(stmt.TrimEnd(';'));
                if (varMatch.Success)
                {
                    var value = EvaluateWithVariables(varMatch.Groups[3].Value, variables);
                    variables[varMatch.Groups[2].Value] = value;
                    lastResult = value;
                    continue;
                }

                lastResult = EvaluateWithVariables(stmt.TrimEnd(';'), variables);
            }

            return FormatResult(lastResult);
        }

        private static List<string> SplitStatements(string code)
        {
            var statements = new List<string>();
            var current = new StringBuilder();
            int depth = 0;
            bool inString = false;
            char stringChar = '\0';

            foreach (char c in code)
            {
                if (!inString && (c == '"' || c == '\'')) { inString = true; stringChar = c; current.Append(c); }
                else if (inString && c == stringChar) { inString = false; current.Append(c); }
                else if (inString) current.Append(c);
                else if (c == '{' || c == '(' || c == '[') { depth++; current.Append(c); }
                else if (c == '}' || c == ')' || c == ']') { depth--; current.Append(c); }
                else if (c == ';' && depth == 0) { statements.Add(current.ToString().Trim()); current.Clear(); }
                else if (c == '\n' || c == '\r') current.Append(' ');
                else current.Append(c);
            }

            if (current.Length > 0) statements.Add(current.ToString().Trim());
            return statements;
        }

        private static object EvaluateWithVariables(string expression, Dictionary<string, object> variables)
        {
            expression = expression.Trim();
            foreach (var kvp in variables)
            {
                if (expression == kvp.Key) return kvp.Value;

                if (expression.StartsWith(kvp.Key + ".") || expression.StartsWith(kvp.Key + "["))
                {
                    var obj = kvp.Value;
                    if (obj == null) return null;

                    var parts = ParseExpression(expression.Substring(kvp.Key.Length).TrimStart('.'));
                    var currentType = obj.GetType();
                    foreach (var part in parts)
                    {
                        if (string.IsNullOrEmpty(part)) continue;
                        var result = EvaluateMember(obj, currentType, part);
                        obj = result.Value;
                        currentType = result.Type ?? obj?.GetType();
                    }
                    return obj;
                }
            }
            return EvaluateExpression(expression);
        }

        private static string FormatResult(object result)
        {
            if (result == null) return "null";
            if (result is System.Collections.IEnumerable enumerable && !(result is string))
            {
                var items = new List<string>();
                int count = 0;
                foreach (var item in enumerable)
                {
                    items.Add(FormatResult(item));
                    if (++count >= 100)
                    {
                        items.Add("... (truncated, showing first 100 of more items)");
                        break;
                    }
                }
                return $"[{string.Join(", ", items)}]";
            }
            return result.ToString();
        }
    }
}
