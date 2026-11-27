# Uumc.DevMcp (`core/dev-mcp/`)

Универсальный отладочный BepInEx 5-плагин (`netstandard2.1`) для Unity Mono-игр: поднимает внутриигровой TCP JSON-RPC (MCP) сервер на порту `27015` (по умолчанию) и предоставляет 33 инструмента инспекции сцены, рефлексии, выполнения C# и снятия скриншотов.

## Сборка и тестирование

Пути к сборкам целевой игры (`BepInEx/core/` и `*_Data/Managed/`) подключаются через `core/Directory.Build.props` без хардкода в `.csproj`:

```bash
dotnet build core/dev-mcp/Uumc.DevMcp.csproj -c Release -p:UumcGameDir="../MyGame"
# либо через переменную окружения UUMC_GAME_DIR

# Автономные C# юнит-тесты pure-логики (без игры и Unity/BepInEx)
dotnet test core/dev-mcp.tests/Uumc.DevMcp.Tests.csproj
```

Выходная сборка `Uumc.DevMcp.dll` копируется в `<GameDir>/BepInEx/plugins/`.

## Архитектура модулей

- **`DevMcpPlugin.cs`**: точка входа (`GUID: com.uumc.devmcp`), конфиг `Config.Bind` (`Port`, `BindAddress`, `KeepRunningInBackground`, опциональный `FocusPatchType`), изолированный `HideAndDontSave`-хост (выживает при `HideManagerGameObject = false`), перехват `Application.logMessageReceived` в `LogBuffer` и запуск `MCPServer`.
- **`BuildInfo.cs`**: метаданные сборки.
- **`Server/`**:
  - `DevMcpLog.cs` и `LogBuffer.cs` — pure-C# фасад логирования и кольцевой буфер логов (до 1000 записей) без зависимости от `BepInEx`.
  - `MCPServer.cs` — асинхронный TCP JSON-RPC 2.0 сервер, обработка уведомлений без `id` (`notifications/initialized` и др.), case-insensitive нормализация аргументов (`camelCase` и `snake_case`) и ресурсы (`uumc://logs/unity`, `uumc://scene/hierarchy`, `uumc://game/info`).
  - `MCPProtocol.cs` — DTO-структуры протокола MCP.
  - `UnityMainThreadDispatcher.cs` — очередь исполнения Unity API в главном потоке (`Update()`).
  - `SpectatorWindowController.cs` — эфемерный контроллер зрительского окна автотестов (активируется только по `UUMC_SPECTATOR_WINDOW=1`: оконный режим `Windowed`, `AudioListener.volume = 0`, разблокировка `Cursor.lockState`, Windows `WS_EX_NOACTIVATE | WS_EX_TRANSPARENT` и позиционирование в угол экрана).
  - `UnityHelper.cs` — рефлексивные операции со сценой, компонентами и скриншотами.
- **`tools_manifest.json`**: эталонный JSON-каталог схем 33 инструментов для офлайн-работы `stdio`-моста (`../testing/mcp_bridge.py`); синхронность имён контролируется автотестом `test_mcp_bridge.py`.
- **`Tools/`** (реализуют `IToolDefinition.cs`, 33 инструмента):
  - `CodeExecutionTools.cs` (рефлексионный интерпретатор цепочек `Type.Member.Method()` и присвоений, без операторов/лямбд/Roslyn), `UnityInspectionTools.cs`, `MonoBehaviourControlTools.cs`, `GameStateTools.cs`, `ReflectionTools.cs`, `LogTools.cs`, `AdvancedTools.cs`, `GameKnowledgeTools.cs`.

## Конфигурация (`BepInEx/config/com.uumc.devmcp.cfg`)

- `Server.Port` (`27015`), `Server.BindAddress` (`127.0.0.1`).
- `Runtime.KeepRunningInBackground` (`true`) — запрещает игре сбрасывать `Application.runInBackground = false`.
- `Runtime.FocusPatchType` (`""`), `Runtime.FocusPatchMethod` (`"OnApplicationFocus"`), `Runtime.BlockUnfocusedPause` (`false`) — опциональный конфигурируемый хук против паузы при расфокусе.
