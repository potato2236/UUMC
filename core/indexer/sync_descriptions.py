#!/usr/bin/env python3
"""
Game-agnostic Descriptions Synchronizer and Breaking Changes Detector for UUMC.

1. Seeds descriptions_cache.json directly from CecilExtractor's symbols_raw.json
   when no cache exists yet (zero markdown scraping, zero decompiled requirement).
2. Classifies symbols into SYNCED, STALE, NEW, REMOVED.
3. Computes clean type relations strictly from IL Call Graph + member types,
   validated against known assembly types (no leaked literals/file names).
4. Generates breaking_changes.md cross-checked with [HarmonyPatch] in src/.
5. Optionally enriches NEW/STALE/non-AI entries via LM Studio, using decompiled_dir
   if present or Cecil metadata summary otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from core.indexer.profile import get_profile_section, load_profile, resolve_profile_path
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.indexer.profile import get_profile_section, load_profile, resolve_profile_path

DEFAULT_LM_STUDIO_URL = "http://localhost:1234/v1"


def compute_sig_hash(signature_key: str) -> str:
    return hashlib.sha256(signature_key.strip().encode("utf-8")).hexdigest()[:16]


def _build_type_lookup(raw_types: dict[str, dict[str, Any]]) -> tuple[set[str], dict[str, str]]:
    """Returns (all_type_ids, unambiguous_simple_name_to_type_id)."""
    all_ids = set(raw_types.keys())
    by_simple: dict[str, list[str]] = {}
    for tid, tinfo in raw_types.items():
        simple = tinfo.get("name") or tid.split(".")[-1].split("+")[-1]
        by_simple.setdefault(simple, []).append(tid)
        if "+" in tid:
            by_simple.setdefault(tid.split(".")[-1], []).append(tid)

    unambiguous = {k: v[0] for k, v in by_simple.items() if len(v) == 1}
    return all_ids, unambiguous


def _resolve_known_type(token: str, all_ids: set[str], simple_map: dict[str, str]) -> str | None:
    cleaned = token.strip(" `*.,;\"'()[]<>?")
    if not cleaned:
        return None
    if cleaned in all_ids:
        return cleaned
    if cleaned in simple_map:
        return simple_map[cleaned]
    return None


def compute_type_relations_from_symbols(
    tid: str,
    tinfo: dict[str, Any],
    all_ids: set[str],
    simple_map: dict[str, str],
) -> list[str]:
    """Derives clean, verified type relations from IL Call Graph, singletons, base type, and members."""
    candidates: list[str] = []

    base_type = tinfo.get("base_type")
    if base_type:
        candidates.append(base_type)
    candidates.extend(tinfo.get("interfaces", []))

    for call_sig in tinfo.get("calls", []):
        candidates.append(call_sig.split(".", 1)[0])
    for sing in tinfo.get("singletons", []):
        candidates.append(sing.split(".", 1)[0])
    for fld in tinfo.get("fields", []):
        candidates.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_+.]*", fld.get("type", "")))
    for prop in tinfo.get("properties", []):
        candidates.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_+.]*", prop.get("type", "")))
    for method in tinfo.get("methods", []):
        candidates.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_+.]*", method.get("return_type", "")))
        for param in method.get("parameters", []):
            candidates.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_+.]*", param.get("type", "")))

    resolved = set()
    for cand in candidates:
        match = _resolve_known_type(cand, all_ids, simple_map)
        if match and match != tid:
            resolved.add(match)
    return sorted(resolved)


def filter_relations_against_symbols(
    raw_tokens: list[str],
    owner_tid: str,
    all_ids: set[str],
    simple_map: dict[str, str],
) -> list[str]:
    resolved = set()
    for tok in raw_tokens:
        match = _resolve_known_type(tok, all_ids, simple_map)
        if match and match != owner_tid:
            resolved.add(match)
    return sorted(resolved)


def extract_relations_from_text(
    text: str,
    owner_tid: str,
    all_ids: set[str],
    simple_map: dict[str, str],
) -> list[str]:
    tokens: list[str] = []
    rel_match = re.search(r"\[Связи:\s*([^\]]+)\]", text, re.IGNORECASE)
    if rel_match:
        tokens.extend(rel_match.group(1).split(","))
    tokens.extend(re.findall(r"`([^`]+)`", text))
    return filter_relations_against_symbols(tokens, owner_tid, all_ids, simple_map)


def clean_summary_text(raw_text: str) -> str:
    text = re.sub(r"\[Связи:\s*[^\]]+\]", "", raw_text, flags=re.IGNORECASE).strip()
    for prefix in ("Назначение:", "Описание:", "Этот метод", "Данный метод", "Класс отвечает за"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    return text.strip()


def build_fallback_summary(entry_kind: str, obj: dict[str, Any]) -> str:
    """Produces a deterministic structural summary without AI or decompiled files."""
    if entry_kind == "type":
        if obj.get("is_enum"):
            kind_label = "Перечисление"
        elif obj.get("is_interface"):
            kind_label = "Интерфейс"
        elif obj.get("is_delegate"):
            kind_label = "Делегат"
        elif obj.get("is_value_type"):
            kind_label = "Структура"
        elif obj.get("is_static"):
            kind_label = "Статический класс"
        else:
            kind_label = "Класс"

        base = obj.get("base_type")
        base_part = f" (: `{base}`)" if base and base not in ("Object", "ValueType", "Enum") else ""
        return (
            f"{kind_label}{base_part}; полей: {len(obj.get('fields', []))}, "
            f"свойств: {len(obj.get('properties', []))}, методов: {len(obj.get('methods', []))}."
        )
    if entry_kind == "field":
        return f"Поле типа `{obj.get('type', 'object')}`."
    if entry_kind == "property":
        return f"Свойство типа `{obj.get('type', 'object')}`."
    params = ", ".join(f"{p.get('type', '')} {p.get('name', '')}".strip() for p in obj.get("parameters", []))
    return f"Возвращает `{obj.get('return_type', 'void')}` ({params})." if params else f"Возвращает `{obj.get('return_type', 'void')}`."


def get_active_harmony_targets(src_dir: Path | None) -> set[tuple[str, str]]:
    targets: set[tuple[str, str]] = set()
    if not src_dir or not src_dir.is_dir():
        return targets

    pattern = re.compile(r'\[HarmonyPatch\(\s*typeof\(([^)]+)\)\s*,\s*"([^"]+)"', re.MULTILINE)
    for cs_file in src_dir.rglob("*.cs"):
        try:
            content = cs_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in pattern.finditer(content):
            targets.add((match.group(1).strip(), match.group(2).strip()))
    return targets


def detect_lm_studio_model(base_url: str) -> str | None:
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/models")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = data.get("data", [])
            for m in models:
                mid = m.get("id", "")
                if "embed" not in mid.lower():
                    return mid
            return models[0].get("id") if models else None
    except Exception:
        return None


def query_lm_studio(prompt: str, base_url: str, model_name: str, max_tokens: int = 600) -> str | None:
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты — технический аналитик кода C# / Unity для моддинга (BepInEx/Harmony).\n"
                    "Опиши роль и назначение сущности кратко в ОДНОМ предложении на русском языке без вводных слов.\n"
                    "В конце обязательно укажи упомянутые ключевые типы в формате [Связи: Type1, Type2]."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    try:
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload).encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
    except Exception as exc:
        print(f"  [Ошибка LM Studio] {exc}", file=sys.stderr)
    return None


def build_cecil_context_snippet(raw_entry: dict[str, Any], tid: str, member_name: str | None) -> str:
    """Builds C#-like context from Cecil metadata when decompiled_dir is unavailable."""
    obj = raw_entry["obj"]
    if not member_name:
        fields_preview = ", ".join(f"{f['type']} {f['name']}" for f in obj.get("fields", [])[:15])
        methods_preview = ", ".join(m["name"] for m in obj.get("methods", [])[:20])
        calls_preview = ", ".join(obj.get("calls", [])[:15])
        return (
            f"// Metadata from Assembly-CSharp.dll (no decompiled source)\n"
            f"type {tid} : {obj.get('base_type')}\n"
            f"// Fields: {fields_preview}\n"
            f"// Methods: {methods_preview}\n"
            f"// Calls: {calls_preview}"
        )
    return f"// Metadata from Assembly-CSharp.dll\n{raw_entry.get('raw_sig', tid)}"


def extract_code_snippet(
    decompiled_dir: Path | None,
    raw_entry: dict[str, Any],
    tid: str,
    member_name: str | None = None,
) -> str:
    """Reads snippet from optional decompiled_dir, falling back seamlessly to Cecil metadata."""
    if not decompiled_dir or not decompiled_dir.is_dir():
        return build_cecil_context_snippet(raw_entry, tid, member_name)

    top_class = tid.split("+")[0].split(".")[-1]
    cs_file = decompiled_dir / f"{top_class}.cs"
    if not cs_file.is_file():
        matches = list(decompiled_dir.rglob(f"{top_class}.cs"))
        if matches:
            cs_file = matches[0]
        else:
            return build_cecil_context_snippet(raw_entry, tid, member_name)

    try:
        lines = cs_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return build_cecil_context_snippet(raw_entry, tid, member_name)

    if not member_name:
        return "\n".join(lines[:60])

    clean_member = member_name.replace("#ctor", top_class).replace("#cctor", top_class)
    for idx, line in enumerate(lines):
        if re.search(rf"\b{re.escape(clean_member)}\b", line):
            return "\n".join(lines[max(0, idx - 5) : min(len(lines), idx + 35)])

    return build_cecil_context_snippet(raw_entry, tid, member_name)


def generate_breaking_changes_report(
    removed_symbols: list[dict[str, Any]],
    stale_symbols: list[dict[str, Any]],
    new_symbols: list[dict[str, Any]],
    harmony_targets: set[tuple[str, str]],
    output_path: Path,
) -> None:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# Отчет об обратной несовместимости и изменениях сборки (Breaking Changes)",
        "",
        f"- **Дата формирования:** `{now_str}`",
        f"- **Удаленных символов (REMOVED):** `{len(removed_symbols)}`",
        f"- **Изменившихся символов (STALE):** `{len(stale_symbols)}`",
        f"- **Новых символов (NEW):** `{len(new_symbols)}`",
        "",
    ]

    affected_patches: list[tuple[dict[str, Any], str]] = []
    for status_label, bucket in (("REMOVED", removed_symbols), ("STALE", stale_symbols)):
        for sym in bucket:
            if sym["kind"] == "method":
                m = re.match(r"^M:(.+?)\.([^.(]+)\(", sym["key"])
                if m:
                    full_type = m.group(1)
                    short_type = full_type.split(".")[-1].split("+")[-1]
                    method_name = m.group(2)
                    if (full_type, method_name) in harmony_targets or (short_type, method_name) in harmony_targets:
                        affected_patches.append((sym, status_label))

    if affected_patches:
        lines.append("## КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ: Затронуты Harmony-патчи в `src/`")
        lines.append("")
        for sym, status in affected_patches:
            lines.append(f"- **[{status}]** `{sym['key']}` (прежнее описание: *{sym.get('summary', 'нет')}*)")
        lines.append("")
    else:
        lines.append("## Совместимость с Harmony-патчами мода (`src/`)")
        lines.append("Активные патчи из `src/` не затронуты изменениями сборки.")
        lines.append("")

    if removed_symbols:
        lines.extend(["## 1. Удаленные символы (`REMOVED`)", "| Ключ символа | Тип | Прежнее назначение | Исходный файл |", "| :--- | :---: | :--- | :--- |"])
        for sym in sorted(removed_symbols, key=lambda s: s["key"]):
            summary = sym.get("summary", "—").replace("|", "\\|")
            lines.append(f"| `{sym['key']}` | {sym['kind']} | {summary} | `{sym.get('file', '—')}` |")
        lines.append("")
    else:
        lines.extend(["## 1. Удаленные символы (`REMOVED`): отсутствуют (0)", ""])

    if stale_symbols:
        lines.extend(["## 2. Изменившиеся сигнатуры (`STALE`)", "| Ключ символа | Тип | Старый хэш | Новый хэш | Новая сигнатура |", "| :--- | :---: | :---: | :---: | :--- |"])
        for sym in sorted(stale_symbols, key=lambda s: s["key"]):
            lines.append(f"| `{sym['key']}` | {sym['kind']} | `{sym['old_hash']}` | `{sym['new_hash']}` | `{sym.get('new_sig', '—')}` |")
        lines.append("")
    else:
        lines.extend(["## 2. Изменившиеся сигнатуры (`STALE`): отсутствуют (0)", ""])

    if new_symbols:
        lines.extend(["## 3. Новые символы (`NEW`)", f"Всего обнаружено `{len(new_symbols)}` новых символов:"])
        for sym in sorted(new_symbols, key=lambda s: s["key"])[:40]:
            lines.append(f"- `{sym['key']}` ({sym['kind']})")
        if len(new_symbols) > 40:
            lines.append(f"- *... и еще {len(new_symbols) - 40} символов.*")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def _build_raw_symbols_index(raw_types: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str, int], list[dict[str, Any]]]]:
    raw_symbols_map: dict[str, dict[str, Any]] = {}
    raw_methods_by_name: dict[tuple[str, str, int], list[dict[str, Any]]] = {}

    for tid, tinfo in raw_types.items():
        top_file = f"{tid.split('+')[0].split('.')[-1]}.cs"
        raw_symbols_map[f"T:{tid}"] = {
            "key": f"T:{tid}",
            "tid": tid,
            "sig_hash": tinfo["sig_hash"],
            "raw_sig": f"{tinfo['name']} ({top_file})",
            "file": top_file,
            "kind": "type",
            "obj": tinfo,
        }

        for fld in tinfo.get("fields", []):
            f_key = fld["key"]
            raw_symbols_map[f_key] = {
                "key": f_key,
                "tid": tid,
                "sig_hash": fld["sig_hash"],
                "raw_sig": f"{fld['type']} {fld['name']}",
                "file": top_file,
                "kind": "field",
                "obj": fld,
            }

        for prop in tinfo.get("properties", []):
            p_key = prop["key"]
            raw_symbols_map[p_key] = {
                "key": p_key,
                "tid": tid,
                "sig_hash": prop["sig_hash"],
                "raw_sig": f"{prop['type']} {prop['name']} {{ get; set; }}",
                "file": top_file,
                "kind": "property",
                "obj": prop,
            }

        for method in tinfo.get("methods", []):
            m_key = method["key"]
            raw_symbols_map[m_key] = {
                "key": m_key,
                "tid": tid,
                "sig_hash": method["sig_hash"],
                "raw_sig": m_key[2:],
                "file": top_file,
                "kind": "method",
                "obj": method,
            }
            pcount = len(method.get("parameters", []))
            raw_methods_by_name.setdefault((tid, method["name"], pcount), []).append(method)

    return raw_symbols_map, raw_methods_by_name


def synchronize(
    symbols_path: Path,
    cache_path: Path,
    breaking_changes_path: Path,
    *,
    decompiled_dir: Path | None = None,
    src_dir: Path | None = None,
    game_name: str = "Unity Game",
    lm_studio_url: str = DEFAULT_LM_STUDIO_URL,
    model_name: str | None = None,
    dry_run: bool = False,
    no_ai: bool = False,
    stats_only: bool = False,
    max_ai_items: int = 50,
    include_members: bool = True,
    prune_removed: bool = False,
    enrich_unai: bool = False,
) -> dict[str, int]:
    """Runs symbol diffing, optional AI enrichment, cache update, and breaking-changes report."""
    if not symbols_path.is_file():
        raise FileNotFoundError(f"Файл символов не найден: {symbols_path}")

    symbols_data = json.loads(symbols_path.read_text(encoding="utf-8-sig"))
    raw_types: dict[str, dict[str, Any]] = symbols_data.get("types", {})

    if cache_path.is_file():
        cache_data: dict[str, dict[str, Any]] = json.loads(cache_path.read_text(encoding="utf-8-sig"))
    else:
        cache_data = {}

    all_ids, simple_map = _build_type_lookup(raw_types)
    raw_symbols_map, raw_methods_by_name = _build_raw_symbols_index(raw_types)
    harmony_targets = get_active_harmony_targets(src_dir)

    synced_items: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    stale_items: list[dict[str, Any]] = []
    removed_items: list[dict[str, Any]] = []
    new_items: list[dict[str, Any]] = []
    matched_new_keys: set[str] = set()

    for c_key, c_val in cache_data.items():
        c_hash = c_val.get("sig_hash", "")
        if c_key.startswith("T:"):
            kind = "type"
        elif c_key.startswith("P:"):
            kind = "property"
        elif c_key.startswith("F:"):
            kind = "field"
        else:
            kind = "method"

        # Support legacy F:Type.Prop keys from older caches when P:Type.Prop exists in symbols_raw
        lookup_key = c_key
        if lookup_key not in raw_symbols_map and c_key.startswith("F:"):
            p_equiv = "P:" + c_key[2:]
            if p_equiv in raw_symbols_map:
                lookup_key = p_equiv

        if lookup_key in raw_symbols_map:
            raw_entry = raw_symbols_map[lookup_key]
            if c_hash == raw_entry["sig_hash"]:
                synced_items.append((c_key, c_val, raw_entry))
            else:
                stale_items.append(
                    {
                        "key": c_key,
                        "kind": kind,
                        "old_hash": c_hash,
                        "new_hash": raw_entry["sig_hash"],
                        "new_key": raw_entry["key"] if lookup_key != c_key else None,
                        "new_sig": raw_entry["raw_sig"],
                        "cache_entry": c_val,
                        "raw_entry": raw_entry,
                    }
                )
            matched_new_keys.add(raw_entry["key"])
            continue

        if kind == "method":
            m_match = re.match(r"^M:(.+?)\.([^.(]+)\((.*)\)$", c_key)
            if m_match:
                tid, mname, params_str = m_match.group(1), m_match.group(2), m_match.group(3)
                pcount = len(params_str.split(",")) if params_str.strip() else 0
                candidates = raw_methods_by_name.get((tid, mname, pcount), [])
                if len(candidates) == 1:
                    cand = candidates[0]
                    matched_new_keys.add(cand["key"])
                    stale_items.append(
                        {
                            "key": c_key,
                            "kind": "method",
                            "old_hash": c_hash,
                            "new_hash": cand["sig_hash"],
                            "new_key": cand["key"],
                            "new_sig": cand["key"][2:],
                            "cache_entry": c_val,
                            "raw_entry": raw_symbols_map[cand["key"]],
                        }
                    )
                    continue

        removed_items.append(
            {
                "key": c_key,
                "kind": kind,
                "old_hash": c_hash,
                "summary": c_val.get("summary", ""),
                "file": c_val.get("file", ""),
                "cache_entry": c_val,
            }
        )

    for r_key, r_val in raw_symbols_map.items():
        if r_key in cache_data or r_key in matched_new_keys:
            continue
        if r_val["kind"] == "type" or include_members:
            new_items.append({"key": r_key, "kind": r_val["kind"], "raw_entry": r_val})

    print("=" * 60)
    print("  РЕЗУЛЬТАТЫ СИНХРОНИЗАЦИИ С БАЗОЙ ЗНАНИЙ")
    print("=" * 60)
    print(f"  Синхронизировано (SYNCED):   {len(synced_items):>5}")
    print(f"  Изменилось (STALE):          {len(stale_items):>5}")
    print(f"  Удалено (REMOVED):           {len(removed_items):>5}")
    print(f"  Новых символов (NEW):        {len(new_items):>5}")
    print("=" * 60)

    if not stats_only:
        generate_breaking_changes_report(removed_items, stale_items, new_items, harmony_targets, breaking_changes_path)

    if stats_only or dry_run:
        return {
            "synced": len(synced_items),
            "stale": len(stale_items),
            "removed": len(removed_items),
            "new": len(new_items),
        }

    # Build AI queue (prioritize types first, then members)
    ai_queue: list[tuple[str, dict[str, Any]]] = []
    if not no_ai:
        for item in sorted(new_items, key=lambda x: (0 if x["kind"] == "type" else 1, x["key"])):
            ai_queue.append(("NEW", item))
        for item in stale_items:
            ai_queue.append(("STALE", item))
        if enrich_unai:
            for c_key, c_val, raw_entry in synced_items:
                if not c_val.get("ai_enriched", True):
                    ai_queue.append(("ENRICH", {"key": c_key, "kind": raw_entry["kind"], "raw_entry": raw_entry}))

    active_model: str | None = None
    if ai_queue and not no_ai:
        active_model = model_name or detect_lm_studio_model(lm_studio_url)
        if not active_model:
            print(f"[Предупреждение] LM Studio недоступен на {lm_studio_url}. Записи сохраняются с автоописаниями.", file=sys.stderr)

    ai_processed_keys: set[str] = set()
    if active_model and ai_queue:
        for status, item in ai_queue[: max(0, max_ai_items)]:
            key = item["key"]
            raw_entry = item["raw_entry"]
            tid = raw_entry["tid"]
            member_name = None if raw_entry["kind"] == "type" else raw_entry["obj"]["name"]
            snippet = extract_code_snippet(decompiled_dir, raw_entry, tid, member_name)
            prompt = (
                f"Проанализируй фрагмент C# для Unity-игры {game_name}, символ `{key}`:\n\n"
                f"```csharp\n{snippet}\n```\n\n"
                f"Опиши кратко назначение `{key}` в 1 предложении и укажи [Связи: Type1, Type2]."
            )
            ans = query_lm_studio(prompt, lm_studio_url, active_model)
            if not ans:
                continue

            final_key = item.get("new_key") or key
            if final_key != key:
                cache_data.pop(key, None)
            text_rels = extract_relations_from_text(ans, tid, all_ids, simple_map)
            il_rels = compute_type_relations_from_symbols(tid, raw_types[tid], all_ids, simple_map) if item["kind"] == "type" else []
            cache_data[final_key] = {
                "sig_hash": raw_entry["sig_hash"],
                "status": "SYNCED",
                "ai_enriched": True,
                "summary": clean_summary_text(ans),
                "relations": sorted(set(text_rels) | set(il_rels)),
                "raw_sig": raw_entry.get("raw_sig", final_key[2:]),
                "file": raw_entry["file"],
            }
            ai_processed_keys.add(key)

    # Seed remaining NEW and STALE entries deterministically without AI
    for item in new_items:
        key = item["key"]
        if key in ai_processed_keys:
            continue
        raw_entry = item["raw_entry"]
        tid = raw_entry["tid"]
        rels = compute_type_relations_from_symbols(tid, raw_types[tid], all_ids, simple_map) if item["kind"] == "type" else []
        cache_data[key] = {
            "sig_hash": raw_entry["sig_hash"],
            "status": "SYNCED",
            "ai_enriched": False,
            "summary": build_fallback_summary(raw_entry["kind"], raw_entry["obj"]),
            "relations": rels,
            "raw_sig": raw_entry.get("raw_sig", key[2:]),
            "file": raw_entry["file"],
        }

    for st in stale_items:
        key = st["key"]
        if key in ai_processed_keys:
            continue
        raw_entry = st["raw_entry"]
        final_key = st.get("new_key") or key
        if final_key != key:
            cache_data.pop(key, None)
        tid = raw_entry["tid"]
        rels = compute_type_relations_from_symbols(tid, raw_types[tid], all_ids, simple_map) if st["kind"] == "type" else []
        cache_data[final_key] = {
            "sig_hash": raw_entry["sig_hash"],
            "status": "SYNCED",
            "ai_enriched": False,
            "summary": build_fallback_summary(raw_entry["kind"], raw_entry["obj"]),
            "relations": rels,
            "raw_sig": raw_entry.get("raw_sig", final_key[2:]),
            "file": raw_entry["file"],
        }

    for c_key, c_val, raw_entry in synced_items:
        c_val["status"] = "SYNCED"
        if raw_entry["kind"] == "type":
            il_rels = compute_type_relations_from_symbols(raw_entry["tid"], raw_types[raw_entry["tid"]], all_ids, simple_map)
            existing_rels = filter_relations_against_symbols(c_val.get("relations", []), raw_entry["tid"], all_ids, simple_map)
            c_val["relations"] = sorted(set(existing_rels) | set(il_rels))

    for rem in removed_items:
        if prune_removed:
            cache_data.pop(rem["key"], None)
        else:
            rem["cache_entry"]["status"] = "REMOVED"

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[sync_descriptions] Семантический кэш обновлён: {cache_path}")

    return {
        "synced": len(synced_items),
        "stale": len(stale_items),
        "removed": len(removed_items),
        "new": len(new_items),
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Descriptions Synchronizer & Breaking Changes Detector (UUMC)")
    parser.add_argument("--profile", type=Path, default=None, help="Опциональный путь к uumc.profile.yaml")
    parser.add_argument("--symbols", type=Path, default=None, help="Путь к symbols_raw.json")
    parser.add_argument("--cache", type=Path, default=None, help="Путь к descriptions_cache.json (создаётся при отсутствии)")
    parser.add_argument("--decompiled", type=Path, default=None, help="Опциональный путь к decompiled/")
    parser.add_argument("--breaking-changes", type=Path, default=None, help="Путь к breaking_changes.md")
    parser.add_argument("--src", type=Path, default=None, help="Путь к src/ мода для проверки [HarmonyPatch]")
    parser.add_argument("--game-name", type=str, default=None, help="Название игры для AI-промпта")
    parser.add_argument("--lm-studio-url", type=str, default=None, help="URL LM Studio API")
    parser.add_argument("--model", type=str, default=None, help="Имя модели LM Studio")
    parser.add_argument("--dry-run", action="store_true", help="Анализ без изменения кэша и запросов к LLM")
    parser.add_argument("--no-ai", action="store_true", help="Не отправлять запросы в LLM (только дифф и автозаполнение)")
    parser.add_argument("--max-ai-items", type=int, default=50, help="Максимум элементов для LLM за раз")
    parser.add_argument("--types-only", action="store_true", help="Инициализировать в кэш только типы (без полей/методов)")
    parser.add_argument("--prune-removed", action="store_true", help="Удалять из кэша ключи со статусом REMOVED")
    parser.add_argument("--enrich-unai", action="store_true", help="Дообогатить через LLM записи с ai_enriched=false")
    parser.add_argument("--stats-only", action="store_true", help="Только вывести статистику без записи файлов")
    args = parser.parse_args()

    base_dir = Path.cwd()
    profile_data: dict[str, Any] = {}
    try:
        if args.profile is not None:
            p_path = args.profile.resolve()
            profile_data = load_profile(p_path)
            base_dir = p_path.parent

        game_cfg = get_profile_section(profile_data, "game")
        mod_cfg = get_profile_section(profile_data, "mod")
        idx_cfg = get_profile_section(profile_data, "indexer")

        symbols_path = args.symbols.resolve() if args.symbols else (
            resolve_profile_path(base_dir, idx_cfg.get("symbols_file")) or (base_dir / "symbols_raw.json")
        )
        cache_path = args.cache.resolve() if args.cache else (
            resolve_profile_path(base_dir, idx_cfg.get("cache_file")) or (base_dir / "descriptions_cache.json")
        )
        breaking_path = args.breaking_changes.resolve() if args.breaking_changes else (
            resolve_profile_path(base_dir, idx_cfg.get("breaking_changes_file")) or (base_dir / "breaking_changes.md")
        )
        decompiled_dir = args.decompiled.resolve() if args.decompiled else resolve_profile_path(base_dir, game_cfg.get("decompiled_dir"))
        src_dir = args.src.resolve() if args.src else resolve_profile_path(base_dir, mod_cfg.get("src_dir"))
        game_name = args.game_name or game_cfg.get("name") or "Unity Game"
        lm_url = args.lm_studio_url or idx_cfg.get("lm_studio_url") or DEFAULT_LM_STUDIO_URL

        synchronize(
            symbols_path,
            cache_path,
            breaking_path,
            decompiled_dir=decompiled_dir,
            src_dir=src_dir,
            game_name=game_name,
            lm_studio_url=lm_url,
            model_name=args.model,
            dry_run=args.dry_run,
            no_ai=args.no_ai,
            stats_only=args.stats_only,
            max_ai_items=args.max_ai_items,
            include_members=not args.types_only,
            prune_removed=args.prune_removed,
            enrich_unai=args.enrich_unai,
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
