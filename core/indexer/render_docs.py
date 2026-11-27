#!/usr/bin/env python3
"""
Game-agnostic Knowledge Base Renderer for UUMC.

Generates:
1. Two-level thematic markdown files (<output_dir>/<folder>/<file>.md).
2. Human overview (<output_dir>/README.md) with auto-detected singletons.
3. Link-free machine catalog (<output_dir>/INDEX-LLM.md).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from core.indexer.profile import get_profile_section, load_profile, resolve_profile_path
    from core.indexer.validate_taxonomy import parse_taxonomy_yaml
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.indexer.profile import get_profile_section, load_profile, resolve_profile_path
    from core.indexer.validate_taxonomy import parse_taxonomy_yaml

DEFAULT_MAX_FILE_SIZE_KB = 55.0
SINGLETON_FIELD_NAMES = {"main", "instance", "singleton", "current"}


def _split_member_key(key: str) -> tuple[str, str, str] | None:
    """Splits ECMA-334 key ('F:'/'P:'/'M:') into (kind, type_id, member_sig)."""
    if key.startswith(("F:", "P:")):
        kind = "field" if key.startswith("F:") else "property"
        tid, sep, fname = key[2:].rpartition(".")
        return (kind, tid, fname) if sep else None
    if key.startswith("M:"):
        head, sep_paren, tail = key[2:].partition("(")
        tid, sep_dot, mname = head.rpartition(".")
        if not sep_dot:
            return None
        return ("method", tid, f"{mname}{sep_paren}{tail}")
    return None


def find_singletons(
    cache: dict[str, Any],
    symbols_data: dict[str, Any] | None = None,
    extra_singletons: list[str] | None = None,
) -> list[tuple[str, str, str, str, str]]:
    """Auto-detects active static singletons from symbols_raw.json and optional profile overrides."""
    candidates: set[tuple[str, str]] = set()

    for item in extra_singletons or []:
        if "." in item:
            tid, fname = item.rsplit(".", 1)
            candidates.add((tid.strip(), fname.strip()))

    if symbols_data:
        raw_types: dict[str, Any] = symbols_data.get("types", {})
        simple_to_tid = {tinfo.get("name"): tid for tid, tinfo in raw_types.items() if tinfo.get("name")}

        for tid, tinfo in raw_types.items():
            for fld in tinfo.get("fields", []):
                if fld.get("is_static") and fld.get("name", "").lower() in SINGLETON_FIELD_NAMES:
                    candidates.add((tid, fld["name"]))
            for prop in tinfo.get("properties", []):
                if prop.get("is_static") and prop.get("name", "").lower() in SINGLETON_FIELD_NAMES:
                    candidates.add((tid, prop["name"]))
            for sing in tinfo.get("singletons", []):
                if "." in sing:
                    owner_short, fname = sing.split(".", 1)
                    resolved_tid = simple_to_tid.get(owner_short, owner_short)
                    if resolved_tid in raw_types:
                        candidates.add((resolved_tid, fname))

    results: list[tuple[str, str, str, str, str]] = []
    for tid, fname in sorted(candidates):
        t_entry = cache.get(f"T:{tid}")
        if not t_entry or t_entry.get("status") == "REMOVED":
            continue
        f_entry = cache.get(f"F:{tid}.{fname}") or cache.get(f"P:{tid}.{fname}") or {}
        if f_entry.get("status") == "REMOVED":
            continue
        raw_sig = f_entry.get("raw_sig", f"{tid}.{fname}")
        src_file = f_entry.get("file") or t_entry.get("file") or f"{tid.split('+')[0].split('.')[-1]}.cs"
        summary = f_entry.get("summary") or t_entry.get("summary") or ""
        results.append((tid, fname, raw_sig, src_file, summary))

    return results


def render_category_file(
    folder: str,
    filename: str,
    type_names: list[str],
    cache: dict[str, Any],
    type_fields: dict[str, list[tuple[str, dict[str, Any]]]],
    type_methods: dict[str, list[tuple[str, dict[str, Any]]]],
    file_descriptions: dict[str, str],
) -> str:
    title = filename.replace(".md", "").replace("-", " ").title()
    rel_key = f"{folder}/{filename}"
    desc = file_descriptions.get(rel_key, "Справочник подсистемы игры.")

    lines = [
        f"# {title} (`{rel_key}`)",
        "",
        f"> {desc}",
        "",
        "## Содержание",
        "",
        " • ".join(sorted(type_names)),
        "",
        "---",
        "",
    ]

    for tid in sorted(type_names):
        t_info = cache.get(f"T:{tid}", {})
        file_name = t_info.get("file", f"{tid.split('+')[0].split('.')[-1]}.cs")
        summary = t_info.get("summary", "Назначение не указано.").strip()
        relations = t_info.get("relations", [])

        lines.append(f"### `{tid}` (`{file_name}`)")
        lines.append(f"- **Роль:** {summary}")

        f_list = type_fields.get(tid, [])
        if f_list:
            f_tokens = []
            for fname, f_entry in f_list:
                f_summ = f_entry.get("summary", "").strip()
                f_tokens.append(f"`{fname}` ({f_summ})" if f_summ else f"`{fname}`")
            lines.append(f"- **Поля:** {', '.join(f_tokens)}")
        else:
            lines.append("- **Поля:** нет.")

        m_list = type_methods.get(tid, [])
        if m_list:
            lines.append("- **Harmony-точки:**")
            for msig, m_entry in m_list:
                m_summ = m_entry.get("summary", "").strip()
                raw_sig = m_entry.get("raw_sig", msig).strip("` ")
                lines.append(f"  - `{raw_sig}` — {m_summ}" if m_summ else f"  - `{raw_sig}`")
        else:
            lines.append("- **Harmony-точки:** нет.")

        if relations:
            rel_str = ", ".join(f"`{r}`" for r in relations[:10])
            lines.append(f"- **Связи:** {rel_str}")

        lines.append("")

    return "\n".join(lines)


def render_master_readme(
    game_name: str,
    taxonomy: dict[str, dict[str, list[str]]],
    singletons: list[tuple[str, str, str, str, str]],
    file_descriptions: dict[str, str],
) -> str:
    total_types = sum(len(tlist) for files in taxonomy.values() for tlist in files.values())
    total_files = sum(len(files) for files in taxonomy.values())

    lines = [
        f"# База знаний {game_name} (`docs/game-internals/`)",
        "",
        f"Справочная документация по внутреннему устройству, типам, полям и методам игры *{game_name}* "
        "для разработки модов на базе **BepInEx 5** и **Harmony**.",
        "",
        f"Индексировано типов: **{total_types}** (разделов: **{len(taxonomy)}**, файлов: **{total_files}**). "
        "Машинный реестр без гиперссылок для нейросетей вынесен в [INDEX-LLM.md](INDEX-LLM.md).",
        "",
        "## Карта тематических справочников",
        "",
        "| Раздел / Файл | Типов | Описание подсистемы |",
        "|---|:---:|---|",
    ]

    type_to_file: dict[str, str] = {}
    for folder, files in sorted(taxonomy.items()):
        for fn, tlist in sorted(files.items()):
            rel_path = f"{folder}/{fn}"
            desc = file_descriptions.get(rel_path, "Справочник подсистемы.").replace("|", "\\|")
            lines.append(f"| [{rel_path}]({rel_path}) | {len(tlist)} | {desc} |")
            for t in tlist:
                type_to_file[t] = rel_path

    lines.extend(["", "---", "", "## Глобальные синглтоны и точки доступа", ""])
    if singletons:
        lines.extend(["| Класс | Поле доступа | Файл C# | Назначение / Описание |", "|---|---|---|---|"])
        for tid, fname, raw_sig, fn, summ in singletons:
            doc_file = type_to_file.get(tid, "")
            anchor = tid.lower().replace("+", "").replace(".", "").replace("`", "")
            doc_link = f"[{tid}]({doc_file}#{anchor})" if doc_file else f"`{tid}`"
            desc_cell = (summ or raw_sig).replace("|", "\\|")
            lines.append(f"| {doc_link} | `{fname}` | `{fn}` | {desc_cell} |")
    else:
        lines.append("*Глобальные синглтоны не обнаружены или не заданы.*")

    lines.extend(["", "---", "", "## Каталог типов для нейросетей", "", f"Полный машинный реестр всех {total_types} типов без гиперссылочной разметки: [INDEX-LLM.md](INDEX-LLM.md).", ""])
    return "\n".join(lines)


def render_machine_index(
    game_name: str,
    taxonomy: dict[str, dict[str, list[str]]],
    cache: dict[str, Any],
) -> str:
    """Renders pure machine catalog INDEX-LLM.md strictly free of markdown links."""
    type_to_file: dict[str, str] = {}
    for folder, files in sorted(taxonomy.items()):
        for fn, tlist in sorted(files.items()):
            rel_path = f"{folder}/{fn}"
            for t in tlist:
                type_to_file[t] = rel_path

    lines = [
        f"# Машинный каталог типов {game_name} (INDEX-LLM)",
        "",
        "| Тип | Файл базы знаний | Исходник C# | Назначение |",
        "|---|---|---|---|",
    ]

    for tid in sorted(type_to_file.keys()):
        rel_path = type_to_file[tid]
        t_info = cache.get(f"T:{tid}", {})
        src_file = t_info.get("file", f"{tid.split('+')[0].split('.')[-1]}.cs")
        summary = t_info.get("summary", "").strip().replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {tid} | {rel_path} | {src_file} | {summary} |")

    lines.append("")
    return "\n".join(lines)


def render_knowledge_base(
    taxonomy_path: Path,
    cache_path: Path,
    output_dir: Path,
    *,
    symbols_path: Path | None = None,
    game_name: str = "Unity Game",
    max_file_size_kb: float = DEFAULT_MAX_FILE_SIZE_KB,
    file_descriptions: dict[str, str] | None = None,
    known_singletons: list[str] | None = None,
) -> dict[str, Any]:
    if not taxonomy_path.is_file():
        raise FileNotFoundError(f"Файл таксономии не найден: {taxonomy_path}")
    if not cache_path.is_file():
        raise FileNotFoundError(f"Файл кэша не найден: {cache_path}")

    cache: dict[str, Any] = json.loads(cache_path.read_text(encoding="utf-8-sig"))
    taxonomy = parse_taxonomy_yaml(taxonomy_path)
    symbols_data = (
        json.loads(symbols_path.read_text(encoding="utf-8-sig"))
        if symbols_path and symbols_path.is_file()
        else None
    )

    type_fields: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    type_methods: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)

    for k, v in cache.items():
        if not isinstance(v, dict) or v.get("status") == "REMOVED":
            continue
        parsed_member = _split_member_key(k)
        if not parsed_member:
            continue
        mkind, tid, member_sig = parsed_member
        if mkind in ("field", "property"):
            type_fields[tid].append((member_sig, v))
        elif mkind == "method":
            type_methods[tid].append((member_sig, v))

    for tid in type_fields:
        type_fields[tid].sort(key=lambda x: x[0])
    for tid in type_methods:
        type_methods[tid].sort(key=lambda x: x[0])

    # Filter out REMOVED types so stale/deleted entries never leak into generated docs
    filtered_taxonomy: dict[str, dict[str, list[str]]] = {}
    missing_in_cache: list[str] = []
    for folder, files in taxonomy.items():
        for filename, type_names in files.items():
            active_list: list[str] = []
            for tid in type_names:
                t_entry = cache.get(f"T:{tid}")
                if t_entry is None:
                    missing_in_cache.append(tid)
                elif t_entry.get("status") != "REMOVED":
                    active_list.append(tid)
            if active_list:
                filtered_taxonomy.setdefault(folder, {})[filename] = active_list

    if missing_in_cache:
        raise ValueError(f"В кэше отсутствуют типы из таксономии ({len(missing_in_cache)} шт.): {missing_in_cache[:5]}")

    desc_map = file_descriptions or {}
    rendered_files: dict[Path, str] = {}
    large_files: list[str] = []
    active_type_count = 0

    for folder, files in sorted(filtered_taxonomy.items()):
        for filename, type_names in sorted(files.items()):
            active_type_count += len(type_names)
            file_path = output_dir / folder / filename
            content = render_category_file(
                folder, filename, type_names, cache, type_fields, type_methods, desc_map
            )
            rendered_files[file_path] = content
            if len(content.encode("utf-8")) / 1024 > max_file_size_kb:
                large_files.append(f"{folder}/{filename}")

    singletons = find_singletons(cache, symbols_data, known_singletons)
    readme_content = render_master_readme(game_name, filtered_taxonomy, singletons, desc_map)
    index_llm_content = render_machine_index(game_name, filtered_taxonomy, cache)

    rendered_files[output_dir / "README.md"] = readme_content
    rendered_files[output_dir / "INDEX-LLM.md"] = index_llm_content

    for path, content in rendered_files.items():
        if not content.strip():
            raise ValueError(f"Сгенерирован пустой файл: {path}")

    for path, content in rendered_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    return {
        "files": len(rendered_files),
        "types": active_type_count,
        "singletons": len(singletons),
        "large_files": large_files,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Генератор базы знаний docs/game-internals/ (UUMC).")
    parser.add_argument("--profile", type=Path, default=None, help="Опциональный путь к uumc.profile.yaml")
    parser.add_argument("--taxonomy", "-t", type=Path, default=None, help="Путь к taxonomy.yaml")
    parser.add_argument("--cache", "-c", type=Path, default=None, help="Путь к descriptions_cache.json")
    parser.add_argument("--symbols", "-s", type=Path, default=None, help="Опциональный путь к symbols_raw.json")
    parser.add_argument("--output-dir", "-o", type=Path, default=None, help="Директория вывода базы знаний")
    parser.add_argument("--game-name", type=str, default=None, help="Название игры для заголовков")
    parser.add_argument("--max-size-kb", type=float, default=None, help="Порог размера файла (КБ)")
    args = parser.parse_args()

    base_dir = Path.cwd()
    profile_data: dict[str, Any] = {}
    try:
        if args.profile is not None:
            p_path = args.profile.resolve()
            profile_data = load_profile(p_path)
            base_dir = p_path.parent

        game_cfg = get_profile_section(profile_data, "game")
        idx_cfg = get_profile_section(profile_data, "indexer")

        taxonomy_path = args.taxonomy.resolve() if args.taxonomy else (
            resolve_profile_path(base_dir, idx_cfg.get("taxonomy_file")) or (base_dir / "taxonomy.yaml")
        )
        cache_path = args.cache.resolve() if args.cache else (
            resolve_profile_path(base_dir, idx_cfg.get("cache_file")) or (base_dir / "descriptions_cache.json")
        )
        symbols_path = args.symbols.resolve() if args.symbols else resolve_profile_path(base_dir, idx_cfg.get("symbols_file"))
        if args.symbols is not None and (symbols_path is None or not symbols_path.is_file()):
            raise FileNotFoundError(f"Указанный файл символов не найден: {symbols_path}")

        output_dir = args.output_dir.resolve() if args.output_dir else (
            resolve_profile_path(base_dir, idx_cfg.get("docs_output_dir")) or (base_dir / "docs" / "game-internals")
        )
        game_name = args.game_name or game_cfg.get("name") or "Unity Game"
        max_kb = float(args.max_size_kb or idx_cfg.get("max_file_size_kb") or DEFAULT_MAX_FILE_SIZE_KB)

        stats = render_knowledge_base(
            taxonomy_path,
            cache_path,
            output_dir,
            symbols_path=symbols_path,
            game_name=game_name,
            max_file_size_kb=max_kb,
            file_descriptions=idx_cfg.get("file_descriptions") if isinstance(idx_cfg.get("file_descriptions"), dict) else None,
            known_singletons=idx_cfg.get("known_singletons") if isinstance(idx_cfg.get("known_singletons"), list) else None,
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    print(
        f"[OK] База знаний сгенерирована в {output_dir}: "
        f"{stats['files']} файлов, {stats['types']} типов, синглтонов: {stats['singletons']}."
    )
    if stats["large_files"]:
        print(f"[WARN] Превышен порог {max_kb} КБ: {', '.join(stats['large_files'])}")


if __name__ == "__main__":
    main()
