# Game Indexer (`core/indexer/`)

Универсальный конвейер извлечения метаданных сборки Unity, синхронизации семантического кэша и генерации базы знаний без привязки к конкретной игре и без обязательного наличия декомпилята (`decompiled_dir`).

## Состав

- `CecilExtractor/` — C#-утилита (`Mono.Cecil`), выгружающая `symbols_raw.json` из `Assembly-CSharp.dll`.
- `profile.py` — парсер `uumc.profile.yaml` / `.uumc.local.yaml` без сторонних библиотек, каскадный резолвер `UUMC_ROOT` / `game_dir` и синхронизатор порта `com.uumc.devmcp.cfg`.
- `sync_descriptions.py` — инициализация кэша напрямую из `symbols_raw.json`, сверка хэшей сигнатур (`SYNCED`/`STALE`/`NEW`/`REMOVED`), отчёт `breaking_changes.md` с проверкой `[HarmonyPatch]` в `src/`, чистый граф `relations` по IL Call Graph и опциональное AI-обогащение через LM Studio.
- `validate_taxonomy.py` — генерация черновика `--init` из `symbols_raw.json` и валидация `Set(YAML) == Set(cache_or_symbols)`.
- `render_docs.py` — рендеринг двухуровневых справочников, `README.md` (с автодетектом синглтонов) и машинного каталога `INDEX-LLM.md` без гиперссылок.
- `enrich_base.py` — опциональный сборщик рантайм-метаданных через `DevMcpClient` или пользовательский адаптер игры.
- `test_indexer.py` — автотесты `unittest` в `tempfile.TemporaryDirectory()`.

## Запуск и примеры

```bash
# 1. Инициализация черновика таксономии (если ещё нет taxonomy.yaml)
python core/indexer/validate_taxonomy.py --init --symbols symbols_raw.json --taxonomy taxonomy.yaml

# 2. Синхронизация кэша без декомпилята и без LLM
python core/indexer/sync_descriptions.py --symbols symbols_raw.json --cache descriptions_cache.json --no-ai

# 3. Проверка таксономии и генерация базы знаний
python core/indexer/validate_taxonomy.py --taxonomy taxonomy.yaml --cache descriptions_cache.json
python core/indexer/render_docs.py --taxonomy taxonomy.yaml --cache descriptions_cache.json --symbols symbols_raw.json --output-dir docs/game-internals
```

## Зависимости

- Python 3.10+ (только стандартная библиотека).
- `.NET SDK 8.0+` для `CecilExtractor/`.
