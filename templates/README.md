# Templates (`templates/`)

Шаблоны развёртывания проекта мода под целевую Unity-игру (создаются командой `uumc init`).

## Структура

- `game-profile.schema.yaml` — эталонная структура профиля `uumc.profile.yaml`.
- `mod-project/` — каркас C#-мода под BepInEx 5 (`Directory.Build.props.tmpl`, `Mod.csproj.tmpl`, `Plugin.cs.tmpl`, `ARCHITECTURE.md.tmpl`, `gitignore.tmpl`, `test_smoke_example.py.tmpl`).
- `agent-contour/` — прикладные инструкции для ИИ-агента внутри проекта мода (`AGENTS.md.tmpl`, `rules/`, `skills/`).
- `runtime-enricher.py.tmpl` — заготовка скрипта сбора рантайм-реестров через `DevMcpClient`.
