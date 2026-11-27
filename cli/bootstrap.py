#!/usr/bin/env python3
"""
Game inspection and workspace bootstrapper for UUMC.

Inspects a Unity game folder (Mono vs IL2CPP, BepInEx 5, HideManagerGameObject),
generates a portable uumc.profile.yaml (without absolute paths to UUMC),
scaffolds src/ and .agents/, optionally runs initial indexing, and validates
the generated workspace with check_paths + check_rules.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

try:
    from core.indexer.profile import (
        LOCAL_CONFIG_FILENAME,
        PROFILE_FILENAME,
        get_profile_section,
        load_local_config,
        load_profile,
        resolve_game_dir,
        resolve_profile_path,
        resolve_uumc_root,
        sync_devmcp_port,
    )
    from core.indexer.validate_taxonomy import parse_taxonomy_yaml
    from core.testing.runner import find_game_exe, kill_process_by_exe_name
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core.indexer.profile import (
        LOCAL_CONFIG_FILENAME,
        PROFILE_FILENAME,
        get_profile_section,
        load_local_config,
        load_profile,
        resolve_game_dir,
        resolve_profile_path,
        resolve_uumc_root,
        sync_devmcp_port,
    )
    from core.indexer.validate_taxonomy import parse_taxonomy_yaml
    from core.testing.runner import find_game_exe, kill_process_by_exe_name

try:
    from core.linters import check_links, check_paths, check_rules
except ImportError:
    check_links = None  # type: ignore[assignment]
    check_paths = None  # type: ignore[assignment]
    check_rules = None  # type: ignore[assignment]

VALID_REPORT_MODES = ("off", "local", "inbox")
DEFAULT_REPORT_MODE = "off"


@dataclass
class GameInspection:
    game_dir: Path
    game_name: str
    exe_name: str
    runtime: str  # "mono" | "il2cpp" | "unknown"
    unity_version: str
    data_dir: Path | None
    managed_dir: Path | None
    assembly_csharp: Path | None
    has_bepinex: bool
    has_cecil: bool
    hide_manager_game_object: bool | None
    warnings: list[str]


def _detect_unity_version(data_dir: Path | None) -> str:
    if data_dir is None:
        return "unknown"
    for fname in ("globalgamemanagers", "mainData", "data.unity3d"):
        fpath = data_dir / fname
        if not fpath.is_file():
            continue
        try:
            chunk = fpath.read_bytes()[:8192].decode("ascii", errors="ignore")
            m = re.search(r"\b(?:20\d{2}|6000)\.\d+\.\d+[a-z]\d+\b", chunk)
            if m:
                return m.group(0)
        except OSError:
            pass
    return "unknown"


def _read_hide_manager_setting(bepinex_cfg: Path) -> bool | None:
    if not bepinex_cfg.is_file():
        return None
    for raw in bepinex_cfg.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        stripped = raw.strip()
        if stripped.startswith(("#", ";")) or "=" not in stripped:
            continue
        key, val = stripped.split("=", 1)
        if key.strip() == "HideManagerGameObject":
            low = re.split(r"[;#]", val, maxsplit=1)[0].strip().lower()
            if low == "true":
                return True
            if low == "false":
                return False
    return None


def inspect_game_dir(game_dir: Path) -> GameInspection:
    """Inspects a Unity game folder for runtime type, assemblies, and BepInEx state."""
    resolved = game_dir.resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"Директория игры не найдена: {resolved}")

    warnings: list[str] = []
    exes = sorted(p for p in resolved.glob("*.exe") if "UnityCrashHandler" not in p.name)
    exe_name = exes[0].name if exes else f"{resolved.name}.exe"
    game_name = exes[0].stem if exes else resolved.name

    data_dirs = sorted(p for p in resolved.glob("*_Data") if p.is_dir())
    data_dir = data_dirs[0] if data_dirs else None
    managed_dir = (data_dir / "Managed") if (data_dir and (data_dir / "Managed").is_dir()) else None
    assembly_csharp = (managed_dir / "Assembly-CSharp.dll") if (managed_dir and (managed_dir / "Assembly-CSharp.dll").is_file()) else None

    has_il2cpp_bin = (resolved / "GameAssembly.dll").is_file()
    has_il2cpp_data = bool(data_dir and (data_dir / "il2cpp_data").is_dir())
    has_mono_dir = (resolved / "MonoBleedingEdge").is_dir() or (resolved / "Mono").is_dir()

    if (has_il2cpp_bin or has_il2cpp_data) and assembly_csharp is None:
        runtime = "il2cpp"
        warnings.append(
            "Обнаружена сборка Unity IL2CPP (GameAssembly.dll / il2cpp_data). "
            "На старте UUMC поддерживает только Mono + BepInEx 5; индексация Assembly-CSharp.dll и DevMCP потребуют BepInEx 6 / Interop."
        )
    elif assembly_csharp is not None or has_mono_dir:
        runtime = "mono"
    else:
        runtime = "unknown"
        warnings.append("Не удалось однозначно определить рантайм Unity (отсутствуют *_Data/Managed/Assembly-CSharp.dll и GameAssembly.dll).")

    bep_core = resolved / "BepInEx" / "core"
    has_bepinex = (bep_core / "BepInEx.dll").is_file() and (bep_core / "0Harmony.dll").is_file()
    has_cecil = (bep_core / "Mono.Cecil.dll").is_file()
    if not has_bepinex:
        warnings.append("BepInEx 5 не обнаружен в <game_dir>/BepInEx/core/ — установите BepInEx 5 перед сборкой и запуском модов.")

    hide_manager = _read_hide_manager_setting(resolved / "BepInEx" / "config" / "BepInEx.cfg")
    if hide_manager is False:
        warnings.append(
            "В BepInEx/config/BepInEx.cfg параметр HideManagerGameObject = false. "
            "Рекомендуется выставить true, чтобы сцена не уничтожала ManagerObject при загрузке."
        )

    return GameInspection(
        game_dir=resolved,
        game_name=game_name,
        exe_name=exe_name,
        runtime=runtime,
        unity_version=_detect_unity_version(data_dir),
        data_dir=data_dir,
        managed_dir=managed_dir,
        assembly_csharp=assembly_csharp,
        has_bepinex=has_bepinex,
        has_cecil=has_cecil,
        hide_manager_game_object=hide_manager,
        warnings=warnings,
    )


def _compute_relative_posix(target_dir: Path, game_dir: Path) -> str | None:
    try:
        rel = os.path.relpath(game_dir, target_dir)
    except ValueError:
        return None
    posix = rel.replace("\\", "/")
    if len(posix) >= 2 and posix[1] == ":":
        return None
    return posix


def _escape_quoted_string(value: str) -> str:
    """Escapes backslashes, double quotes, and control chars for YAML/C#/Python string literals."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )


def _render_template(
    template_path: Path,
    replacements: dict[str, str],
    *,
    quoted_keys: tuple[str, ...] = (),
    xml_keys: tuple[str, ...] = (),
) -> str:
    text = template_path.read_text(encoding="utf-8")
    quoted_set = set(quoted_keys)
    xml_set = set(xml_keys)
    for key, val in replacements.items():
        rendered = val
        if key in xml_set:
            rendered = xml_escape(rendered)
        elif key in quoted_set:
            rendered = _escape_quoted_string(rendered)
        text = text.replace(f"{{{{{key}}}}}", rendered)
    return text


def _build_routing_table(taxonomy_path: Path | None) -> str:
    if not taxonomy_path or not taxonomy_path.is_file():
        return (
            "| Область | Целевой справочник |\n"
            "|---|---|\n"
            "| Все типы игры (машинный реестр) | `docs/game-internals/INDEX-LLM.md` |\n"
            "| Обзор подсистем и синглтонов | `docs/game-internals/README.md` |"
        )
    taxonomy = parse_taxonomy_yaml(taxonomy_path)
    rows = ["| Подсистема / Раздел | Справочник (`docs/game-internals/`) | Типов |", "|---|---|---:|"]
    for folder, files in sorted(taxonomy.items()):
        for fn, types_list in sorted(files.items()):
            rel = f"docs/game-internals/{folder}/{fn}"
            rows.append(f"| `{folder}/{fn}` | `{rel}` | {len(types_list)} |")
    return "\n".join(rows)


def _safe_identifier(raw: str, fallback: str = "MyUnityMod") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", raw)
    if not cleaned or cleaned[0].isdigit():
        return fallback
    return cleaned


def _find_foreign_plugin_dlls(game_dir: Path, mod_assembly_name: str | None = None) -> list[str]:
    """Returns DLLs inside BepInEx/plugins outside Uumc.DevMcp.dll and <ModName>/."""
    plugins_dir = game_dir.resolve() / "BepInEx" / "plugins"
    if not plugins_dir.is_dir():
        return []
    mod_key = mod_assembly_name.strip().lower() if mod_assembly_name else ""
    foreign: list[str] = []
    for dll in sorted(plugins_dir.rglob("*.dll")):
        rel = dll.relative_to(plugins_dir)
        rel_posix = rel.as_posix()
        if rel_posix.lower() == "uumc.devmcp.dll":
            continue
        if mod_key and len(rel.parts) >= 2 and rel.parts[0].lower() == mod_key:
            continue
        foreign.append(rel_posix)
    return foreign


def warn_foreign_plugin_dlls(game_dir: Path, mod_assembly_name: str | None = None) -> list[str]:
    """Warns if BepInEx/plugins contains DLLs outside Uumc.DevMcp.dll and <ModName>/."""
    foreign = _find_foreign_plugin_dlls(game_dir, mod_assembly_name)
    if foreign:
        plugins_dir = game_dir.resolve() / "BepInEx" / "plugins"
        preview = ", ".join(foreign[:5]) + (" ..." if len(foreign) > 5 else "")
        print(
            f"[WARN] В {plugins_dir} обнаружены посторонние DLL ({preview}). "
            "Для изоляции модов рекомендуется отдельная Dev-копия игры на каждый мод."
        )
    return foreign


def _write_local_props(src_dir: Path, game_dir: Path) -> Path:
    props_path = src_dir / "Directory.Build.Local.props"
    content = (
        "<Project>\n"
        "  <PropertyGroup>\n"
        f"    <UumcGameDir>{xml_escape(str(game_dir.resolve()))}</UumcGameDir>\n"
        "  </PropertyGroup>\n"
        "</Project>\n"
    )
    if not props_path.is_file() or props_path.read_text(encoding="utf-8") != content:
        props_path.write_text(content, encoding="utf-8")
    return props_path


def _validate_single_segment_name(raw_name: str, field_name: str) -> str:
    """Validates that a configured project/assembly name is a single safe filename segment."""
    name = raw_name.strip()
    if (
        not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or ":" in name
        or Path(name).is_absolute()
        or any(ord(ch) < 32 for ch in name)
    ):
        raise ValueError(f"Некорректное имя '{raw_name}' в поле '{field_name}': ожидается односегментное имя без путей.")
    return name


def _resolve_workspace_subdir(workspace: Path, raw_rel: str, field_name: str) -> Path:
    """Resolves a relative subdirectory strictly inside workspace (no rooted or escaping paths)."""
    cleaned = raw_rel.strip()
    if not cleaned:
        raise ValueError(f"Путь '{field_name}' не может быть пустым.")
    if Path(cleaned).is_absolute() or (len(cleaned) >= 2 and cleaned[1] == ":"):
        raise ValueError(f"{field_name} должен быть относительным путём внутри рабочей папки: '{raw_rel}'")
    resolved = (workspace / cleaned).resolve()
    if not resolved.is_relative_to(workspace.resolve()):
        raise ValueError(f"{field_name} выходит за пределы рабочей папки: {raw_rel}")
    return resolved


def validate_report_mode(raw_mode: str) -> str:
    """Validates and normalizes bug reporting mode ('off', 'local', or 'inbox')."""
    cleaned = raw_mode.strip().lower()
    if cleaned not in VALID_REPORT_MODES:
        raise ValueError(
            f"Недопустимый режим отчётов '{raw_mode}'; допустимо: {', '.join(VALID_REPORT_MODES)}."
        )
    return cleaned


def resolve_report_mode(workspace_dir: Path | None, uumc_root: Path) -> str:
    """Resolves effective report mode from .uumc.local.yaml, or inbox in dev repo, else off."""
    if workspace_dir is not None:
        local_cfg = load_local_config(workspace_dir)
        raw_mode = local_cfg.get("report_mode")
        if isinstance(raw_mode, str) and raw_mode.strip().lower() in VALID_REPORT_MODES:
            return raw_mode.strip().lower()
    if (uumc_root / "backlog" / "inbox").is_dir():
        return "inbox"
    return DEFAULT_REPORT_MODE


def write_local_binding(
    workspace_dir: Path,
    game_dir: Path,
    uumc_root: Path,
    *,
    src_dir_rel: str = "src",
    python_exe: str | None = None,
    report_mode: str | None = None,
) -> dict[str, Path]:
    """Generates .uumc.local.yaml, src/Directory.Build.Local.props, and uumc.cmd."""
    workspace = workspace_dir.resolve()
    resolved_game = game_dir.resolve()
    resolved_uumc = uumc_root.resolve()
    py_exe = str(python_exe or sys.executable)

    if report_mode is None:
        existing_local = load_local_config(workspace)
        raw_existing = existing_local.get("report_mode")
        if isinstance(raw_existing, str) and raw_existing.strip().lower() in VALID_REPORT_MODES:
            effective_report_mode = raw_existing.strip().lower()
        else:
            effective_report_mode = DEFAULT_REPORT_MODE
    else:
        effective_report_mode = validate_report_mode(report_mode)

    src_dir = _resolve_workspace_subdir(workspace, src_dir_rel, "src_dir")
    src_dir.mkdir(parents=True, exist_ok=True)

    local_yaml_path = workspace / LOCAL_CONFIG_FILENAME
    local_yaml_text = (
        "# Локальная привязка рабочей среды UUMC к этой машине (в .gitignore)\n"
        f'uumc_root: "{_escape_quoted_string(str(resolved_uumc))}"\n'
        f'game_dir: "{_escape_quoted_string(str(resolved_game))}"\n'
        f'python_exe: "{_escape_quoted_string(py_exe)}"\n'
        f'report_mode: "{effective_report_mode}"\n'
    )
    local_yaml_path.write_text(local_yaml_text, encoding="utf-8")

    local_props_path = _write_local_props(src_dir, resolved_game)

    uumc_py_cmd = str((resolved_uumc / "cli" / "uumc.py").resolve()).replace("%", "%%")
    py_cmd = py_exe.replace("%", "%%")
    cmd_path = workspace / "uumc.cmd"
    cmd_text = f'@echo off\r\n"{py_cmd}" "{uumc_py_cmd}" --workspace "%~dp0." %*\r\n'
    cmd_path.write_bytes(cmd_text.encode("utf-8"))

    return {
        "local_yaml": local_yaml_path,
        "local_props": local_props_path,
        "cmd_launcher": cmd_path,
    }


def sync_local_props_from_config(workspace_dir: Path, profile_data: dict[str, Any] | None = None) -> Path | None:
    """Syncs src/Directory.Build.Local.props from .uumc.local.yaml if game_dir is configured."""
    ws = workspace_dir.resolve()
    local_cfg = load_local_config(ws)
    raw_game = local_cfg.get("game_dir")
    if not raw_game:
        return None
    resolved_game = resolve_profile_path(ws, str(raw_game))
    if resolved_game is None:
        return None

    mod_cfg = get_profile_section(profile_data, "mod") if profile_data else {}
    src_rel = str(mod_cfg.get("src_dir") or "src")
    src_dir = _resolve_workspace_subdir(ws, src_rel, "mod.src_dir")
    src_dir.mkdir(parents=True, exist_ok=True)
    return _write_local_props(src_dir, resolved_game)


def _resolve_devmcp_source(uumc_root: Path) -> tuple[Path | None, Path]:
    """Returns (csproj_or_none, fallback_dll_path) or raises FileNotFoundError."""
    csproj = uumc_root / "core" / "dev-mcp" / "Uumc.DevMcp.csproj"
    prebuilt = uumc_root / "core" / "dev-mcp" / "bin" / "Release" / "Uumc.DevMcp.dll"
    alt_prebuilt = uumc_root / "bin" / "Uumc.DevMcp.dll"
    if csproj.is_file():
        return csproj, prebuilt
    if prebuilt.is_file():
        return None, prebuilt
    if alt_prebuilt.is_file():
        return None, alt_prebuilt
    raise FileNotFoundError(f"Исходники или предсобранная DLL Uumc.DevMcp не найдены в {uumc_root}")


def _deploy_devmcp(uumc_root: Path, info: GameInspection, *, port: int = 27015) -> None:
    if not info.has_bepinex:
        raise FileNotFoundError(
            f"BepInEx 5 не обнаружен в {info.game_dir / 'BepInEx' / 'core'}; установка Uumc.DevMcp невозможна."
        )
    plugins_dir = info.game_dir / "BepInEx" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    csproj, source_dll = _resolve_devmcp_source(uumc_root)

    if csproj is not None:
        res = subprocess.run(
            ["dotnet", "build", str(csproj), "-c", "Release", f"-p:UumcGameDir={info.game_dir}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if res.returncode != 0:
            raise RuntimeError(f"Не удалось собрать Uumc.DevMcp:\n{res.stdout}\n{res.stderr}")

    if not source_dll.is_file():
        raise FileNotFoundError(f"Собранная DLL не найдена: {source_dll}")
    shutil.copy2(source_dll, plugins_dir / "Uumc.DevMcp.dll")
    sync_devmcp_port(info.game_dir, port)
    print(f"[bootstrap] Uumc.DevMcp.dll скопирован в: {plugins_dir / 'Uumc.DevMcp.dll'}")


def resolve_cecil_command(uumc_root: Path, forward_args: list[str]) -> list[str]:
    """Builds command to run CecilExtractor from source .csproj or prebuilt binary."""
    cecil_proj = uumc_root / "core" / "indexer" / "CecilExtractor" / "CecilExtractor.csproj"
    if cecil_proj.is_file():
        return ["dotnet", "run", "--project", str(cecil_proj), "--", *forward_args]

    for rel in (
        Path("bin/CecilExtractor.exe"),
        Path("core/indexer/CecilExtractor/bin/Release/net8.0/CecilExtractor.exe"),
    ):
        cand = uumc_root / rel
        if cand.is_file():
            return [str(cand), *forward_args]

    for rel in (
        Path("bin/CecilExtractor.dll"),
        Path("core/indexer/CecilExtractor/bin/Release/net8.0/CecilExtractor.dll"),
    ):
        cand = uumc_root / rel
        if cand.is_file():
            return ["dotnet", str(cand), *forward_args]

    raise FileNotFoundError(f"CecilExtractor не найден в {uumc_root}")


def _run_initial_indexing(uumc_root: Path, target_dir: Path, profile_path: Path, info: GameInspection) -> None:
    if info.assembly_csharp is None or not info.assembly_csharp.is_file():
        print("[WARN] Пропуск индексации: Assembly-CSharp.dll не найдена.")
        return

    symbols_out = target_dir / "tools" / "game-indexer" / "symbols_raw.json"
    env = dict(os.environ)
    env["UUMC_ROOT"] = str(uumc_root)

    steps = [
        resolve_cecil_command(
            uumc_root,
            ["--assembly", str(info.assembly_csharp), "--output", str(symbols_out)],
        ),
        [sys.executable, str(uumc_root / "core" / "indexer" / "sync_descriptions.py"), "--profile", str(profile_path), "--no-ai"],
        [sys.executable, str(uumc_root / "core" / "indexer" / "validate_taxonomy.py"), "--profile", str(profile_path), "--init"],
        [sys.executable, str(uumc_root / "core" / "indexer" / "validate_taxonomy.py"), "--profile", str(profile_path)],
        [sys.executable, str(uumc_root / "core" / "indexer" / "render_docs.py"), "--profile", str(profile_path)],
    ]
    for cmd in steps:
        proc = subprocess.run(cmd, cwd=target_dir, env=env)
        if proc.returncode != 0:
            raise RuntimeError(f"Ошибка первичной индексации на команде: {' '.join(cmd)}")


def bootstrap_workspace(
    game_dir: Path,
    target_dir: Path,
    *,
    mod_name: str | None = None,
    plugin_guid: str | None = None,
    run_index: bool = False,
    deploy_devmcp: bool = False,
    with_enricher: bool = False,
    report_mode: str = DEFAULT_REPORT_MODE,
    force: bool = False,
    uumc_root: Path | None = None,
) -> dict[str, object]:
    """Bootstraps a target Unity modding workspace from UUMC templates."""
    clean_report_mode = validate_report_mode(report_mode)
    root = (uumc_root or resolve_uumc_root()).resolve()
    templates_dir = root / "templates"
    if not templates_dir.is_dir():
        raise FileNotFoundError(f"Папка шаблонов UUMC не найдена: {templates_dir}")
    if clean_report_mode == "inbox" and not (root / "backlog" / "inbox").is_dir():
        raise ValueError(
            "Режим отчётов 'inbox' недоступен в пользовательской поставке UUMC; используйте 'local' или 'off'."
        )

    info = inspect_game_dir(game_dir)
    for w in info.warnings:
        print(f"[WARN] {w}")

    target = target_dir.resolve()
    profile_path = target / PROFILE_FILENAME
    if profile_path.exists() and not force:
        raise FileExistsError(f"Профиль уже существует: {profile_path}. Используйте --force для перезаписи.")

    safe_mod = _safe_identifier(mod_name or f"{info.game_name}Mod", "MyUnityMod")
    guid = f"com.uumc.{safe_mod.lower()}" if plugin_guid is None else plugin_guid.strip()
    if not guid:
        raise ValueError("GUID плагина (--guid) не может быть пустым.")

    if deploy_devmcp and info.runtime != "il2cpp":
        if not info.has_bepinex:
            raise FileNotFoundError(
                f"BepInEx 5 не обнаружен в {info.game_dir / 'BepInEx' / 'core'}; установка Uumc.DevMcp невозможна."
            )
        _resolve_devmcp_source(root)
    if run_index and info.runtime != "il2cpp" and info.assembly_csharp is not None and info.assembly_csharp.is_file():
        resolve_cecil_command(root, [])

    target.mkdir(parents=True, exist_ok=True)
    rel_game = _compute_relative_posix(target, info.game_dir)
    csproj_name = f"{safe_mod}.csproj"

    foreign_plugins = warn_foreign_plugin_dlls(info.game_dir, safe_mod)

    is_same_dir = rel_game == "."
    is_external = rel_game is not None and (rel_game == ".." or rel_game.startswith("../"))

    # Three-zone isolation: uumc.profile.yaml always stores game_dir: null;
    # machine-local paths are stored in .uumc.local.yaml and src/Directory.Build.Local.props.
    game_dir_yaml = "null"
    game_dir_props = ""

    if is_same_dir:
        game_dir_gitignore = "BepInEx/plugins/\nBepInEx/LogOutput.log"
        game_dir_exclude = "BepInEx"
    elif rel_game is not None and not is_external:
        game_dir_gitignore = f"{rel_game}/"
        game_dir_exclude = rel_game
    else:
        game_dir_gitignore = f"# Папка игры задаётся в .uumc.local.yaml / UUMC_GAME_DIR ({info.game_dir.name})"
        game_dir_exclude = info.game_dir.name
    deploy_dest = f"BepInEx/plugins/{safe_mod}/"

    hide_str = "true" if info.hide_manager_game_object is True else ("false" if info.hide_manager_game_object is False else "true")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    replacements = {
        "GAME_NAME": info.game_name,
        "GAME_EXE_NAME": info.exe_name,
        "UNITY_RUNTIME": info.runtime,
        "UNITY_VERSION": info.unity_version,
        "GAME_DIR_REL": game_dir_yaml,
        "GAME_DIR_PROPS": game_dir_props,
        "GAME_DIR_NAME": game_dir_exclude,
        "GAME_DIR_GITIGNORE": game_dir_gitignore,
        "GAME_DIR_DEPLOY_PLUGINS": deploy_dest,
        "HIDE_MANAGER": hide_str,
        "MOD_PROJECT_NAME": safe_mod,
        "MOD_ASSEMBLY_NAME": safe_mod,
        "MOD_NAMESPACE": safe_mod,
        "MOD_CSPROJ": csproj_name,
        "PLUGIN_GUID": guid,
        "ENRICHER_SCRIPT": '"tools/game-indexer/enrich_custom.py"' if with_enricher else "null",
        "INIT_TIMESTAMP": now_str,
        "TAXONOMY_ROUTING_TABLE": _build_routing_table(None),
    }

    # 1. Profile (quote game_dir only if non-null)
    profile_text = _render_template(
        templates_dir / "game-profile.schema.yaml",
        replacements,
        quoted_keys=("GAME_NAME", "GAME_EXE_NAME", "GAME_DIR_REL", "PLUGIN_GUID", "GAME_DIR_NAME"),
    )
    if game_dir_yaml == "null":
        profile_text = profile_text.replace('game_dir: "null"', "game_dir: null")
    profile_path.write_text(profile_text, encoding="utf-8")

    # 2. Gitignore and C# mod skeleton
    (target / ".gitignore").write_text(_render_template(templates_dir / "mod-project" / "gitignore.tmpl", replacements), encoding="utf-8")
    src_dir = target / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "Directory.Build.props").write_text(
        _render_template(
            templates_dir / "mod-project" / "Directory.Build.props.tmpl",
            replacements,
            xml_keys=("GAME_DIR_PROPS",),
        ),
        encoding="utf-8",
    )
    (src_dir / csproj_name).write_text(
        _render_template(
            templates_dir / "mod-project" / "Mod.csproj.tmpl",
            replacements,
            xml_keys=("MOD_ASSEMBLY_NAME", "MOD_NAMESPACE"),
        ),
        encoding="utf-8",
    )
    (src_dir / "Plugin.cs").write_text(
        _render_template(
            templates_dir / "mod-project" / "Plugin.cs.tmpl",
            replacements,
            quoted_keys=("PLUGIN_GUID", "MOD_PROJECT_NAME"),
        ),
        encoding="utf-8",
    )
    (src_dir / "ARCHITECTURE.md").write_text(
        _render_template(templates_dir / "mod-project" / "ARCHITECTURE.md.tmpl", replacements), encoding="utf-8"
    )

    tests_dir = target / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    smoke_example_path = tests_dir / "test_smoke_example.py"
    if force or not smoke_example_path.exists():
        smoke_example_path.write_text(
            _render_template(templates_dir / "mod-project" / "test_smoke_example.py.tmpl", replacements),
            encoding="utf-8",
        )

    if with_enricher:
        enricher_path = target / "tools" / "game-indexer" / "enrich_custom.py"
        enricher_path.parent.mkdir(parents=True, exist_ok=True)
        enricher_path.write_text(
            _render_template(
                templates_dir / "runtime-enricher.py.tmpl",
                replacements,
                quoted_keys=("GAME_NAME",),
            ),
            encoding="utf-8",
        )

    # 2b. Local machine binding (.uumc.local.yaml, src/Directory.Build.Local.props, uumc.cmd)
    local_files = write_local_binding(target, info.game_dir, root, src_dir_rel="src", report_mode=clean_report_mode)

    # 3. Optional DevMCP deploy and initial indexing
    devmcp_deployed = False
    if deploy_devmcp:
        if info.runtime == "il2cpp":
            print("[WARN] Пропуск --deploy-devmcp: целевая игра использует IL2CPP, а Uumc.DevMcp рассчитан на Mono/BepInEx 5.")
        else:
            _deploy_devmcp(root, info, port=27015)
            devmcp_deployed = True

    indexed = False
    if run_index:
        if info.runtime == "il2cpp":
            print("[WARN] Пропуск --index: обнаружен IL2CPP без Managed/Assembly-CSharp.dll.")
        else:
            _run_initial_indexing(root, target, profile_path, info)
            indexed = True

    # 4. Agent contour (refresh routing table if taxonomy was generated)
    tax_path = target / "tools" / "game-indexer" / "taxonomy.yaml"
    replacements["TAXONOMY_ROUTING_TABLE"] = _build_routing_table(tax_path)

    contour_dir = templates_dir / "agent-contour"
    (target / "AGENTS.md").write_text(_render_template(contour_dir / "AGENTS.md.tmpl", replacements), encoding="utf-8")

    agents_dir = target / ".agents"
    rules_dir = agents_dir / "rules"
    skills_dir = agents_dir / "skills"
    rules_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(parents=True, exist_ok=True)

    for rule_name in ("modding-csharp.md", "game-internals.md"):
        (rules_dir / rule_name).write_text(
            _render_template(contour_dir / "rules" / f"{rule_name}.tmpl", replacements), encoding="utf-8"
        )
    feedback_rule_path = rules_dir / "uumc-feedback.md"
    if clean_report_mode != "off":
        feedback_rule_path.write_text(
            _render_template(contour_dir / "rules" / "uumc-feedback.md.tmpl", replacements), encoding="utf-8"
        )
    elif feedback_rule_path.is_file():
        feedback_rule_path.unlink()

    for skill_name in ("build-and-test", "first-run"):
        skill_dir = skills_dir / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            _render_template(contour_dir / "skills" / skill_name / "SKILL.md.tmpl", replacements), encoding="utf-8"
        )

    # 5. Self-validate generated workspace when dev linters are available
    if check_paths is not None and check_rules is not None and check_links is not None:
        exclusions = check_paths.resolve_exclusions(target, profile_path, [])
        _, path_violations = check_paths.run_lint(target, exclusions)
        rule_errors, _ = check_rules.run_check(target, exclusions, 10, require_registry=False)
        link_errors, _, _ = check_links.run_check(target, exclusions, profile_path=profile_path)
        if path_violations or rule_errors or link_errors:
            details = [f"{p}:{ln} {msg}" for p, ln, _, msg in path_violations] + rule_errors + link_errors
            raise RuntimeError("Сгенерированное пространство не прошло проверку линтеров:\n" + "\n".join(details))

    return {
        "target": str(target),
        "profile": str(profile_path),
        "local_yaml": str(local_files["local_yaml"]),
        "local_props": str(local_files["local_props"]),
        "cmd_launcher": str(local_files["cmd_launcher"]),
        "runtime": info.runtime,
        "unity_version": info.unity_version,
        "has_bepinex": info.has_bepinex,
        "hide_manager_game_object": info.hide_manager_game_object,
        "devmcp_deployed": devmcp_deployed,
        "indexed": indexed,
        "report_mode": clean_report_mode,
        "foreign_plugins": foreign_plugins,
        "warnings": info.warnings,
    }


def link_workspace(
    game_dir: Path,
    workspace_dir: Path,
    *,
    deploy_devmcp: bool = False,
    uumc_root: Path | None = None,
) -> dict[str, object]:
    """Re-creates machine-local binding files in an existing mod workspace without touching sources."""
    root = (uumc_root or resolve_uumc_root()).resolve()
    ws = workspace_dir.resolve()
    profile_path = ws / PROFILE_FILENAME
    if not profile_path.is_file():
        raise FileNotFoundError(
            f"Профиль {PROFILE_FILENAME} не найден в {ws}. Укажите --workspace или создайте проект через `uumc init`."
        )

    profile_data = load_profile(profile_path)
    mod_cfg = get_profile_section(profile_data, "mod")
    mcp_cfg = get_profile_section(profile_data, "dev_mcp")

    info = inspect_game_dir(game_dir)
    for w in info.warnings:
        print(f"[WARN] {w}")

    mod_asm = str(mod_cfg.get("assembly_name") or mod_cfg.get("project_name") or "").strip() or None
    src_dir_rel = str(mod_cfg.get("src_dir") or "src")
    warn_foreign_plugin_dlls(info.game_dir, mod_asm)

    local_files = write_local_binding(ws, info.game_dir, root, src_dir_rel=src_dir_rel)

    if deploy_devmcp:
        if info.runtime == "il2cpp":
            print("[WARN] Пропуск --deploy-devmcp: целевая игра использует IL2CPP, а Uumc.DevMcp рассчитан на Mono/BepInEx 5.")
        else:
            port = int(mcp_cfg.get("port") or 27015)
            _deploy_devmcp(root, info, port=port)

    return {
        "workspace": str(ws),
        "game_dir": str(info.game_dir),
        "local_yaml": str(local_files["local_yaml"]),
        "local_props": str(local_files["local_props"]),
        "cmd_launcher": str(local_files["cmd_launcher"]),
        "runtime": info.runtime,
        "warnings": info.warnings,
    }


def _find_mod_csproj(src_dir: Path, mod_cfg: dict[str, Any]) -> Path:
    resolved_src = src_dir.resolve()
    for key in ("project_name", "assembly_name"):
        raw = str(mod_cfg.get(key) or "").strip()
        if raw:
            name = _validate_single_segment_name(raw, f"mod.{key}")
            cand = (resolved_src / f"{name}.csproj").resolve()
            if not cand.is_relative_to(resolved_src):
                raise ValueError(f"Некорректный путь .csproj для mod.{key}: {raw}")
            if cand.is_file():
                return cand
    found = sorted(resolved_src.glob("*.csproj"))
    if len(found) == 1:
        return found[0]
    if not found:
        raise FileNotFoundError(f"Файл .csproj не найден в {resolved_src}")
    raise ValueError(f"В {resolved_src} найдено несколько .csproj ({', '.join(p.name for p in found)}); укажите mod.project_name в профиле.")


def build_and_deploy_mod(
    workspace_dir: Path,
    *,
    profile_path: Path | None = None,
    explicit_game_dir: Path | None = None,
    configuration: str = "Release",
    kill_existing: bool = False,
    deploy: bool = True,
) -> dict[str, object]:
    """Syncs Directory.Build.Local.props, builds mod .csproj, and deploys to BepInEx/plugins/<ModName>/."""
    ws = workspace_dir.resolve()
    prof = (profile_path or (ws / PROFILE_FILENAME)).resolve()
    if not prof.is_file():
        raise FileNotFoundError(f"Файл профиля не найден: {prof}")

    profile_data = load_profile(prof)
    game_cfg = get_profile_section(profile_data, "game")
    mod_cfg = get_profile_section(profile_data, "mod")
    mcp_cfg = get_profile_section(profile_data, "dev_mcp")

    game_dir = resolve_game_dir(
        explicit_game_dir=explicit_game_dir,
        workspace_dir=ws,
        profile_data=profile_data,
        profile_dir=prof.parent,
    )
    if game_dir is None:
        raise ValueError(
            'Не удалось определить папку игры (game_dir). Выполните `uumc link --game-dir "<путь>"` или передайте `--game-dir`.'
        )
    if not game_dir.is_dir():
        raise FileNotFoundError(f"Директория игры не найдена: {game_dir}")

    sync_local_props_from_config(ws, profile_data)

    src_rel = str(mod_cfg.get("src_dir") or "src")
    src_dir = _resolve_workspace_subdir(prof.parent, src_rel, "mod.src_dir")
    csproj_path = _find_mod_csproj(src_dir, mod_cfg)
    raw_assembly = str(mod_cfg.get("assembly_name") or mod_cfg.get("project_name") or csproj_path.stem)
    assembly_name = _validate_single_segment_name(raw_assembly, "mod.assembly_name")

    if kill_existing and deploy:
        exe_name = game_cfg.get("exe_name")
        exe_path = find_game_exe(game_dir, str(exe_name) if exe_name else None)
        if exe_path is not None:
            kill_process_by_exe_name(exe_path.name)
            time.sleep(0.5)

    cmd = ["dotnet", "build", str(csproj_path), "-c", configuration]
    if explicit_game_dir is not None:
        cmd.append(f"-p:UumcGameDir={game_dir}")

    proc = subprocess.run(cmd, cwd=prof.parent)
    if proc.returncode != 0:
        raise RuntimeError(f"Сборка {csproj_path.name} завершилась с ошибкой (код {proc.returncode}).")

    deployed_dll: Path | None = None
    if deploy:
        bin_dir = src_dir / "bin" / configuration
        cand_dll = (bin_dir / f"{assembly_name}.dll").resolve()
        if not cand_dll.is_file() and bin_dir.is_dir():
            matches = sorted(bin_dir.rglob(f"{assembly_name}.dll"))
            if matches:
                cand_dll = matches[0].resolve()
        if not cand_dll.is_file():
            raise FileNotFoundError(f"Собранная DLL мода не найдена: {cand_dll}")

        plugins_root = (game_dir / "BepInEx" / "plugins").resolve()
        dest_dir = (plugins_root / assembly_name).resolve()
        if dest_dir == plugins_root or not dest_dir.is_relative_to(plugins_root):
            raise ValueError(f"Путь деплоя должен находиться строго в подпапке BepInEx/plugins/<ModName>/: {dest_dir}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        deployed_dll = (dest_dir / f"{assembly_name}.dll").resolve()
        if not deployed_dll.is_relative_to(dest_dir):
            raise ValueError(f"Некорректный путь целевой DLL: {deployed_dll}")
        try:
            shutil.copy2(cand_dll, deployed_dll)
        except OSError as exc:
            raise RuntimeError(
                f"Не удалось скопировать {cand_dll.name} в {deployed_dll}: "
                "файл заблокирован запущенным процессом игры. Закройте игру или используйте `--kill-existing`."
            ) from exc

        raw_port = mcp_cfg.get("port")
        if raw_port is not None:
            sync_devmcp_port(game_dir, int(raw_port))

    return {
        "csproj": str(csproj_path),
        "assembly_name": assembly_name,
        "game_dir": str(game_dir),
        "deployed_dll": str(deployed_dll) if deployed_dll else None,
    }


def _detect_dotnet_sdk() -> tuple[bool, str | None]:
    dotnet_bin = shutil.which("dotnet")
    if not dotnet_bin:
        return False, None
    try:
        proc = subprocess.run(
            [dotnet_bin, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except OSError:
        return False, None
    if proc.returncode != 0:
        return False, None
    version = proc.stdout.strip().splitlines()[0].strip() if proc.stdout.strip() else "installed"
    return True, version


def _make_check_item(
    name: str,
    status: str,
    summary: str,
    *,
    fix_hint: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": name,
        "status": status,  # "ok" | "warn" | "fail" | "unsupported"
        "summary": summary,
    }
    if fix_hint:
        item["fix_hint"] = fix_hint
    return item


def diagnose_environment(
    *,
    workspace_dir: Path | None = None,
    profile_path: Path | None = None,
    explicit_game_dir: Path | None = None,
    uumc_root: Path | None = None,
) -> dict[str, Any]:
    """Diagnoses UUMC prerequisites, target game compatibility, and mod workspace readiness."""
    root = (uumc_root or resolve_uumc_root(workspace_dir=workspace_dir)).resolve()
    checks: list[dict[str, Any]] = []
    tips: list[str] = []

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    checks.append(
        _make_check_item(
            "python",
            "ok" if sys.version_info >= (3, 10) else "fail",
            f"Python {py_ver}",
            fix_hint=None if sys.version_info >= (3, 10) else "Требуется Python 3.10 или новее.",
        )
    )

    has_dotnet, dotnet_ver = _detect_dotnet_sdk()
    checks.append(
        _make_check_item(
            "dotnet_sdk",
            "ok" if has_dotnet else "fail",
            f".NET SDK {dotnet_ver}" if has_dotnet else ".NET SDK не найден в PATH",
            fix_hint=None if has_dotnet else "Установите .NET SDK 8.0+ для индексации (CecilExtractor) и сборки C#-модов.",
        )
    )

    ws: Path | None = None
    prof_path: Path | None = None
    profile_data: dict[str, Any] | None = None

    if profile_path is not None:
        prof_path = profile_path.resolve()
        ws = prof_path.parent
    elif workspace_dir is not None:
        ws = workspace_dir.resolve()
        candidate_prof = ws / PROFILE_FILENAME
        if candidate_prof.is_file():
            prof_path = candidate_prof

    resolved_game: Path | None = None
    mod_asm: str | None = None

    if ws is not None:
        if prof_path is None or not prof_path.is_file():
            checks.append(
                _make_check_item(
                    "workspace_profile",
                    "fail",
                    f"Не найден {PROFILE_FILENAME} в {ws}",
                    fix_hint=f'Выполните: python "{root / "cli" / "uumc.py"}" init --game-dir "<путь к игре>" --target "{ws}" --full',
                )
            )
        else:
            try:
                profile_data = load_profile(prof_path)
                checks.append(_make_check_item("workspace_profile", "ok", f"Профиль загружен ({prof_path.name})"))
            except (FileNotFoundError, ValueError) as exc:
                checks.append(_make_check_item("workspace_profile", "fail", f"Ошибка профиля: {exc}"))

        if profile_data is not None:
            mod_cfg = get_profile_section(profile_data, "mod")
            mod_asm = str(mod_cfg.get("assembly_name") or mod_cfg.get("project_name") or "").strip() or None
            has_local = (ws / LOCAL_CONFIG_FILENAME).is_file()
            has_cmd = (ws / "uumc.cmd").is_file()
            src_rel = str(mod_cfg.get("src_dir") or "src")
            try:
                src_dir = _resolve_workspace_subdir(ws, src_rel, "mod.src_dir")
            except ValueError as exc:
                src_dir = None
                checks.append(_make_check_item("mod_src", "fail", str(exc)))

            if has_local and has_cmd:
                checks.append(_make_check_item("local_binding", "ok", "Локальная связка (.uumc.local.yaml, uumc.cmd) активна"))
            else:
                checks.append(
                    _make_check_item(
                        "local_binding",
                        "fail",
                        "Отсутствует локальная связка (.uumc.local.yaml / uumc.cmd)",
                        fix_hint=f'.\\uumc.cmd link --game-dir "<путь к игре>" (или `uumc link --workspace "{ws}" --game-dir "<путь>"`)',
                    )
                )

            if src_dir is not None:
                try:
                    csproj = _find_mod_csproj(src_dir, mod_cfg)
                    checks.append(_make_check_item("mod_csproj", "ok", f"Проект мода: {csproj.name}"))
                except (FileNotFoundError, ValueError) as exc:
                    checks.append(_make_check_item("mod_csproj", "fail", str(exc)))

            idx_cfg = get_profile_section(profile_data, "indexer")
            symbols_path = resolve_profile_path(prof_path.parent, idx_cfg.get("symbols_file"))
            docs_dir = resolve_profile_path(prof_path.parent, idx_cfg.get("docs_output_dir"))
            index_llm = (docs_dir / "INDEX-LLM.md") if docs_dir else None
            if symbols_path and symbols_path.is_file() and index_llm and index_llm.is_file():
                checks.append(_make_check_item("knowledge_base", "ok", "База знаний сгенерирована (INDEX-LLM.md готов)"))
            else:
                checks.append(
                    _make_check_item(
                        "knowledge_base",
                        "fail",
                        "Индексация игры ещё не выполнена (нет symbols_raw.json или INDEX-LLM.md)",
                        fix_hint=".\\uumc.cmd index && .\\uumc.cmd sync --no-ai && .\\uumc.cmd taxonomy --init && .\\uumc.cmd render",
                    )
                )

            test_cfg = get_profile_section(profile_data, "testing")
            if not test_cfg.get("start_scene_csharp") or not test_cfg.get("ready_check_csharp"):
                tips.append(
                    "Опционально: задайте `testing.start_scene_csharp` и `testing.ready_check_csharp` в uumc.profile.yaml для быстрого входа в тестовую сцену."
                )

            resolved_game = resolve_game_dir(
                explicit_game_dir=explicit_game_dir,
                workspace_dir=ws,
                profile_data=profile_data,
                profile_dir=prof_path.parent,
            )
    elif explicit_game_dir is not None:
        resolved_game = explicit_game_dir.expanduser().resolve()

    game_report: dict[str, Any] | None = None
    if resolved_game is None:
        if ws is not None:
            checks.append(
                _make_check_item(
                    "game_dir",
                    "fail",
                    "Не удалось определить папку игры (game_dir)",
                    fix_hint='Выполните `uumc link --game-dir "<путь к игре>"` или передайте `--game-dir`.',
                )
            )
        else:
            checks.append(
                _make_check_item(
                    "target_input",
                    "fail",
                    "Не указана ни папка игры (`--game-dir`), ни рабочее пространство мода (`--workspace`).",
                    fix_hint='Запустите `python cli/uumc.py doctor --game-dir "<путь к игре>"`.',
                )
            )
    elif not resolved_game.is_dir():
        checks.append(
            _make_check_item(
                "game_dir",
                "fail",
                f"Директория игры не найдена: {resolved_game}",
                fix_hint="Проверьте путь к папке игры или перепривяжите через `uumc link --game-dir`.",
            )
        )
    else:
        info = inspect_game_dir(resolved_game)
        game_report = {
            "game_dir": str(info.game_dir),
            "game_name": info.game_name,
            "exe_name": info.exe_name,
            "runtime": info.runtime,
            "unity_version": info.unity_version,
            "has_bepinex": info.has_bepinex,
            "hide_manager_game_object": info.hide_manager_game_object,
        }

        if info.runtime == "il2cpp":
            checks.append(
                _make_check_item(
                    "unity_runtime",
                    "unsupported",
                    f"Обнаружен Unity IL2CPP ({info.game_name}, Unity {info.unity_version}). Текущая версия UUMC поддерживает только Unity Mono + BepInEx 5.",
                    fix_hint="Эту игру нельзя модифицировать через текущий стек UUMC (требуется поддержка BepInEx 6 / Il2CppInterop).",
                )
            )
        elif info.runtime == "unknown":
            checks.append(
                _make_check_item(
                    "unity_runtime",
                    "unsupported",
                    f"Папка {info.game_dir.name} не распознана как поддерживаемая игра Unity Mono (отсутствуют *_Data/Managed/Assembly-CSharp.dll и MonoBleedingEdge).",
                    fix_hint="Убедитесь, что указан корень игры рядом с .exe; игры не на Unity Mono сейчас не поддерживаются.",
                )
            )
        else:
            checks.append(
                _make_check_item(
                    "unity_runtime",
                    "ok",
                    f"Unity Mono ({info.game_name}, версия Unity: {info.unity_version})",
                )
            )
            if info.has_bepinex:
                checks.append(_make_check_item("bepinex5", "ok", "BepInEx 5 установлен (BepInEx.dll + 0Harmony.dll)"))
            else:
                checks.append(
                    _make_check_item(
                        "bepinex5",
                        "fail",
                        "BepInEx 5 не установлен в <game_dir>/BepInEx/core/",
                        fix_hint="Установите BepInEx 5 (x64/x86 для Unity Mono) в dev-копию игры и один раз запустите игру для создания конфигов.",
                    )
                )

            if info.hide_manager_game_object is False:
                checks.append(
                    _make_check_item(
                        "bepinex_hide_manager",
                        "warn",
                        "В BepInEx/config/BepInEx.cfg задано HideManagerGameObject = false",
                        fix_hint="Рекомендуется выставить `HideManagerGameObject = true`, чтобы сцена не уничтожала ManagerObject при загрузке.",
                    )
                )
            elif info.hide_manager_game_object is None and info.has_bepinex:
                tips.append("Файл BepInEx/config/BepInEx.cfg ещё не создан — он появится после первого запуска игры с BepInEx 5.")

            devmcp_dll = info.game_dir / "BepInEx" / "plugins" / "Uumc.DevMcp.dll"
            if devmcp_dll.is_file():
                checks.append(_make_check_item("devmcp_plugin", "ok", "Uumc.DevMcp.dll установлен в BepInEx/plugins/"))
            elif ws is not None:
                checks.append(
                    _make_check_item(
                        "devmcp_plugin",
                        "fail",
                        "Uumc.DevMcp.dll не установлен в BepInEx/plugins/",
                        fix_hint=f'.\\uumc.cmd link --game-dir "{info.game_dir}" --deploy-devmcp',
                    )
                )
            else:
                tips.append("При создании проекта добавьте `--full` (или `--deploy-devmcp`), чтобы сразу установить Uumc.DevMcp.dll.")

            foreign = _find_foreign_plugin_dlls(info.game_dir, mod_asm)
            if foreign:
                preview = ", ".join(foreign[:4]) + (" ..." if len(foreign) > 4 else "")
                checks.append(
                    _make_check_item(
                        "plugin_isolation",
                        "warn",
                        f"В BepInEx/plugins обнаружены сторонние DLL ({preview})",
                        fix_hint="Для чистых тестов рекомендуется выделенная dev-копия игры под разрабатываемый мод.",
                    )
                )

    statuses = {c["status"] for c in checks}
    if "unsupported" in statuses:
        verdict = "UNSUPPORTED"
        exit_code = 2
    elif "fail" in statuses:
        verdict = "NEEDS_SETUP"
        exit_code = 1
    else:
        verdict = "READY"
        exit_code = 0

    if ws is not None:
        tips.append("Для подключения живого дебага IDE выполните: .\\uumc.cmd mcp --print-config")

    return {
        "verdict": verdict,
        "exit_code": exit_code,
        "mode": "workspace" if ws is not None else "game",
        "workspace_dir": str(ws) if ws is not None else None,
        "game": game_report,
        "checks": checks,
        "tips": tips,
    }


def format_doctor_report(report: dict[str, Any]) -> str:
    """Formats diagnose_environment output as concise human-readable Russian text."""
    verdict = report["verdict"]
    badge_map = {
        "READY": "READY — среда готова к разработке мода",
        "NEEDS_SETUP": "NEEDS_SETUP — игра поддерживается, но требуется настройка",
        "UNSUPPORTED": "UNSUPPORTED — эту игру нельзя модифицировать в текущей версии UUMC",
    }
    lines = [f"[DOCTOR] Вердикт: {badge_map.get(verdict, verdict)}"]
    if report.get("workspace_dir"):
        lines.append(f"         Проект мода: {report['workspace_dir']}")
    if report.get("game"):
        lines.append(f"         Игра: {report['game']['game_dir']}")

    tag_map = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]", "unsupported": "[STOP]"}
    for chk in report["checks"]:
        tag = tag_map.get(chk["status"], "[INFO]")
        lines.append(f"  {tag} {chk['summary']}")
        if chk.get("fix_hint"):
            lines.append(f"         -> {chk['fix_hint']}")

    for tip in report.get("tips") or ():
        lines.append(f"  [TIP]  {tip}")
    return "\n".join(lines)


def _looks_like_unity_game_dir(candidate: Path) -> bool:
    if not candidate.is_dir():
        return False
    if (candidate / "GameAssembly.dll").is_file():
        return True
    for data_dir in candidate.glob("*_Data"):
        if not data_dir.is_dir():
            continue
        if (data_dir / "Managed" / "Assembly-CSharp.dll").is_file() or (data_dir / "il2cpp_data").is_dir():
            return True
    return False


def _steam_root_candidates() -> list[Path]:
    candidates: list[Path] = []
    if os.name == "nt":
        try:
            import winreg

            for hive, subkey, val_name in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
            ):
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        raw, _ = winreg.QueryValueEx(key, val_name)
                        if isinstance(raw, str) and raw.strip():
                            candidates.append(Path(raw.strip()))
                except OSError:
                    continue
        except ImportError:
            pass
        for env_key in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(env_key, "").strip()
            if base:
                candidates.append(Path(base) / "Steam")
    else:
        home = Path.home()
        candidates.extend([home / ".local" / "share" / "Steam", home / ".steam" / "steam"])
    return candidates


def _parse_libraryfolders_vdf(vdf_path: Path) -> list[Path]:
    if not vdf_path.is_file():
        return []
    text = vdf_path.read_text(encoding="utf-8", errors="replace")
    paths: list[Path] = []
    for match in re.finditer(r'"path"\s+"([^"]+)"', text):
        raw = match.group(1).replace("\\\\", "\\").strip()
        if raw:
            paths.append(Path(raw))
    return paths


def discover_steam_games(extra_roots: list[Path] | None = None) -> list[dict[str, Any]]:
    """Discovers installed Unity games in Steam libraries and optional extra directories."""
    common_dirs: list[Path] = []
    seen_common: set[str] = set()

    def _add_common(path: Path) -> None:
        resolved = path.expanduser().resolve()
        key = os.path.normcase(str(resolved))
        if resolved.is_dir() and key not in seen_common:
            seen_common.add(key)
            common_dirs.append(resolved)

    for steam_root in _steam_root_candidates():
        if not steam_root.is_dir():
            continue
        _add_common(steam_root / "steamapps" / "common")
        for lib_root in _parse_libraryfolders_vdf(steam_root / "steamapps" / "libraryfolders.vdf"):
            _add_common(lib_root / "steamapps" / "common")

    direct_candidates: list[Path] = []
    for extra in extra_roots or ():
        resolved_extra = extra.expanduser().resolve()
        if not resolved_extra.is_dir():
            continue
        if _looks_like_unity_game_dir(resolved_extra):
            direct_candidates.append(resolved_extra)
        elif (resolved_extra / "steamapps" / "common").is_dir():
            _add_common(resolved_extra / "steamapps" / "common")
        else:
            _add_common(resolved_extra)

    game_dirs: list[Path] = list(direct_candidates)
    for common in common_dirs:
        try:
            for entry in sorted(common.iterdir()):
                if _looks_like_unity_game_dir(entry):
                    game_dirs.append(entry)
        except OSError:
            continue

    seen_games: set[str] = set()
    discovered: list[dict[str, Any]] = []
    for gdir in game_dirs:
        resolved_game = gdir.resolve()
        key = os.path.normcase(str(resolved_game))
        if key in seen_games:
            continue
        seen_games.add(key)
        try:
            info = inspect_game_dir(resolved_game)
        except (OSError, FileNotFoundError):
            continue
        if info.runtime == "unknown":
            continue
        discovered.append(
            {
                "name": info.game_name,
                "game_dir": str(info.game_dir),
                "exe_name": info.exe_name,
                "runtime": info.runtime,
                "unity_version": info.unity_version,
                "has_bepinex": info.has_bepinex,
                "supported": info.runtime == "mono",
            }
        )

    discovered.sort(key=lambda item: (not item["supported"], str(item["name"]).lower()))
    return discovered


def format_scan_report(games: list[dict[str, Any]]) -> str:
    """Formats discovered Unity games into concise human-readable text."""
    if not games:
        return (
            "[SCAN] Установленные Unity-игры в библиотеках Steam не найдены.\n"
            "       Укажите путь к папке игры вручную: uumc doctor --game-dir \"<путь к игре>\""
        )
    lines = [f"[SCAN] Найдено Unity-игр: {len(games)}"]
    for idx, item in enumerate(games, start=1):
        badge = "[OK Mono]" if item["supported"] else "[STOP IL2CPP]"
        bep = "BepInEx 5 готов" if item["has_bepinex"] else "нужен BepInEx 5"
        status_note = bep if item["supported"] else "не поддерживается"
        lines.append(
            f"  {idx}. {badge} {item['name']} (Unity {item['unity_version']}, {status_note})"
        )
        lines.append(f"     {item['game_dir']}")
    return "\n".join(lines)


def set_report_mode(
    workspace_dir: Path,
    mode: str,
    *,
    uumc_root: Path | None = None,
) -> dict[str, str]:
    """Updates report_mode in .uumc.local.yaml and syncs optional uumc-feedback rule."""
    clean_mode = validate_report_mode(mode)
    ws = workspace_dir.resolve()
    profile_path = ws / PROFILE_FILENAME
    if not profile_path.is_file():
        raise FileNotFoundError(
            f"Профиль {PROFILE_FILENAME} не найден в {ws}. Укажите --workspace или создайте проект через `uumc init`."
        )
    root = (uumc_root or resolve_uumc_root(workspace_dir=ws)).resolve()
    if clean_mode == "inbox" and not (root / "backlog" / "inbox").is_dir():
        raise ValueError(
            "Режим отчётов 'inbox' недоступен в этой поставке UUMC; используйте 'local' или 'off'."
        )

    profile_data = load_profile(profile_path)
    game_cfg = get_profile_section(profile_data, "game")
    mod_cfg = get_profile_section(profile_data, "mod")
    src_dir_rel = str(mod_cfg.get("src_dir") or "src")
    game_dir = resolve_game_dir(workspace_dir=ws, profile_data=profile_data, profile_dir=ws)
    if game_dir is None:
        raise ValueError(
            'Не удалось определить папку игры (game_dir). Выполните `uumc link --game-dir "<путь>"`.'
        )

    write_local_binding(ws, game_dir, root, src_dir_rel=src_dir_rel, report_mode=clean_mode)

    rules_dir = ws / ".agents" / "rules"
    feedback_rule = rules_dir / "uumc-feedback.md"
    feedback_tmpl = root / "templates" / "agent-contour" / "rules" / "uumc-feedback.md.tmpl"
    if clean_mode == "off":
        if feedback_rule.is_file():
            feedback_rule.unlink()
    elif feedback_tmpl.is_file():
        feedback_rule.parent.mkdir(parents=True, exist_ok=True)
        feedback_rule.write_text(
            _render_template(feedback_tmpl, {"GAME_NAME": str(game_cfg.get("name") or ws.name)}),
            encoding="utf-8",
        )

    return {"workspace": str(ws), "report_mode": clean_mode}


def create_friction_report(
    *,
    kind: str,
    title: str,
    tool: str,
    actual: str,
    expected: str,
    workaround: str | None = None,
    mode: str | None = None,
    workspace_dir: Path | None = None,
    profile_path: Path | None = None,
    uumc_root: Path | None = None,
) -> Path:
    """Writes a compact report card into reports/ (local) or dev inbox (inbox)."""
    valid_kinds = ("bug", "gap", "promote")
    clean_kind = kind.strip().lower()
    if clean_kind not in valid_kinds:
        raise ValueError(f"Недопустимый тип отчёта '{kind}'; допустимо: {', '.join(valid_kinds)}.")

    ws = workspace_dir.resolve() if workspace_dir is not None else None
    if profile_path is not None:
        prof: Path | None = profile_path.resolve()
        if not prof.is_file():
            raise FileNotFoundError(f"Файл профиля не найден: {prof}")
        if ws is None:
            ws = prof.parent
    else:
        prof = (ws / PROFILE_FILENAME) if ws is not None else None
    root = (uumc_root or resolve_uumc_root(workspace_dir=ws)).resolve()

    effective_mode = validate_report_mode(mode) if mode is not None else resolve_report_mode(ws, root)
    if effective_mode == "off":
        raise ValueError(
            "Сохранение баг-репортов отключено (report_mode=off). "
            "Чтобы включить локальное сохранение, выполните: uumc report --set-mode local"
        )

    clean_title = title.strip()
    clean_tool = tool.strip()
    clean_actual = actual.strip()
    clean_expected = expected.strip()
    if not clean_title or not clean_tool or not clean_actual or not clean_expected:
        raise ValueError("Поля --title, --tool, --actual и --expected не могут быть пустыми.")

    game_context = "не привязан (запуск вне рабочего пространства)"
    if prof is not None and prof.is_file():
        try:
            pdata = load_profile(prof)
            gcfg = get_profile_section(pdata, "game")
            gname = str(gcfg.get("name") or "unknown")
            gruntime = str(gcfg.get("runtime") or "unknown")
            gunity = str(gcfg.get("unity_version") or "unknown")
            game_context = f"{gname} (`{gruntime}`, Unity `{gunity}`)"
        except (FileNotFoundError, ValueError) as exc:
            game_context = f"ошибка чтения профиля ({exc})"

    if effective_mode == "inbox":
        target_dir = root / "backlog" / "inbox"
        if not target_dir.is_dir():
            raise ValueError(
                "Режим отчётов 'inbox' недоступен в этой поставке UUMC; используйте '--mode local' или '--set-mode local'."
            )
    else:
        base_dir = ws if ws is not None else Path.cwd().resolve()
        target_dir = base_dir / "reports"
        target_dir.mkdir(parents=True, exist_ok=True)

    slug_ascii = re.sub(r"[^a-z0-9]+", "-", clean_title.lower()).strip("-")
    if not slug_ascii:
        slug_ascii = clean_kind
    slug_ascii = slug_ascii[:40].strip("-")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    candidate = target_dir / f"{stamp}-{clean_kind}-{slug_ascii}.md"
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = target_dir / f"{stamp}-{clean_kind}-{slug_ascii}-{suffix}.md"

    wa_text = workaround.strip() if workaround and workaround.strip() else "не указан"
    card = (
        f"# [{clean_kind.upper()}] {clean_title}\n\n"
        f"- **Дата:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"- **Контекст игры:** {game_context}\n"
        f"- **Инструмент / слой:** `{clean_tool}`\n"
        f"- **Факт (Actual):** {clean_actual}\n"
        f"- **Ожидание (Expected):** {clean_expected}\n"
        f"- **Локальный обход (Workaround):** {wa_text}\n"
    )
    candidate.write_text(card, encoding="utf-8")
    return candidate
