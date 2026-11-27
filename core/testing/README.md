# Testing (`core/testing/`)

Универсальный клиент DevMCP и раннер рантайм-тестов для любых Unity / BepInEx 5 игр.

## Состав

- `mcp_client.py` — единый TCP JSON-RPC 2.0 клиент (`DevMcpClient`) с буферизацией строк, повторами и сохранением PNG-скриншотов на стороне клиента.
- `mcp_bridge.py` — постоянный `stdio <-> TCP` JSON-RPC 2.0 мост для нативного подключения `Uumc.DevMcp` в IDE (`mcp_config.json`) с резервным каталогом 33 инструментов (`../dev-mcp/tools_manifest.json`) на случай офлайн-игры, авто-переподключением при перезапуске игры и генератором `--print-config`. Все протокольные ответы и ошибки моста (`isError: true`, `-32000` в `resources/read`) отдаются строго на английском языке в едином контракте с `Uumc.DevMcp`, тогда как CLI-диагностика запуска (`--help`, `stderr`) выводится на русском.
- `runner.py` — раннер жизненного цикла теста: разрешение `game_dir` через каскад (`--game-dir` → `UUMC_GAME_DIR` → `.uumc.local.yaml` → `uumc.profile.yaml`), запуск игры в неинтерактивном зрительском мини-окне по умолчанию при `--launch` (через эфемерные переменные `UUMC_SPECTATOR_*` без записи в `com.uumc.devmcp.cfg`), автосинхронизация порта, отслеживание PID, мягкое завершение (`WM_CLOSE` → fallback `taskkill`) и аудит `BepInEx/LogOutput.log`.
- `run_test.ps1` — Windows-обёртка для запуска зрительского теста (`--launch --kill-existing --check-log`, опции `-Interactive`, `-WithAudio`, `-WindowSize`, `-WindowPos`, `-Linger`).
- `test_testing.py` и `test_mcp_bridge.py` — автотесты `unittest` на мок-TCP сервере (включая проверку консистентности `tools_manifest.json` с C#-кодом).

## Запуск и примеры

```bash
# Smoke-тест по профилю игры (зрительское мини-окно в углу экрана без кражи фокуса и звука)
python core/testing/runner.py --profile ../MyGameModding/uumc.profile.yaml --launch --smoke --check-log

# Интерактивный запуск со звуком и увеличенным окном
python core/testing/runner.py --profile ../MyGameModding/uumc.profile.yaml --launch --interactive --with-audio --window-size 1280x720 --smoke

# Запуск пользовательского Python-сценария (принимает run_test(client))
python core/testing/runner.py --profile ../MyGameModding/uumc.profile.yaml --script ../MyGameModding/tests/my_test.py --screenshot shot.png

# PowerShell-обёртка на Windows
powershell -File core/testing/run_test.ps1 -Profile ../MyGameModding/uumc.profile.yaml -Smoke

# Печать фрагмента mcp_config.json для подключения DevMCP в IDE
python core/testing/mcp_bridge.py --workspace ../MyGameModding --print-config
```

## Зависимости

- Python 3.10+ (только стандартная библиотека).
- Для живого прогона: установленный в игре плагин `Uumc.DevMcp.dll`.
