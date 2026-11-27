# CecilExtractor (`core/indexer/CecilExtractor/`)

Универсальная консольная C#-утилита для извлечения CLR-метаданных, сигнатур (схема ECMA-334), IL Call Graph и глобальных синглтонов из сборки Unity (`Assembly-CSharp.dll`).

## Назначение

- Извлечение типов (классы, структуры, интерфейсы, enum, делегаты, вложенные `Outer+Inner`), полей, свойств и методов с хэшами `sig_hash`.
- Построение графа вызовов IL Call Graph (`OpCodes.Call`, `Callvirt`, `Newobj`) и поиск обращений к синглтонам (`.main`, `.instance`, `.singleton`).
- Детерминированный вывод в `symbols_raw.json` — основа для генерации базы знаний даже при отсутствии декомпилированных `.cs`-файлов (`decompiled_dir`).

## Запуск и примеры

```bash
# Сборка и запуск с явным путём к сборке
dotnet run --project core/indexer/CecilExtractor/CecilExtractor.csproj -- \
  --assembly ../MyGame/MyGame_Data/Managed/Assembly-CSharp.dll \
  --output ../MyGameModding/symbols_raw.json

# Или через папку игры / переменную UUMC_GAME_DIR
dotnet run --project core/indexer/CecilExtractor/CecilExtractor.csproj -- \
  --game-dir ../MyGame \
  --output symbols_raw.json
```

## Зависимости

- **.NET SDK 8.0+** (`net8.0`).
- `Mono.Cecil` — подключается из `$(ResolvedUumcBepInExCoreDir)` (через `core/Directory.Build.props`, если задан `UUMC_GAME_DIR` / `-p:UumcGameDir=...`) либо автоматически через NuGet `Mono.Cecil 0.11.5`.

## Входные и выходные данные

- **Вход:** скомпилированная управляемая сборка Unity (обычно `Assembly-CSharp.dll`).
- **Выход:** JSON-файл `symbols_raw.json`.
