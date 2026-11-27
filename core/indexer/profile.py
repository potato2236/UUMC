#!/usr/bin/env python3
"""
Lightweight YAML subset parser and profile/root path resolver for UUMC.

Zero external dependencies (pure stdlib). Resolves UUMC_ROOT via environment
variable or script location, never requiring absolute paths in profiles.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

PROFILE_FILENAME = "uumc.profile.yaml"
LOCAL_CONFIG_FILENAME = ".uumc.local.yaml"


def find_workspace_root(start_dir: Path | None = None) -> Path | None:
    """Walks upward from start_dir (default: cwd) to locate uumc.profile.yaml."""
    current = (start_dir or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / PROFILE_FILENAME).is_file():
            return candidate
    return None


def load_local_config(workspace_dir: Path | None) -> dict[str, Any]:
    """Loads optional .uumc.local.yaml from workspace_dir; returns {} if absent."""
    if workspace_dir is None:
        return {}
    local_path = workspace_dir.resolve() / LOCAL_CONFIG_FILENAME
    if not local_path.is_file():
        return {}
    return parse_yaml_file(local_path)


def resolve_uumc_root(
    workspace_dir: Path | None = None,
    explicit_uumc_root: Path | str | None = None,
) -> Path:
    """Resolves UUMC root: explicit -> UUMC_ROOT env -> .uumc.local.yaml -> repo layout."""
    if explicit_uumc_root is not None and str(explicit_uumc_root).strip():
        candidate = Path(explicit_uumc_root).expanduser().resolve()
        if not candidate.is_dir():
            raise FileNotFoundError(f"Указанная папка UUMC не найдена: {candidate}")
        return candidate

    env_root = os.environ.get("UUMC_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if candidate.is_dir():
            return candidate

    ws = workspace_dir.resolve() if workspace_dir is not None else find_workspace_root()
    if ws is not None:
        local_cfg = load_local_config(ws)
        raw_local_root = local_cfg.get("uumc_root")
        if raw_local_root:
            candidate = resolve_profile_path(ws, str(raw_local_root))
            if candidate is not None and candidate.is_dir():
                return candidate

    return Path(__file__).resolve().parents[2]


def strip_yaml_comment(raw_line: str) -> str:
    """Strips # comments outside single/double quotes (handling escaped quotes)."""
    in_quote: str | None = None
    escaped = False
    for idx, ch in enumerate(raw_line):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_quote == '"':
            escaped = True
            continue
        if ch in ('"', "'"):
            if in_quote is None:
                in_quote = ch
            elif in_quote == ch:
                in_quote = None
        elif ch == "#" and in_quote is None and (idx == 0 or raw_line[idx - 1].isspace()):
            return raw_line[:idx].rstrip()
    return raw_line.rstrip()


def _split_inline_list(inner: str, src: Path, line_no: int) -> list[Any]:
    items: list[str] = []
    buf: list[str] = []
    in_quote: str | None = None
    escaped = False
    brace_depth = 0

    for ch in inner:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\" and in_quote == '"':
            buf.append(ch)
            escaped = True
            continue
        if ch in ('"', "'"):
            if in_quote is None:
                in_quote = ch
            elif in_quote == ch:
                in_quote = None
            buf.append(ch)
            continue
        if in_quote is None:
            if ch in "{[":
                brace_depth += 1
            elif ch in "}]":
                brace_depth -= 1
                if brace_depth < 0:
                    raise ValueError(f"{src}:{line_no}: лишняя закрывающая скобка в списке")
            elif ch == "," and brace_depth == 0:
                token = "".join(buf).strip()
                if token:
                    items.append(token)
                buf = []
                continue
        buf.append(ch)

    if in_quote is not None or brace_depth != 0:
        raise ValueError(f"{src}:{line_no}: незакрытая строка или скобка в inline-списке")

    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return [_parse_scalar(it, src, line_no) for it in items]


def _parse_scalar(raw_val: str, src: Path | None = None, line_no: int = 0) -> Any:
    val = raw_val.strip()
    if not val or val.lower() in ("null", "none", "~"):
        return None
    if val[0] in ('"', "'"):
        quote = val[0]
        if len(val) == 1 or val[-1] != quote:
            loc = f"{src}:{line_no}: " if src else ""
            raise ValueError(f"{loc}незакрытая кавычка в скаляре '{val}'")
        inner = val[1:-1]
        if quote == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    low = val.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


def _split_key_value(stripped: str) -> tuple[str, str | None]:
    in_quote: str | None = None
    escaped = False
    for idx, ch in enumerate(stripped):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_quote == '"':
            escaped = True
            continue
        if ch in ('"', "'"):
            if in_quote is None:
                in_quote = ch
            elif in_quote == ch:
                in_quote = None
        elif ch == ":" and in_quote is None:
            if idx + 1 == len(stripped) or stripped[idx + 1].isspace():
                key = stripped[:idx].strip().strip("\"'")
                rest = stripped[idx + 1 :].strip()
                return key, (rest if rest else None)
    return stripped, None


def parse_yaml_file(yaml_path: Path) -> dict[str, Any]:
    """Parses nested YAML mappings and lists into Python dicts/lists."""
    if not yaml_path.is_file():
        raise FileNotFoundError(f"YAML файл не найден: {yaml_path}")

    lines = yaml_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    tokens: list[tuple[int, int, str]] = []
    for line_no, raw_line in enumerate(lines, start=1):
        cleaned = strip_yaml_comment(raw_line)
        if not cleaned.strip():
            continue
        leading = cleaned[: len(cleaned) - len(cleaned.lstrip())]
        if "\t" in leading:
            raise ValueError(f"{yaml_path}:{line_no}: табуляция в отступах YAML запрещена")
        indent = len(leading)
        tokens.append((line_no, indent, cleaned.strip()))

    def parse_block(idx: int) -> tuple[Any, int]:
        if idx >= len(tokens):
            return {}, idx
        _, first_indent, first_text = tokens[idx]
        if first_text.startswith("- "):
            return parse_list(idx, first_indent)
        return parse_dict(idx, first_indent)

    def parse_list(idx: int, list_indent: int) -> tuple[list[Any], int]:
        result: list[Any] = []
        while idx < len(tokens):
            line_no, indent, text = tokens[idx]
            if indent < list_indent or not text.startswith("- "):
                break
            if indent > list_indent:
                raise ValueError(f"{yaml_path}:{line_no}: неожиданный отступ элемента списка")
            item_text = text[2:].strip()
            if item_text.startswith("["):
                if not item_text.endswith("]"):
                    raise ValueError(f"{yaml_path}:{line_no}: незакрытый inline-список")
                result.append(_split_inline_list(item_text[1:-1], yaml_path, line_no))
                idx += 1
            else:
                result.append(_parse_scalar(item_text, yaml_path, line_no))
                idx += 1
        return result, idx

    def parse_dict(idx: int, dict_indent: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        while idx < len(tokens):
            line_no, indent, text = tokens[idx]
            if indent < dict_indent:
                break
            if indent > dict_indent:
                raise ValueError(f"{yaml_path}:{line_no}: неожиданный отступ ключа '{text}'")
            if text.startswith("- "):
                break
            key, inline_val = _split_key_value(text)
            if inline_val is None and not text.endswith(":"):
                raise ValueError(f"{yaml_path}:{line_no}: некорректная YAML строка '{text}'")
            if key in result:
                raise ValueError(f"{yaml_path}:{line_no}: дублирующийся ключ YAML '{key}'")

            idx += 1
            if inline_val is not None:
                if inline_val.startswith("["):
                    if not inline_val.endswith("]"):
                        raise ValueError(f"{yaml_path}:{line_no}: незакрытый список у ключа '{key}'")
                    result[key] = _split_inline_list(inline_val[1:-1], yaml_path, line_no)
                elif inline_val == "{}":
                    result[key] = {}
                else:
                    result[key] = _parse_scalar(inline_val, yaml_path, line_no)
            else:
                if idx < len(tokens) and (
                    tokens[idx][1] > indent or (tokens[idx][1] == indent and tokens[idx][2].startswith("- "))
                ):
                    nested, idx = parse_block(idx)
                    result[key] = nested
                else:
                    result[key] = {}
        return result, idx

    if not tokens:
        return {}
    parsed, _ = parse_block(0)
    if not isinstance(parsed, dict):
        raise ValueError(f"{yaml_path}: корневой элемент должен быть словарём")
    return parsed


def load_profile(profile_path: Path) -> dict[str, Any]:
    """Loads and validates uumc.profile.yaml."""
    resolved = profile_path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Файл профиля не найден: {resolved}")
    return parse_yaml_file(resolved)


def get_profile_section(profile_data: dict[str, Any], section: str) -> dict[str, Any]:
    """Safely fetches a dict section from parsed profile, treating null/missing as {}."""
    val = profile_data.get(section)
    if val is None:
        return {}
    if not isinstance(val, dict):
        raise ValueError(f"Секция профиля '{section}' должна быть словарём")
    return val


def resolve_profile_path(base_dir: Path, raw_path: str | None) -> Path | None:
    """Resolves an optional relative or explicit path against base_dir."""
    if not raw_path:
        return None
    candidate = Path(raw_path).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (base_dir / candidate).resolve()


def resolve_game_dir(
    *,
    explicit_game_dir: Path | str | None = None,
    workspace_dir: Path | None = None,
    profile_data: dict[str, Any] | None = None,
    profile_dir: Path | None = None,
    local_config: dict[str, Any] | None = None,
) -> Path | None:
    """Resolves game_dir using priority: explicit CLI -> UUMC_GAME_DIR -> .uumc.local.yaml -> profile."""
    if explicit_game_dir is not None and str(explicit_game_dir).strip():
        return Path(explicit_game_dir).expanduser().resolve()

    env_game = os.environ.get("UUMC_GAME_DIR", "").strip()
    if env_game:
        return Path(env_game).expanduser().resolve()

    ws_base = (workspace_dir or profile_dir or Path.cwd()).resolve()
    local_cfg = local_config if local_config is not None else load_local_config(workspace_dir or profile_dir)
    raw_local_game = local_cfg.get("game_dir")
    if raw_local_game:
        return resolve_profile_path(ws_base, str(raw_local_game))

    if profile_data:
        game_cfg = get_profile_section(profile_data, "game")
        raw_profile_game = game_cfg.get("game_dir")
        if raw_profile_game:
            prof_base = (profile_dir or ws_base).resolve()
            return resolve_profile_path(prof_base, str(raw_profile_game))

    return None


def sync_devmcp_port(game_dir: Path, port: int) -> Path:
    """Creates or updates [Server] Port = <port> in BepInEx/config/com.uumc.devmcp.cfg."""
    if isinstance(port, bool):
        raise ValueError(f"Некорректный порт DevMCP: {port} (ожидается целое число 1..65535)")
    try:
        port_int = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Некорректный порт DevMCP: {port} (ожидается целое число 1..65535)") from exc
    if not (1 <= port_int <= 65535):
        raise ValueError(f"Некорректный порт DevMCP: {port_int} (ожидается 1..65535)")

    cfg_path = game_dir.resolve() / "BepInEx" / "config" / "com.uumc.devmcp.cfg"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    port_line = f"Port = {port_int}"

    if not cfg_path.is_file():
        cfg_path.write_text(f"[Server]\n{port_line}\n", encoding="utf-8")
        return cfg_path

    lines = cfg_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    section_re = re.compile(r"^\s*\[([^\]]+)\]\s*(?:[#;].*)?$")
    port_re = re.compile(r"^\s*Port\s*=", re.IGNORECASE)

    in_server = False
    server_found = False
    server_header_idx = -1
    port_updated = False
    out_lines: list[str] = []

    for line in lines:
        m_sec = section_re.match(line)
        if m_sec:
            if in_server and not port_updated:
                out_lines.append(port_line)
                port_updated = True
            sec_name = m_sec.group(1).strip().lower()
            in_server = sec_name == "server"
            if in_server:
                server_found = True
                server_header_idx = len(out_lines)
            out_lines.append(line)
            continue

        if in_server and port_re.match(line):
            if not port_updated:
                out_lines.append(port_line)
                port_updated = True
            continue

        out_lines.append(line)

    if in_server and not port_updated:
        out_lines.append(port_line)
        port_updated = True
    elif not server_found:
        if out_lines and out_lines[-1].strip():
            out_lines.append("")
        out_lines.extend(["[Server]", port_line])
    elif not port_updated and server_header_idx >= 0:
        out_lines.insert(server_header_idx + 1, port_line)

    cfg_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return cfg_path
