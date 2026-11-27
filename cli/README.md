# UUMC CLI (`cli/`)

Утилиты командной строки и графический мастер для генерации и сопровождения модов под Unity Mono + BepInEx 5.

## Состав

- `launcher.py` — графический мастер (`start.cmd`) для поиска игр в Steam, проверки совместимости и создания проекта мода.
- `uumc.py` — единый CLI (`scan`, `doctor`, `init`, `link`, `build`, `report`, `index`, `sync`, `taxonomy`, `render`, `enrich`, `test`, `mcp`).
- `bootstrap.py` — инспекция игр, генерация рабочего пространства мода и управление локальными отчётами.
