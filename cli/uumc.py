#!/usr/bin/env python3
"""
Unified CLI orchestrator for UUMC (Universal Unity Modding Core).

Resolves UUMC root via UUMC_ROOT environment variable or script location.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from cli.bootstrap import (
        DEFAULT_REPORT_MODE,
        VALID_REPORT_MODES,
        bootstrap_workspace,
        build_and_deploy_mod,
        create_friction_report,
        diagnose_environment,
        discover_steam_games,
        format_doctor_report,
        format_scan_report,
        link_workspace,
        resolve_cecil_command,
        set_report_mode,
    )
    from core.indexer.profile import (
        LOCAL_CONFIG_FILENAME,
        PROFILE_FILENAME,
        find_workspace_root,
        get_profile_section,
        load_profile,
        resolve_game_dir,
        resolve_profile_path,
        resolve_uumc_root,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cli.bootstrap import (
        DEFAULT_REPORT_MODE,
        VALID_REPORT_MODES,
        bootstrap_workspace,
        build_and_deploy_mod,
        create_friction_report,
        diagnose_environment,
        discover_steam_games,
        format_doctor_report,
        format_scan_report,
        link_workspace,
        resolve_cecil_command,
        set_report_mode,
    )
    from core.indexer.profile import (
        LOCAL_CONFIG_FILENAME,
        PROFILE_FILENAME,
        find_workspace_root,
        get_profile_section,
        load_profile,
        resolve_game_dir,
        resolve_profile_path,
        resolve_uumc_root,
    )


def _run_python_module(
    uumc_root: Path,
    script_rel: str,
    forward_args: list[str],
    *,
    unbuffered: bool = False,
) -> int:
    env = dict(os.environ)
    env["UUMC_ROOT"] = str(uumc_root)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{uumc_root}{os.pathsep}{existing_pp}" if existing_pp else str(uumc_root)
    script_path = uumc_root / script_rel
    if not script_path.is_file():
        print(f"[ERROR] Скрипт ядра не найден: {script_path}", file=sys.stderr)
        return 1
    cmd = [sys.executable]
    if unbuffered:
        cmd.append("-u")
    cmd.extend([str(script_path), *forward_args])
    return subprocess.run(cmd, env=env).returncode


def _prompt_non_empty(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        raw = input(f"{label}{suffix}: ").strip()
        if raw:
            return raw.strip('"').strip("'")
        if default is not None:
            return default
        print("Значение не может быть пустым.", file=sys.stderr)


def _prompt_yes_no(label: str, *, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"{label} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "д", "да", "1", "true")


def _print_init_next_steps(res: dict[str, object]) -> None:
    runtime = str(res.get("runtime") or "unknown")
    target = str(res.get("target") or "")
    has_bepinex = bool(res.get("has_bepinex"))
    devmcp_deployed = bool(res.get("devmcp_deployed"))
    indexed = bool(res.get("indexed"))

    print("\nСледующие шаги:")
    if runtime in ("il2cpp", "unknown"):
        print("  [STOP] Текущая версия UUMC поддерживает только Unity Mono + BepInEx 5.")
        print(f"         Для проверки совместимости запустите: python cli/uumc.py doctor --workspace \"{target}\"")
        return

    step = 1
    if not has_bepinex:
        print(f"  {step}. Установите BepInEx 5 в dev-копию игры и один раз запустите её для создания конфигов.")
        step += 1
        print(f"  {step}. Перепривяжите среду с установкой DevMCP: uumc link --workspace \"{target}\" --game-dir \"<путь>\" --deploy-devmcp")
        step += 1
    elif not devmcp_deployed or not indexed:
        missing = []
        if not indexed:
            missing.append(".\\uumc.cmd index && .\\uumc.cmd sync --no-ai && .\\uumc.cmd taxonomy --init && .\\uumc.cmd render")
        if not devmcp_deployed:
            missing.append(".\\uumc.cmd link --game-dir \"<путь к игре>\" --deploy-devmcp")
        print(f"  {step}. Завершите настройку в папке мода ({target}): {' ; '.join(missing)}")
        step += 1

    print(f"  {step}. Откройте папку \"{target}\" в IDE и проверьте стенд командой: .\\uumc.cmd doctor")
    step += 1
    print(f"  {step}. Для живого дебага в IDE получите конфиг MCP: .\\uumc.cmd mcp --print-config")
    step += 1
    print(f"  {step}. Соберите и запустите базовый smoke-тест: .\\uumc.cmd build --kill-existing && .\\uumc.cmd test --launch --smoke --check-log")


def _handle_init(args: argparse.Namespace, uumc_root: Path) -> int:
    game_dir = args.game_dir
    target = args.target
    mod_name = args.mod_name
    run_index = bool(args.index or args.full)
    deploy_devmcp = bool(args.deploy_devmcp or args.full)
    with_enricher = bool(args.with_enricher or args.full)
    report_mode = args.report_mode

    if args.wizard:
        if game_dir is None:
            game_dir = Path(_prompt_non_empty("Путь к папке с игрой (рядом с .exe)"))
        diag = diagnose_environment(explicit_game_dir=game_dir, uumc_root=uumc_root)
        print(format_doctor_report(diag))
        if not game_dir.expanduser().resolve().is_dir():
            print(f"[ERROR] Директория игры не найдена: {game_dir}", file=sys.stderr)
            return 1
        if diag["exit_code"] == 2 and not args.force:
            print("[STOP] Инициализация остановлена: игра не поддерживается текущим стеком UUMC.", file=sys.stderr)
            return 2
        if target is None:
            default_target = str(game_dir.resolve().parent / f"{game_dir.resolve().name}Mod")
            target = Path(_prompt_non_empty("Куда создать проект мода", default_target))
        if mod_name is None:
            default_mod = f"{(diag.get('game') or {}).get('game_name', 'MyGame')}Mod"
            mod_name = _prompt_non_empty("Имя C#-мода", default_mod)
        if not args.full and not args.index and not args.deploy_devmcp:
            full_choice = _prompt_yes_no("Выполнить полную настройку (--index + --deploy-devmcp + enricher)?", default=True)
            if full_choice:
                run_index = deploy_devmcp = with_enricher = True
        if report_mode is None:
            rep_choice = _prompt_yes_no("Включить сохранение локальных баг-репортов инструмента (reports/)?", default=False)
            report_mode = "local" if rep_choice else "off"
    elif game_dir is None or target is None:
        print(
            "[ERROR] Укажите --game-dir и --target (либо запустите интерактивный мастер: `uumc init --wizard`).",
            file=sys.stderr,
        )
        return 2
    else:
        diag = diagnose_environment(explicit_game_dir=game_dir, uumc_root=uumc_root)
        if diag["exit_code"] == 2 and not args.force:
            print(format_doctor_report(diag), file=sys.stderr)
            print(
                "[STOP] Инициализация остановлена: игра не поддерживается текущим стеком UUMC (нужен Unity Mono + BepInEx 5). "
                "Передайте --force только если нужен пустой каркас без индексации и DevMCP.",
                file=sys.stderr,
            )
            return 2

    effective_report_mode = report_mode or DEFAULT_REPORT_MODE

    try:
        res = bootstrap_workspace(
            game_dir,
            target,
            mod_name=mod_name,
            plugin_guid=args.guid,
            run_index=run_index,
            deploy_devmcp=deploy_devmcp,
            with_enricher=with_enricher,
            report_mode=effective_report_mode,
            force=args.force,
            uumc_root=uumc_root,
        )
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(f"[OK] Рабочее пространство UUMC развёрнуто: {res['target']}")
    print(f"     Профиль: {res['profile']} (runtime={res['runtime']}, unity={res['unity_version']})")
    print(f"     Локальная связка: {res['local_yaml']} (report_mode={res['report_mode']})")
    _print_init_next_steps(res)
    return 0


def _handle_link(args: argparse.Namespace, uumc_root: Path, workspace_dir: Path | None) -> int:
    ws = workspace_dir or find_workspace_root(Path.cwd())
    if ws is None:
        print(
            f"[ERROR] Рабочая среда ({PROFILE_FILENAME}) не найдена. Перейдите в папку мода или укажите --workspace.",
            file=sys.stderr,
        )
        return 1
    try:
        res = link_workspace(
            args.game_dir,
            ws,
            deploy_devmcp=args.deploy_devmcp,
            uumc_root=uumc_root,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(f"[OK] Локальная связка обновлена: {res['local_yaml']}")
    print(f"     Игра: {res['game_dir']}")
    print(f"     MSBuild: {res['local_props']}")
    print(f"     Лаунчер: {res['cmd_launcher']}")
    return 0


def _handle_build(args: argparse.Namespace, workspace_dir: Path | None) -> int:
    ws = workspace_dir or (args.profile.resolve().parent if args.profile else find_workspace_root(Path.cwd()))
    if ws is None:
        print(
            f"[ERROR] Рабочая среда ({PROFILE_FILENAME}) не найдена. Перейдите в папку мода или укажите --workspace / --profile.",
            file=sys.stderr,
        )
        return 1
    try:
        res = build_and_deploy_mod(
            ws,
            profile_path=args.profile,
            explicit_game_dir=args.game_dir,
            configuration=args.configuration,
            kill_existing=args.kill_existing,
            deploy=not args.no_deploy,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(f"[OK] Сборка завершена: {res['csproj']}")
    if res["deployed_dll"]:
        print(f"     Деплой: {res['deployed_dll']}")
    return 0


def _handle_doctor(args: argparse.Namespace, uumc_root: Path, workspace_dir: Path | None) -> int:
    ws = workspace_dir or (args.profile.resolve().parent if args.profile else None)
    try:
        report = diagnose_environment(
            workspace_dir=ws,
            profile_path=args.profile,
            explicit_game_dir=args.game_dir,
            uumc_root=uumc_root,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if args.json:
        json_payload = {
            "verdict": report["verdict"],
            "exit_code": report["exit_code"],
            "mode": report["mode"],
            "workspace_dir": report["workspace_dir"],
            "game": report["game"],
            "checks": [{"name": c["name"], "status": c["status"]} for c in report["checks"]],
        }
        print(json.dumps(json_payload, ensure_ascii=True, indent=2))
    else:
        print(format_doctor_report(report))
    return int(report["exit_code"])


def _handle_scan(args: argparse.Namespace) -> int:
    for raw_dir in args.dir or ():
        if not raw_dir.expanduser().resolve().is_dir():
            print(f"[ERROR] Указанная папка для сканирования не найдена: {raw_dir}", file=sys.stderr)
            return 1
    games = discover_steam_games(extra_roots=args.dir)
    if args.json:
        print(json.dumps({"games": games}, ensure_ascii=True, indent=2))
    else:
        print(format_scan_report(games))
    return 0


def _handle_report(args: argparse.Namespace, uumc_root: Path, workspace_dir: Path | None) -> int:
    ws = workspace_dir or (args.profile.resolve().parent if args.profile else find_workspace_root(Path.cwd()))
    if args.set_mode is not None:
        if ws is None:
            print(
                f"[ERROR] Рабочая среда ({PROFILE_FILENAME}) не найдена. Перейдите в папку мода или укажите --workspace.",
                file=sys.stderr,
            )
            return 1
        try:
            res = set_report_mode(ws, args.set_mode, uumc_root=uumc_root)
        except (FileNotFoundError, ValueError) as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1
        print(f"[OK] Режим отчётов для {res['workspace']} установлен: {res['report_mode']}")
        return 0

    missing_fields = [
        flag
        for flag, val in (
            ("--title", args.title),
            ("--tool", args.tool),
            ("--actual", args.actual),
            ("--expected", args.expected),
        )
        if not val or not str(val).strip()
    ]
    if missing_fields:
        print(
            f"[ERROR] Укажите обязательные параметры: {', '.join(missing_fields)} (или используйте --set-mode).",
            file=sys.stderr,
        )
        return 2

    try:
        card_path = create_friction_report(
            kind=args.kind,
            title=args.title,
            tool=args.tool,
            actual=args.actual,
            expected=args.expected,
            workaround=args.workaround,
            mode=args.mode,
            workspace_dir=ws,
            profile_path=args.profile,
            uumc_root=uumc_root,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    base_rel = ws if (ws is not None and card_path.is_relative_to(ws)) else uumc_root
    try:
        display = card_path.relative_to(base_rel).as_posix()
    except ValueError:
        display = str(card_path)
    print(f"[OK] Отчёт сохранён: {display}")
    return 0


def _has_flag(tokens: list[str], names: tuple[str, ...]) -> bool:
    for tok in tokens:
        if tok == "--":
            break
        if tok in names or any(tok.startswith(f"{name}=") for name in names):
            return True
    return False


def _extract_flag_arg(tokens: list[str], flag_name: str, empty_err: str) -> tuple[str | None, list[str]]:
    """Extracts all occurrences of flag_name before '--' (last wins) to avoid launcher/user duplicates."""
    remaining: list[str] = []
    last_val: str | None = None
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok == "--":
            remaining.extend(tokens[idx:])
            break
        if tok == flag_name:
            if idx + 1 >= len(tokens) or not tokens[idx + 1].strip():
                raise ValueError(empty_err)
            last_val = tokens[idx + 1]
            idx += 2
            continue
        if tok.startswith(f"{flag_name}="):
            val = tok.split("=", 1)[1].strip()
            if not val:
                raise ValueError(empty_err)
            last_val = val
            idx += 1
            continue
        remaining.append(tok)
        idx += 1
    return last_val, remaining


def _extract_profile_arg(tokens: list[str]) -> tuple[str | None, list[str]]:
    return _extract_flag_arg(tokens, "--profile", "После --profile не указан путь к файлу профиля.")


def _warn_if_missing_local_binding(workspace_dir: Path | None, forward: list[str]) -> None:
    if workspace_dir is None or _has_flag(forward, ("--help", "-h")):
        return
    if (workspace_dir / PROFILE_FILENAME).is_file() and not (workspace_dir / LOCAL_CONFIG_FILENAME).is_file():
        print(
            f"[WARN] Локальная связка {LOCAL_CONFIG_FILENAME} не найдена в {workspace_dir}. "
            f'Для привязки игры выполните: uumc link --workspace "{workspace_dir}" --game-dir "<путь к игре>"',
            file=sys.stderr,
        )


def _handle_index(forward_args: list[str], uumc_root: Path, workspace_dir: Path | None = None) -> int:
    try:
        resolve_cecil_command(uumc_root, [])
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    forward = list(forward_args)
    if forward and forward[0] == "--":
        forward = forward[1:]

    # If --profile (or --profile=...) is passed to `uumc index`, resolve assembly/output unless overridden
    try:
        profile_arg, forward = _extract_profile_arg(forward)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    profile_path: Path | None = None
    if profile_arg is not None:
        profile_path = Path(profile_arg).resolve()
    elif workspace_dir is not None and (workspace_dir / PROFILE_FILENAME).is_file() and not _has_flag(forward, ("--help", "-h")):
        profile_path = (workspace_dir / PROFILE_FILENAME).resolve()

    if profile_path is not None:
        try:
            pdata = load_profile(profile_path)
        except (FileNotFoundError, ValueError) as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1

        base_dir = profile_path.parent
        ws_for_local = workspace_dir or base_dir
        _warn_if_missing_local_binding(ws_for_local, forward)
        idx_cfg = get_profile_section(pdata, "indexer")

        if not _has_flag(forward, ("--game-dir", "-g", "--assembly", "-a")):
            gdir = resolve_game_dir(workspace_dir=ws_for_local, profile_data=pdata, profile_dir=base_dir)
            if gdir is None:
                print(
                    '[ERROR] Не удалось определить папку игры (game_dir). Выполните `uumc link --game-dir "<путь>"` или передайте `--game-dir`.',
                    file=sys.stderr,
                )
                return 1
            forward.extend(["--game-dir", str(gdir)])
        if not _has_flag(forward, ("--output", "-o")):
            sym_out = resolve_profile_path(base_dir, idx_cfg.get("symbols_file")) or (base_dir / "symbols_raw.json")
            forward.extend(["--output", str(sym_out)])

    cmd = resolve_cecil_command(uumc_root, forward)
    return subprocess.run(cmd).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uumc",
        description="Единый CLI оркестратор UUMC (Universal Unity Modding Core).",
    )
    parser.add_argument("--workspace", type=Path, default=None, help="Корневая папка проекта мода")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Развернуть рабочее пространство мода под целевую Unity-игру")
    p_init.add_argument("--game-dir", type=Path, default=None, help="Папка с целевой игрой")
    p_init.add_argument("--target", type=Path, default=None, help="Целевая папка проекта мода")
    p_init.add_argument("--mod-name", type=str, default=None, help="Имя C#-проекта и сборки мода")
    p_init.add_argument("--guid", type=str, default=None, help="GUID BepInEx-плагина")
    p_init.add_argument("--full", action="store_true", help="Полная настройка: --index + --deploy-devmcp + --with-enricher")
    p_init.add_argument("--wizard", action="store_true", help="Интерактивный пошаговый мастер создания проекта")
    p_init.add_argument("--index", action="store_true", help="Выполнить первичную индексацию Assembly-CSharp.dll")
    p_init.add_argument("--deploy-devmcp", action="store_true", help="Собрать и скопировать Uumc.DevMcp.dll в BepInEx/plugins")
    p_init.add_argument("--with-enricher", action="store_true", help="Создать заготовку tools/game-indexer/enrich_custom.py")
    p_init.add_argument(
        "--report-mode",
        choices=VALID_REPORT_MODES,
        default=None,
        help="Режим баг-репортов: off (по умолчанию), local (в reports/) или inbox (dev)",
    )
    p_init.add_argument("--force", action="store_true", help="Перезаписать существующий uumc.profile.yaml")

    p_scan = sub.add_parser("scan", help="Найти установленные Unity-игры в Steam и проверить совместимость")
    p_scan.add_argument("--dir", action="append", type=Path, default=[], help="Дополнительная папка с играми для сканирования")
    p_scan.add_argument("--json", action="store_true", help="Вывести список игр в формате JSON (английские ключи)")

    p_doctor = sub.add_parser("doctor", help="Проверить совместимость игры и готовность рабочей среды UUMC")
    p_doctor.add_argument("--workspace", type=Path, default=None, help="Корневая папка проекта мода")
    p_doctor.add_argument("--profile", type=Path, default=None, help="Путь к uumc.profile.yaml")
    p_doctor.add_argument("--game-dir", type=Path, default=None, help="Папка с целевой игрой")
    p_doctor.add_argument("--json", action="store_true", help="Вывести отчёт в формате JSON (английские ключи)")

    p_report = sub.add_parser("report", help="Сохранить отчёт об ошибке инструмента или настроить режим отчётов")
    p_report.add_argument("--workspace", type=Path, default=None, help="Корневая папка проекта мода")
    p_report.add_argument("--profile", type=Path, default=None, help="Путь к uumc.profile.yaml")
    p_report.add_argument("--set-mode", choices=VALID_REPORT_MODES, default=None, help="Установить режим отчётов для проекта мода (off | local | inbox)")
    p_report.add_argument("--mode", choices=VALID_REPORT_MODES, default=None, help="Временно переопределить режим отчётов для этого вызова")
    p_report.add_argument("--kind", choices=("bug", "gap", "promote"), default="bug", help="Тип отчёта: bug | gap | promote")
    p_report.add_argument("--title", type=str, default=None, help="Короткий заголовок (3-8 слов)")
    p_report.add_argument("--tool", type=str, default=None, help="Инструмент или слой ядра (например, execute_csharp, uumc build)")
    p_report.add_argument("--actual", type=str, default=None, help="Что произошло фактически")
    p_report.add_argument("--expected", type=str, default=None, help="Какое поведение ожидалось")
    p_report.add_argument("--workaround", type=str, default=None, help="Локальный обход, применённый в моде")

    p_link = sub.add_parser("link", help="Создать или обновить локальную связку (.uumc.local.yaml, Directory.Build.Local.props, uumc.cmd)")
    p_link.add_argument("--game-dir", type=Path, required=True, help="Папка с целевой игрой")
    p_link.add_argument("--workspace", type=Path, default=None, help="Корневая папка проекта мода")
    p_link.add_argument("--deploy-devmcp", action="store_true", help="Собрать и скопировать Uumc.DevMcp.dll в BepInEx/plugins")

    p_build = sub.add_parser("build", help="Собрать C#-мод и скопировать DLL в BepInEx/plugins/<ModName>/")
    p_build.add_argument("--workspace", type=Path, default=None, help="Корневая папка проекта мода")
    p_build.add_argument("--profile", type=Path, default=None, help="Путь к uumc.profile.yaml")
    p_build.add_argument("--game-dir", type=Path, default=None, help="Папка с целевой игрой (переопределяет .uumc.local.yaml)")
    p_build.add_argument("-c", "--configuration", type=str, default="Release", help="Конфигурация сборки dotnet (по умолчанию Release)")
    p_build.add_argument("--kill-existing", action="store_true", help="Закрыть запущенную копию игры перед сборкой и деплоем")
    p_build.add_argument("--no-deploy", action="store_true", help="Только собрать проект без копирования DLL в BepInEx/plugins")

    for name, help_text in (
        ("index", "Извлечь CLR-символы из Assembly-CSharp.dll через CecilExtractor"),
        ("sync", "Синхронизировать descriptions_cache.json и breaking_changes.md"),
        ("taxonomy", "Проверить или сгенерировать черновик taxonomy.yaml"),
        ("render", "Сгенерировать базу знаний docs/game-internals/ и INDEX-LLM.md"),
        ("enrich", "Собрать рантайм-реестры через DevMCP"),
        ("test", "Запустить рантайм-тесты через DevMCP"),
        ("mcp", "Запустить stdio-мост DevMCP для нативного подключения IDE"),
        ("lint", "Запустить check_paths, check_rules, check_links и unittest"),
    ):
        p = sub.add_parser(name, help=help_text, add_help=False)
        p.add_argument("args", nargs=argparse.REMAINDER, help="Аргументы целевого инструмента")

    return parser


SUBCOMMAND_MAP = {
    "sync": "core/indexer/sync_descriptions.py",
    "taxonomy": "core/indexer/validate_taxonomy.py",
    "render": "core/indexer/render_docs.py",
    "enrich": "core/indexer/enrich_base.py",
    "test": "core/testing/runner.py",
    "mcp": "core/testing/mcp_bridge.py",
    "lint": "core/linters/run_checks.py",
}


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        ws_arg, clean_argv = _extract_flag_arg(raw_argv, "--workspace", "После --workspace не указан путь к рабочей папке.")
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    workspace_dir: Path | None = None
    try:
        if ws_arg is not None:
            workspace_dir = Path(ws_arg).expanduser().resolve()
            if not workspace_dir.is_dir():
                raise FileNotFoundError(f"Рабочая папка не найдена: {workspace_dir}")
        else:
            workspace_dir = find_workspace_root(Path.cwd())

        parser = build_parser()
        uumc_root = resolve_uumc_root(workspace_dir=workspace_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    if clean_argv and clean_argv[0] == "index":
        sys.exit(_handle_index(clean_argv[1:], uumc_root, workspace_dir=workspace_dir))
    if clean_argv and clean_argv[0] in SUBCOMMAND_MAP:
        subcmd = clean_argv[0]
        forward = clean_argv[1:]
        if forward and forward[0] == "--":
            forward = forward[1:]
        if not _has_flag(forward, ("--help", "-h")) and workspace_dir is not None:
            if subcmd == "lint":
                if not _has_flag(forward, ("--root",)):
                    forward = ["--root", str(workspace_dir), *forward]
                if not _has_flag(forward, ("--profile",)) and (workspace_dir / PROFILE_FILENAME).is_file():
                    forward = ["--profile", str(workspace_dir / PROFILE_FILENAME), *forward]
            else:
                if subcmd != "mcp":
                    _warn_if_missing_local_binding(workspace_dir, forward)
                if not _has_flag(forward, ("--profile",)) and (workspace_dir / PROFILE_FILENAME).is_file():
                    forward = ["--profile", str(workspace_dir / PROFILE_FILENAME), *forward]
                if subcmd in ("test", "mcp") and not _has_flag(forward, ("--workspace", "--profile")):
                    forward = ["--workspace", str(workspace_dir), *forward]
        if subcmd == "lint" and not (uumc_root / SUBCOMMAND_MAP[subcmd]).is_file():
            print(
                "[INFO] Команда `lint` доступна только в dev-репозитории UUMC. "
                "Для проверки проекта мода используйте `uumc doctor`.",
                file=sys.stderr,
            )
            sys.exit(1)
        sys.exit(_run_python_module(uumc_root, SUBCOMMAND_MAP[subcmd], forward, unbuffered=(subcmd == "mcp")))

    args = parser.parse_args(clean_argv)
    if args.command == "init":
        sys.exit(_handle_init(args, uumc_root))
    if args.command == "scan":
        sys.exit(_handle_scan(args))
    if args.command == "doctor":
        sys.exit(_handle_doctor(args, uumc_root, workspace_dir))
    if args.command == "report":
        sys.exit(_handle_report(args, uumc_root, workspace_dir))
    if args.command == "link":
        sys.exit(_handle_link(args, uumc_root, workspace_dir))
    if args.command == "build":
        sys.exit(_handle_build(args, workspace_dir))

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
