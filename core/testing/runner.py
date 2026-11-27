#!/usr/bin/env python3
"""
Game-agnostic runtime test runner for UUMC.

Drives Unity/BepInEx game lifecycle, DevMCP readiness polling, scene/state
bootstrapping, smoke/scripted checks, screenshot capture, and log validation
using uumc.profile.yaml or explicit CLI flags.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import os
import re
import runpy
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from core.indexer.profile import (
        LOCAL_CONFIG_FILENAME,
        PROFILE_FILENAME,
        find_workspace_root,
        get_profile_section,
        load_profile,
        resolve_game_dir,
        sync_devmcp_port,
    )
    from core.testing.mcp_client import DevMcpClient, DevMcpError
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.indexer.profile import (
        LOCAL_CONFIG_FILENAME,
        PROFILE_FILENAME,
        find_workspace_root,
        get_profile_section,
        load_profile,
        resolve_game_dir,
        sync_devmcp_port,
    )
    from core.testing.mcp_client import DevMcpClient, DevMcpError

DEFAULT_LOG_ERROR_MARKERS = ("NullReferenceException", "[Error  :", "[Fatal  :")
DEFAULT_WINDOW_SIZE = "640x360"
DEFAULT_WINDOW_POS = "bottom-right"
VALID_WINDOW_POSITIONS = ("bottom-right", "top-right", "bottom-left", "top-left")
_WINDOW_SIZE_RE = re.compile(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$")


def parse_window_size(raw: Any) -> tuple[int, int]:
    """Parses and validates a WxH window size string."""
    if not isinstance(raw, str):
        raise ValueError(f"Некорректный тип window_size '{raw!r}': ожидается строка WxH (например, 640x360).")
    match = _WINDOW_SIZE_RE.match(raw)
    if not match:
        raise ValueError(f"Некорректный формат --window-size '{raw}': ожидается WxH (например, 640x360).")
    width = int(match.group(1))
    height = int(match.group(2))
    if not (160 <= width <= 3840 and 90 <= height <= 2160):
        raise ValueError(f"Недопустимый размер окна {width}x{height} (допустимо 160..3840 x 90..2160).")
    return width, height


def parse_window_pos(raw: Any) -> str:
    """Validates a window corner identifier."""
    if not isinstance(raw, str):
        raise ValueError(f"Некорректный тип window_pos '{raw!r}': ожидается строковый идентификатор угла экрана.")
    pos = raw.strip().lower()
    if pos not in VALID_WINDOW_POSITIONS:
        choices = ", ".join(VALID_WINDOW_POSITIONS)
        raise ValueError(f"Некорректная позиция окна '{raw}': допустимы {choices}.")
    return pos


def parse_bool_setting(raw: Any, field_name: str) -> bool:
    """Strictly parses a boolean setting from YAML/CLI without truthiness pitfalls."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    raise ValueError(f"Некорректное булево значение для '{field_name}': {raw!r} (ожидается true/false).")


def parse_linger(raw: Any) -> float:
    """Validates --linger seconds as a finite non-negative number."""
    if isinstance(raw, bool):
        raise ValueError(f"Некорректное значение --linger: {raw!r}")
    try:
        val = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Некорректное значение --linger: {raw!r}") from exc
    if not math.isfinite(val) or val < 0.0:
        raise ValueError(f"Параметр --linger должен быть конечным неотрицательным числом, получено: {raw}")
    return val


def find_game_exe(game_dir: Path, exe_name: str | None) -> Path | None:
    if exe_name:
        candidate = game_dir / exe_name
        return candidate if candidate.is_file() else None
    exes = sorted(
        p
        for p in game_dir.glob("*.exe")
        if "UnityCrashHandler" not in p.name
    )
    return exes[0] if exes else None


def _list_pids_by_exe_name(exe_name: str) -> set[int]:
    """Returns currently running PIDs matching exe_name (Windows)."""
    if os.name != "nt":
        return set()
    image_name = f"{Path(exe_name).stem}.exe"
    proc = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    pids: set[int] = set()
    for row in csv.reader(io.StringIO(proc.stdout)):
        if len(row) >= 2 and row[0].strip().lower() == image_name.lower():
            try:
                pids.add(int(row[1].strip()))
            except ValueError:
                pass
    return pids


def _is_pid_running(pid: int, exe_name: str) -> bool:
    return pid in _list_pids_by_exe_name(exe_name)


def _wait_pid_exit(pid: int, exe_name: str, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _is_pid_running(pid, exe_name):
            return True
        time.sleep(0.2)
    return not _is_pid_running(pid, exe_name)


def kill_process_by_exe_name(exe_name: str, pid: int | None = None, graceful_timeout: float = 3.0) -> None:
    """Requests graceful window close first so BepInEx flushes LogOutput.log, then force-kills."""
    proc_stem = Path(exe_name).stem
    if os.name == "nt":
        if pid is not None:
            subprocess.run(["taskkill", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if _wait_pid_exit(pid, exe_name, graceful_timeout):
                return
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return

        image_name = f"{proc_stem}.exe"
        subprocess.run(["taskkill", "/IM", image_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        deadline = time.monotonic() + graceful_timeout
        while time.monotonic() < deadline:
            if not _list_pids_by_exe_name(exe_name):
                return
            time.sleep(0.2)
        subprocess.run(["taskkill", "/F", "/IM", image_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        subprocess.run(
            ["pkill", "-f", proc_stem],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def build_spectator_env(
    *,
    width: int,
    height: int,
    pos: str,
    mute_audio: bool,
    interactive: bool,
) -> dict[str, str]:
    """Builds ephemeral UUMC_SPECTATOR_* environment variables for the child game process."""
    return {
        "UUMC_SPECTATOR_WINDOW": "1",
        "UUMC_SPECTATOR_WIDTH": str(width),
        "UUMC_SPECTATOR_HEIGHT": str(height),
        "UUMC_SPECTATOR_POS": pos,
        "UUMC_SPECTATOR_MUTE": "1" if mute_audio else "0",
        "UUMC_SPECTATOR_UNLOCK_CURSOR": "0" if interactive else "1",
        "UUMC_SPECTATOR_INTERACTIVE": "1" if interactive else "0",
        "UUMC_SPECTATOR_TOPMOST": "0" if interactive else "1",
    }


def _find_new_pid(exe_name: str, before_pids: set[int], wait_sec: float = 1.5) -> int | None:
    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        new_pids = sorted(_list_pids_by_exe_name(exe_name) - before_pids)
        if new_pids:
            return new_pids[-1]
        time.sleep(0.1)
    return None


def launch_game_process(
    exe_path: Path,
    visible: bool = True,
    env: dict[str, str] | None = None,
    unity_args: list[str] | None = None,
) -> subprocess.Popen[bytes] | int | None:
    """Launches the game executable. On Windows with visible=True, uses shell start and returns new PID."""
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    extra_args = list(unity_args or [])

    if os.name == "nt" and visible:
        before_pids = _list_pids_by_exe_name(exe_path.name)
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "", str(exe_path), *extra_args],
            cwd=str(exe_path.parent),
            env=merged_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return _find_new_pid(exe_path.name, before_pids)

    return subprocess.Popen(
        [str(exe_path), *extra_args],
        cwd=str(exe_path.parent),
        env=merged_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def inspect_bepinex_log(
    log_path: Path,
    error_markers: tuple[str, ...] = DEFAULT_LOG_ERROR_MARKERS,
) -> list[str]:
    """Returns lines from BepInEx LogOutput.log matching critical error markers."""
    if not log_path.is_file():
        return []
    hits: list[str] = []
    for line in log_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if any(marker in line for marker in error_markers):
            hits.append(line.strip())
    return hits


def wait_for_ready(
    client: DevMcpClient,
    ready_expr: str,
    timeout_sec: float = 30.0,
    poll_interval: float = 1.0,
) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            res = client.execute_csharp(ready_expr)
            if res.strip().lower() == "true":
                return True
        except Exception:
            pass
        time.sleep(poll_interval)
    return False


def _resolve_runtime_settings(args: argparse.Namespace) -> dict[str, Any]:
    workspace_dir: Path | None = None
    explicit_ws = getattr(args, "workspace", None)
    if explicit_ws is not None:
        workspace_dir = explicit_ws.resolve()
        if not workspace_dir.is_dir():
            raise FileNotFoundError(f"Рабочая папка не найдена: {workspace_dir}")

    profile_data: dict[str, Any] = {}
    profile_dir = workspace_dir or Path.cwd()
    if args.profile is not None:
        profile_path = args.profile.resolve()
        if not profile_path.is_file():
            raise FileNotFoundError(f"Файл профиля не найден: {profile_path}")
        profile_data = load_profile(profile_path)
        profile_dir = profile_path.parent
        if workspace_dir is None:
            workspace_dir = profile_dir
    else:
        ws_candidate = workspace_dir or find_workspace_root(Path.cwd())
        if ws_candidate is not None and (ws_candidate / PROFILE_FILENAME).is_file():
            workspace_dir = ws_candidate
            profile_dir = ws_candidate
            profile_data = load_profile(ws_candidate / PROFILE_FILENAME)

    game_cfg = get_profile_section(profile_data, "game")
    mcp_cfg = get_profile_section(profile_data, "dev_mcp")
    test_cfg = get_profile_section(profile_data, "testing")

    game_dir = resolve_game_dir(
        explicit_game_dir=args.game_dir,
        workspace_dir=workspace_dir,
        profile_data=profile_data,
        profile_dir=profile_dir,
    )

    raw_cli_size = getattr(args, "window_size", None)
    if raw_cli_size is not None:
        win_w, win_h = parse_window_size(raw_cli_size)
    elif test_cfg.get("window_size") is not None:
        win_w, win_h = parse_window_size(test_cfg.get("window_size"))
    else:
        win_w, win_h = parse_window_size(DEFAULT_WINDOW_SIZE)

    raw_cli_pos = getattr(args, "window_pos", None)
    if raw_cli_pos is not None:
        win_pos = parse_window_pos(raw_cli_pos)
    elif test_cfg.get("window_pos") is not None:
        win_pos = parse_window_pos(test_cfg.get("window_pos"))
    else:
        win_pos = DEFAULT_WINDOW_POS

    profile_mute = test_cfg.get("mute_audio")
    default_mute = True if profile_mute is None else parse_bool_setting(profile_mute, "testing.mute_audio")
    mute_audio = False if getattr(args, "with_audio", False) else default_mute
    linger_sec = parse_linger(getattr(args, "linger", 0.0))

    return {
        "workspace_dir": workspace_dir,
        "game_dir": game_dir,
        "exe_name": args.exe or game_cfg.get("exe_name"),
        "host": args.host or str(mcp_cfg.get("host") or "127.0.0.1"),
        "port": int(args.port or mcp_cfg.get("port") or 27015),
        "start_scene_csharp": args.start_csharp or test_cfg.get("start_scene_csharp"),
        "ready_check_csharp": args.ready_csharp or test_cfg.get("ready_check_csharp"),
        "window_width": win_w,
        "window_height": win_h,
        "window_pos": win_pos,
        "mute_audio": mute_audio,
        "interactive": bool(getattr(args, "interactive", False)),
        "linger": linger_sec,
    }


def _connect_with_process_watch(
    client: DevMcpClient,
    *,
    retries: int,
    launched_proc: subprocess.Popen[bytes] | None,
    launched_pid: int | None,
    exe_name: str | None,
) -> bool:
    attempts = max(1, retries)
    for attempt in range(attempts):
        if client.connect(retries=1, retry_delay=0.0):
            return True
        if launched_proc is not None and launched_proc.poll() is not None:
            print(
                f"[ERROR] Процесс игры завершился до запуска DevMCP (exit code {launched_proc.returncode}).",
                file=sys.stderr,
            )
            return False
        if launched_pid is not None and exe_name and not _is_pid_running(launched_pid, exe_name):
            print(f"[ERROR] Процесс игры (PID={launched_pid}) завершился до подключения DevMCP.", file=sys.stderr)
            return False
        if attempt + 1 < attempts:
            time.sleep(1.0)
    return False


def _stop_launched_game(
    launched_proc: subprocess.Popen[bytes] | None,
    launched_pid: int | None,
    exe_path: Path | None,
) -> None:
    if launched_proc is not None:
        launched_proc.terminate()
        try:
            launched_proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            launched_proc.kill()
        return
    if exe_path is not None:
        kill_process_by_exe_name(exe_path.name, pid=launched_pid)


def run_session(args: argparse.Namespace) -> int:
    cfg = _resolve_runtime_settings(args)
    game_dir: Path | None = cfg["game_dir"]
    exe_name: str | None = cfg["exe_name"]
    host: str = cfg["host"]
    port: int = cfg["port"]

    if args.script is not None and not args.script.resolve().is_file():
        print(f"[ERROR] Тестовый скрипт не найден: {args.script}", file=sys.stderr)
        return 1

    launched_proc: subprocess.Popen[bytes] | None = None
    launched_pid: int | None = None
    exe_path: Path | None = None
    log_path: Path | None = None

    if game_dir is not None:
        log_path = game_dir / "BepInEx" / "LogOutput.log"
        exe_path = find_game_exe(game_dir, exe_name)

    if args.launch:
        if game_dir is None or exe_path is None:
            ws_dir = cfg.get("workspace_dir")
            hint = ""
            if game_dir is None and ws_dir is not None and not (ws_dir / LOCAL_CONFIG_FILENAME).is_file():
                hint = ' Локальная связка .uumc.local.yaml не найдена — выполните `uumc link --game-dir "<путь>"`.'
            print(f"[ERROR] Для --launch требуется валидный --game-dir и .exe игры.{hint}", file=sys.stderr)
            return 1
        if args.kill_existing:
            kill_process_by_exe_name(exe_path.name)
            time.sleep(0.5)
        sync_devmcp_port(game_dir, port)
        if log_path and log_path.exists():
            try:
                log_path.unlink()
            except OSError:
                pass

        spectator_env = build_spectator_env(
            width=cfg["window_width"],
            height=cfg["window_height"],
            pos=cfg["window_pos"],
            mute_audio=cfg["mute_audio"],
            interactive=cfg["interactive"],
        )
        unity_args = ["-screen-fullscreen", "0"]
        if not cfg["interactive"]:
            unity_args.append("-popupwindow")

        print(
            f"[runner] Запуск игры: {exe_path} "
            f"(window={cfg['window_width']}x{cfg['window_height']}, pos={cfg['window_pos']}, "
            f"interactive={cfg['interactive']}, mute={cfg['mute_audio']})"
        )
        launch_res = launch_game_process(
            exe_path,
            visible=True,
            env=spectator_env,
            unity_args=unity_args,
        )
        if isinstance(launch_res, int):
            launched_pid = launch_res
        elif isinstance(launch_res, subprocess.Popen):
            launched_proc = launch_res
            launched_pid = launch_res.pid

    game_stopped = False
    try:
        client = DevMcpClient(host=host, port=port, timeout=args.timeout, client_name="uumc-runner")
        print(f"[runner] Подключение к DevMCP {host}:{port}...")
        connected = _connect_with_process_watch(
            client,
            retries=args.connect_retries,
            launched_proc=launched_proc,
            launched_pid=launched_pid,
            exe_name=exe_path.name if exe_path else None,
        )
        if not connected:
            print(f"[ERROR] Не удалось подключиться к DevMCP ({host}:{port}).", file=sys.stderr)
            return 1

        try:
            if args.smoke:
                info = client.get_game_info()
                print(f"[runner] Smoke OK: {info.get('gameName', 'Unknown')} (Unity {info.get('engineVersion', '?')})")

            ready_expr = cfg["ready_check_csharp"]
            start_expr = cfg["start_scene_csharp"]

            if start_expr:
                need_start = True
                if ready_expr:
                    try:
                        need_start = client.execute_csharp(ready_expr).strip().lower() != "true"
                    except DevMcpError:
                        need_start = True
                if need_start:
                    print("[runner] Инициализация тестовой сцены...")
                    client.execute_csharp(start_expr)

            if ready_expr:
                print("[runner] Ожидание готовности сцены/игрока...")
                if not wait_for_ready(client, ready_expr, timeout_sec=args.ready_timeout):
                    print("[ERROR] Таймаут ожидания готовности по ready_check_csharp.", file=sys.stderr)
                    return 1

            for expr in args.eval:
                res = client.execute_csharp(expr)
                print(f"[eval] {res}")

            if args.script is not None:
                script_path = args.script.resolve()
                print(f"[runner] Выполнение сценария: {script_path}")
                ns = runpy.run_path(str(script_path), run_name="__uumc_test__")
                if callable(ns.get("run_test")):
                    ns["run_test"](client)

            if args.screenshot is not None:
                png_bytes = client.take_screenshot(save_local_path=args.screenshot)
                print(f"[runner] Скриншот сохранён: {args.screenshot} ({len(png_bytes)} байт)")
        finally:
            client.close()

        linger_sec = cfg["linger"]
        if args.launch and not args.keep_open and linger_sec > 0:
            time.sleep(linger_sec)

        if args.launch and not args.keep_open:
            _stop_launched_game(launched_proc, launched_pid, exe_path)
            game_stopped = True

        if args.check_log:
            if log_path is None:
                print(
                    "[ERROR] Для --check-log требуется валидный --game-dir (не удалось определить путь к LogOutput.log).",
                    file=sys.stderr,
                )
                return 1
            if not log_path.is_file() or log_path.stat().st_size == 0:
                print(f"[ERROR] Лог BepInEx отсутствует или пуст: {log_path}", file=sys.stderr)
                return 1
            hits = inspect_bepinex_log(log_path)
            if hits:
                print(f"[ERROR] В {log_path} обнаружены критические ошибки ({len(hits)}):", file=sys.stderr)
                for h in hits[:10]:
                    print(f"  {h}", file=sys.stderr)
                return 1
            print(f"[runner] Лог чист: {log_path}")

        return 0
    finally:
        if args.launch and not args.keep_open and not game_stopped:
            _stop_launched_game(launched_proc, launched_pid, exe_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Универсальный раннер рантайм-тестов UUMC через DevMCP.")
    parser.add_argument("--workspace", type=Path, default=None, help="Корневая папка проекта мода")
    parser.add_argument("--profile", type=Path, default=None, help="Путь к uumc.profile.yaml")
    parser.add_argument("--game-dir", type=Path, default=None, help="Папка игры (переопределяет профиль / UUMC_GAME_DIR)")
    parser.add_argument("--exe", type=str, default=None, help="Имя .exe игры")
    parser.add_argument("--host", type=str, default=None, help="Хост DevMCP (по умолчанию 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Порт DevMCP (по умолчанию 27015)")
    parser.add_argument("--launch", action="store_true", help="Запустить игру перед тестом и закрыть после")
    parser.add_argument("--visible", action="store_true", help="Совместимость: видимое окно (активно по умолчанию при --launch)")
    parser.add_argument("--interactive", action="store_true", help="Обычное кликабельное/фокусируемое окно без click-through")
    parser.add_argument("--with-audio", action="store_true", help="Не глушить звук игры в зрительском режиме")
    parser.add_argument("--window-size", type=str, default=None, help="Размер окна WxH (по умолчанию 640x360)")
    parser.add_argument(
        "--window-pos",
        type=str,
        choices=VALID_WINDOW_POSITIONS,
        default=None,
        help="Угол экрана для окна наблюдения (по умолчанию bottom-right)",
    )
    parser.add_argument("--linger", type=float, default=0.0, help="Пауза перед закрытием окна после теста (сек)")
    parser.add_argument("--kill-existing", action="store_true", help="Закрыть запущенную копию игры перед стартом")
    parser.add_argument("--keep-open", action="store_true", help="Не закрывать игру после теста")
    parser.add_argument("--smoke", action="store_true", help="Базовый smoke-тест подключения и get_game_info")
    parser.add_argument("--start-csharp", type=str, default=None, help="C#-код запуска тестовой сцены")
    parser.add_argument("--ready-csharp", type=str, default=None, help="C#-условие готовности (ожидает 'true')")
    parser.add_argument("--eval", action="append", default=[], help="Выполнить C#-фрагмент через execute_csharp")
    parser.add_argument("--script", type=Path, default=None, help="Python-сценарий (опционально функция run_test(client))")
    parser.add_argument("--screenshot", type=Path, default=None, help="Локальный путь для сохранения скриншота")
    parser.add_argument("--check-log", action="store_true", help="Проверить BepInEx/LogOutput.log на ошибки")
    parser.add_argument("--connect-retries", type=int, default=20, help="Число попыток подключения к DevMCP")
    parser.add_argument("--ready-timeout", type=float, default=30.0, help="Таймаут ожидания ready_check_csharp (сек)")
    parser.add_argument("--timeout", type=float, default=10.0, help="Таймаут сокета DevMCP (сек)")
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args()
    try:
        code = run_session(args)
    except (FileNotFoundError, ValueError, DevMcpError, ConnectionError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    sys.exit(code)


if __name__ == "__main__":
    main()
