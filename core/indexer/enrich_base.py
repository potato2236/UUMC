#!/usr/bin/env python3
"""
Generic runtime enrichment runner for UUMC.

1. If a game-specific enricher_script is configured (--script or profile),
   delegates execution to it with standardized CLI arguments.
2. Otherwise connects to Uumc.DevMcp via core.testing.mcp_client.DevMcpClient
   to collect generic game metadata and loaded assemblies into runtime_registries.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from core.indexer.profile import get_profile_section, load_profile, resolve_profile_path
    from core.testing.mcp_client import DevMcpClient, DevMcpError
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.indexer.profile import get_profile_section, load_profile, resolve_profile_path
    from core.testing.mcp_client import DevMcpClient, DevMcpError


def collect_generic_runtime(host: str, port: int, timeout: float = 10.0) -> dict[str, Any]:
    client = DevMcpClient(host=host, port=port, timeout=timeout, client_name="uumc-enrich")
    if not client.connect():
        raise ConnectionError(f"Не удалось подключиться к DevMCP ({host}:{port})")
    try:
        game_info = client.get_game_info()
        assemblies_raw = client.call_tool("list_assemblies", {"includeSystem": False})
        assemblies = json.loads(assemblies_raw) if assemblies_raw else []
        return {
            "source": "devmcp_generic",
            "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "game_info": game_info,
            "assemblies": assemblies,
        }
    finally:
        client.close()


def run_enrichment(
    output_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 27015,
    script_path: Path | None = None,
    decompiled_dir: Path | None = None,
    allow_offline: bool = False,
) -> int:
    if script_path is not None:
        if not script_path.is_file():
            raise FileNotFoundError(f"Скрипт обогащения не найден: {script_path}")
        cmd = [
            sys.executable,
            str(script_path),
            "--host",
            host,
            "--port",
            str(port),
            "--output",
            str(output_path),
        ]
        if decompiled_dir and decompiled_dir.is_dir():
            cmd.extend(["--decompiled", str(decompiled_dir)])
        result = subprocess.run(cmd)
        return result.returncode

    try:
        payload = collect_generic_runtime(host, port)
    except (ConnectionError, DevMcpError, OSError) as exc:
        if not allow_offline:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1
        payload = {
            "source": "offline_stub",
            "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "game_info": {},
            "assemblies": [],
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] Рантайм-реестры сохранены: {output_path} (source={payload['source']})")
    return 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Сбор рантайм-метаданных и реестров игры через DevMCP.")
    parser.add_argument("--profile", type=Path, default=None, help="Опциональный путь к uumc.profile.yaml")
    parser.add_argument("--script", type=Path, default=None, help="Опциональный кастомный скрипт обогащения игры")
    parser.add_argument("--decompiled", type=Path, default=None, help="Опциональный путь к decompiled/")
    parser.add_argument("--host", type=str, default=None, help="Хост DevMCP (по умолчанию 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Порт DevMCP (по умолчанию 27015)")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Выходной JSON-файл")
    parser.add_argument("--allow-offline", action="store_true", help="Создать базовый offline-дамп при недоступности игры")
    args = parser.parse_args()

    base_dir = Path.cwd()
    profile_data: dict[str, Any] = {}
    try:
        if args.profile is not None:
            p_path = args.profile.resolve()
            profile_data = load_profile(p_path)
            base_dir = p_path.parent

        game_cfg = get_profile_section(profile_data, "game")
        mcp_cfg = get_profile_section(profile_data, "dev_mcp")
        idx_cfg = get_profile_section(profile_data, "indexer")

        host = args.host or str(mcp_cfg.get("host") or "127.0.0.1")
        port = int(args.port or mcp_cfg.get("port") or 27015)
        script_path = args.script.resolve() if args.script else resolve_profile_path(base_dir, idx_cfg.get("enricher_script"))
        decompiled_dir = args.decompiled.resolve() if args.decompiled else resolve_profile_path(base_dir, game_cfg.get("decompiled_dir"))
        output_path = args.output.resolve() if args.output else (
            resolve_profile_path(base_dir, idx_cfg.get("runtime_registries_file")) or (base_dir / "runtime_registries.json")
        )

        code = run_enrichment(
            output_path,
            host=host,
            port=port,
            script_path=script_path,
            decompiled_dir=decompiled_dir,
            allow_offline=args.allow_offline,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    sys.exit(code)


if __name__ == "__main__":
    main()
