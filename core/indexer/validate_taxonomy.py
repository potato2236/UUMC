#!/usr/bin/env python3
"""
Game-agnostic taxonomy validator and auto-draft generator for UUMC.

Validates:
1. Two-level mapping structure: folder -> file.md -> [TypeIds].
2. Zero duplicate types across files.
3. Exact coverage: Set(taxonomy.yaml) == Set(active cache_or_symbols).
4. Optional expected folders check (configured via CLI/profile, never hardcoded).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    from core.indexer.profile import get_profile_section, load_profile, parse_yaml_file, resolve_profile_path
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.indexer.profile import get_profile_section, load_profile, parse_yaml_file, resolve_profile_path


def parse_taxonomy_yaml(yaml_path: Path) -> dict[str, dict[str, list[str]]]:
    """Parses and validates the 2-level taxonomy mapping."""
    raw = parse_yaml_file(yaml_path)
    taxonomy: dict[str, dict[str, list[str]]] = {}

    for folder, files in raw.items():
        if not isinstance(files, dict):
            raise ValueError(f"{yaml_path}: секция '{folder}' должна содержать словарь файлов .md")
        taxonomy[folder] = {}
        for filename, types_list in files.items():
            if not isinstance(types_list, list):
                raise ValueError(f"{yaml_path}: '{folder}/{filename}' должен содержать список типов")
            taxonomy[folder][filename] = [str(t) for t in types_list if str(t).strip()]

    return taxonomy


def validate_taxonomy(
    taxonomy: dict[str, dict[str, list[str]]],
    reference_types: set[str],
    expected_folders: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """Checks structure, uniqueness, and exact set coverage."""
    errors: list[str] = []

    if not taxonomy:
        errors.append("Таксономия пуста (нет ни одной макро-папки).")
        return False, errors

    if expected_folders:
        for folder in expected_folders:
            if folder not in taxonomy:
                errors.append(f"Отсутствует ожидаемая папка таксономии: '{folder}'")

    all_yaml_types: list[str] = []
    for folder, files in taxonomy.items():
        if not files:
            errors.append(f"Папка '{folder}' не содержит ни одного файла.")
            continue
        for filename, types_list in files.items():
            if not filename.endswith(".md"):
                errors.append(f"Файл '{folder}/{filename}' должен иметь расширение .md")
            if not types_list:
                errors.append(f"Файл '{folder}/{filename}' пуст (0 типов).")
            all_yaml_types.extend(types_list)

    counts = Counter(all_yaml_types)
    duplicates = sorted(t for t, count in counts.items() if count > 1)
    if duplicates:
        errors.append(f"Обнаружены дубликаты типов ({len(duplicates)} шт.): {', '.join(duplicates[:10])}")

    yaml_types_set = set(all_yaml_types)
    missing = sorted(reference_types - yaml_types_set)
    extra = sorted(yaml_types_set - reference_types)

    if missing:
        errors.append(f"В taxonomy.yaml отсутствуют типы ({len(missing)} шт.): {', '.join(missing[:10])}")
    if extra:
        errors.append(f"В taxonomy.yaml присутствуют лишние типы ({len(extra)} шт.): {', '.join(extra[:10])}")

    return len(errors) == 0, errors


def build_draft_taxonomy(symbols_data: dict, max_types_per_file: int = 40) -> dict[str, dict[str, list[str]]]:
    """Generates a deterministic 2-level taxonomy draft from symbols_raw.json."""
    types_dict: dict[str, dict] = symbols_data.get("types", {})
    groups: dict[str, list[str]] = defaultdict(list)

    for tid, tinfo in sorted(types_dict.items()):
        ns = (tinfo.get("namespace") or "").strip()
        top_ns = ns.split(".")[0].lower() if ns else "global"
        safe_group = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in top_ns) or "global"
        groups[safe_group].append(tid)

    taxonomy: dict[str, dict[str, list[str]]] = {}
    for group_name, type_ids in sorted(groups.items()):
        sorted_ids = sorted(type_ids)
        folder = "core" if group_name == "global" else group_name
        taxonomy.setdefault(folder, {})

        if len(sorted_ids) <= max_types_per_file:
            taxonomy[folder]["types.md"] = sorted_ids
        else:
            chunks = [sorted_ids[i : i + max_types_per_file] for i in range(0, len(sorted_ids), max_types_per_file)]
            for idx, chunk in enumerate(chunks, start=1):
                taxonomy[folder][f"part-{idx:02d}.md"] = chunk

    return taxonomy


def write_taxonomy_yaml(taxonomy: dict[str, dict[str, list[str]]], output_path: Path) -> None:
    lines = [
        "# Автосгенерированная таксономия базы знаний UUMC",
        "# Формат: <макро-папка> -> <справочник.md> -> [список типов]",
        "",
    ]
    for folder, files in sorted(taxonomy.items()):
        lines.append(f"{folder}:")
        for filename, type_ids in sorted(files.items()):
            lines.append(f"  {filename}:")
            for tid in type_ids:
                lines.append(f'    - "{tid}"')
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def print_summary_table(taxonomy: dict[str, dict[str, list[str]]]) -> int:
    print("=" * 70)
    print(" РАСПРЕДЕЛЕНИЕ ТАКСОНОМИИ БАЗЫ ЗНАНИЙ")
    print("=" * 70)
    print(f" {'Папка':<16} | {'Справочник (.md)':<32} | {'Типов':>6}")
    print("-" * 70)

    total_types = 0
    total_files = 0
    for folder, files in sorted(taxonomy.items()):
        first_row = True
        for filename, types_list in sorted(files.items()):
            label = folder if first_row else ""
            print(f" {label:<16} | {filename:<32} | {len(types_list):>6}")
            total_types += len(types_list)
            total_files += 1
            first_row = False
        print("-" * 70)

    print(f" ИТОГО: {len(taxonomy)} папок, {total_files} файлов, {total_types} записей.")
    print("=" * 70)
    return total_types


def _load_reference_types(
    cache_path: Path | None,
    symbols_path: Path | None,
    *,
    explicit_cache: bool = False,
    explicit_symbols: bool = False,
) -> set[str]:
    if explicit_cache and (cache_path is None or not cache_path.is_file()):
        raise FileNotFoundError(f"Указанный файл кэша не найден: {cache_path}")
    if explicit_symbols and (symbols_path is None or not symbols_path.is_file()):
        raise FileNotFoundError(f"Указанный файл символов не найден: {symbols_path}")

    if cache_path and cache_path.is_file():
        cache_data = json.loads(cache_path.read_text(encoding="utf-8-sig"))
        return {
            k[2:]
            for k, v in cache_data.items()
            if k.startswith("T:") and isinstance(v, dict) and v.get("status") != "REMOVED"
        }
    if symbols_path and symbols_path.is_file():
        symbols_data = json.loads(symbols_path.read_text(encoding="utf-8-sig"))
        return set(symbols_data.get("types", {}).keys())
    raise FileNotFoundError("Не найден ни файл кэша (--cache), ни файл символов (--symbols).")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Валидатор и генератор черновика таксономии taxonomy.yaml (без хардкода структуры)."
    )
    parser.add_argument("--profile", type=Path, default=None, help="Опциональный путь к uumc.profile.yaml")
    parser.add_argument("--taxonomy", "-t", type=Path, default=None, help="Путь к taxonomy.yaml")
    parser.add_argument("--cache", "-c", type=Path, default=None, help="Путь к descriptions_cache.json")
    parser.add_argument("--symbols", "-s", type=Path, default=None, help="Путь к symbols_raw.json")
    parser.add_argument("--expected-folder", action="append", default=[], help="Обязательная папка таксономии")
    parser.add_argument("--init", action="store_true", help="Сгенерировать черновик taxonomy.yaml из symbols_raw.json")
    parser.add_argument("--max-types-per-file", type=int, default=40, help="Максимум типов на файл при --init")
    args = parser.parse_args()

    base_dir = Path.cwd()
    profile_data: dict = {}
    try:
        if args.profile is not None:
            p_path = args.profile.resolve()
            profile_data = load_profile(p_path)
            base_dir = p_path.parent

        idx_cfg = get_profile_section(profile_data, "indexer")
        taxonomy_path = args.taxonomy.resolve() if args.taxonomy else (
            resolve_profile_path(base_dir, idx_cfg.get("taxonomy_file")) or (base_dir / "taxonomy.yaml")
        )
        cache_path = args.cache.resolve() if args.cache else resolve_profile_path(base_dir, idx_cfg.get("cache_file"))
        symbols_path = args.symbols.resolve() if args.symbols else resolve_profile_path(base_dir, idx_cfg.get("symbols_file"))

        if args.init:
            if not symbols_path or not symbols_path.is_file():
                raise FileNotFoundError(f"Для --init требуется существующий --symbols ({symbols_path})")
            symbols_data = json.loads(symbols_path.read_text(encoding="utf-8-sig"))
            draft = build_draft_taxonomy(symbols_data, max_types_per_file=max(1, args.max_types_per_file))
            write_taxonomy_yaml(draft, taxonomy_path)
            print_summary_table(draft)
            print(f"[OK] Черновик таксономии сохранён в: {taxonomy_path}")
            sys.exit(0)

        if not taxonomy_path.is_file():
            raise FileNotFoundError(f"Файл таксономии не найден: {taxonomy_path}")

        ref_types = _load_reference_types(
            cache_path,
            symbols_path,
            explicit_cache=args.cache is not None,
            explicit_symbols=args.symbols is not None,
        )
        taxonomy = parse_taxonomy_yaml(taxonomy_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    expected = args.expected_folder or idx_cfg.get("expected_folders") or None
    is_valid, errors = validate_taxonomy(taxonomy, ref_types, expected_folders=expected)
    print_summary_table(taxonomy)

    if is_valid:
        print(f"\n[OK] Таксономия валидна: Set(YAML) == Set(reference) ({len(ref_types)} типов), 0 дубликатов.")
        sys.exit(0)

    print("\n[FAIL] Обнаружены нарушения таксономии:", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
