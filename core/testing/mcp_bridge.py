#!/usr/bin/env python3
"""
Persistent MCP stdio <-> TCP JSON-RPC 2.0 bridge for Uumc.DevMcp.

Allows IDEs and agentic systems (Antigravity, Cursor, Claude Code, VS Code) to
connect directly via stdio without dropping the MCP session when the Unity game
is offline or restarts. Reuses DevMcpClient for all TCP transport operations.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, BinaryIO

try:
    from core.indexer.profile import (
        PROFILE_FILENAME,
        find_workspace_root,
        get_profile_section,
        load_local_config,
        load_profile,
        resolve_uumc_root,
    )
    from core.testing.mcp_client import DevMcpClient
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.indexer.profile import (
        PROFILE_FILENAME,
        find_workspace_root,
        get_profile_section,
        load_local_config,
        load_profile,
        resolve_uumc_root,
    )
    from core.testing.mcp_client import DevMcpClient

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 27015
DEFAULT_CONNECT_TIMEOUT = 1.0
DEFAULT_IO_TIMEOUT = 35.0
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_INSTRUCTIONS = (
    "Uumc.DevMcp provides tools to interact with running Unity (BepInEx 5) games: "
    "read logs, execute C# at runtime, inspect/control MonoBehaviours, and explore scene hierarchies. "
    "If the game is not running yet, start it (e.g. via `uumc test --launch`) before calling runtime tools."
)

STATIC_RESOURCES: list[dict[str, Any]] = [
    {
        "uri": "uumc://logs/unity",
        "name": "Unity Logs",
        "description": "Recent log output captured from Unity",
        "mimeType": "text/plain",
    },
    {
        "uri": "uumc://scene/hierarchy",
        "name": "Scene Hierarchy",
        "description": "Current Unity scene object hierarchy",
        "mimeType": "application/json",
    },
    {
        "uri": "uumc://game/info",
        "name": "Game Information",
        "description": "Information about the running Unity game",
        "mimeType": "application/json",
    },
]


def _validate_port(raw_port: Any) -> int:
    if isinstance(raw_port, bool):
        raise ValueError(f"Некорректный порт DevMCP: {raw_port!r}")
    try:
        port_int = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Некорректный порт DevMCP: {raw_port!r}") from exc
    if not (1 <= port_int <= 65535):
        raise ValueError(f"Порт DevMCP вне диапазона 1..65535: {port_int}")
    return port_int


def resolve_bridge_target(
    explicit_host: str | None = None,
    explicit_port: int | None = None,
    workspace_dir: Path | None = None,
    profile_path: Path | None = None,
    cwd: Path | None = None,
) -> tuple[str, int, Path | None]:
    """Resolves (host, port, workspace_dir) using UUMC priority cascade."""
    resolved_ws: Path | None = None
    if workspace_dir is not None:
        resolved_ws = workspace_dir.expanduser().resolve()
        if not resolved_ws.is_dir():
            raise FileNotFoundError(f"Рабочая папка не найдена: {resolved_ws}")

    profile_data: dict[str, Any] = {}
    if profile_path is not None:
        p_path = profile_path.expanduser().resolve()
        if not p_path.is_file():
            raise FileNotFoundError(f"Файл профиля не найден: {p_path}")
        profile_data = load_profile(p_path)
        if resolved_ws is None:
            resolved_ws = p_path.parent
    else:
        ws_candidate = resolved_ws or find_workspace_root(cwd or Path.cwd())
        if ws_candidate is not None and (ws_candidate / PROFILE_FILENAME).is_file():
            resolved_ws = ws_candidate
            profile_data = load_profile(ws_candidate / PROFILE_FILENAME)

    mcp_cfg = get_profile_section(profile_data, "dev_mcp")
    local_cfg = load_local_config(resolved_ws) if resolved_ws is not None else {}

    env_host = os.environ.get("UUMC_DEVMCP_HOST", "").strip()
    env_port = os.environ.get("UUMC_DEVMCP_PORT", "").strip()

    host = (
        (explicit_host or "").strip()
        or env_host
        or str(local_cfg.get("dev_mcp_host") or "").strip()
        or str(mcp_cfg.get("host") or "").strip()
        or DEFAULT_HOST
    )

    raw_port: Any
    if explicit_port is not None:
        raw_port = explicit_port
    elif env_port:
        raw_port = env_port
    elif local_cfg.get("dev_mcp_port") is not None:
        raw_port = local_cfg.get("dev_mcp_port")
    elif mcp_cfg.get("port") is not None:
        raw_port = mcp_cfg.get("port")
    else:
        raw_port = DEFAULT_PORT

    port = _validate_port(raw_port)
    return host, port, resolved_ws


def load_fallback_tools(manifest_path: Path | None = None) -> list[dict[str, Any]]:
    """Loads static tool definitions used when the Unity game is offline."""
    target = manifest_path or (Path(__file__).resolve().parents[1] / "dev-mcp" / "tools_manifest.json")
    if not target.is_file():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        tools = data.get("tools", []) if isinstance(data, dict) else []
        return tools if isinstance(tools, list) else []
    except Exception as exc:
        print(f"[WARN] Failed to load fallback tools manifest ({target}): {exc}", file=sys.stderr)
        return []


def build_mcp_config_snippet(
    workspace_dir: Path | None = None,
    host: str | None = None,
    port: int | None = None,
    uumc_root: Path | None = None,
    python_exe: str | None = None,
    profile_path: Path | None = None,
) -> dict[str, Any]:
    """Builds a ready-to-paste mcp_config.json snippet for IDE integration."""
    resolved_profile = profile_path.expanduser().resolve() if profile_path is not None else None
    resolved_ws = (
        workspace_dir.expanduser().resolve()
        if workspace_dir is not None
        else (resolved_profile.parent if resolved_profile is not None else None)
    )
    resolved_uumc = (uumc_root or resolve_uumc_root(workspace_dir=resolved_ws)).resolve()
    bridge_script = resolved_uumc / "core" / "testing" / "mcp_bridge.py"

    local_cfg = load_local_config(resolved_ws) if resolved_ws is not None else {}
    resolved_python = python_exe or str(local_cfg.get("python_exe") or "").strip() or sys.executable

    args = ["-u", str(bridge_script)]
    if resolved_profile is not None and resolved_profile.name != PROFILE_FILENAME:
        args.extend(["--profile", str(resolved_profile)])
    elif resolved_ws is not None:
        args.extend(["--workspace", str(resolved_ws)])

    if host:
        args.extend(["--host", host])
    if port is not None:
        args.extend(["--port", str(_validate_port(port))])

    return {
        "mcpServers": {
            "uumc-devmcp": {
                "command": resolved_python,
                "args": args,
            }
        }
    }


def _jsonrpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


class McpStdioBridge:
    """Bridges MCP stdio JSON-RPC 2.0 frames to Uumc.DevMcp via DevMcpClient."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        io_timeout: float = DEFAULT_IO_TIMEOUT,
        fallback_tools: list[dict[str, Any]] | None = None,
        log_stream: Any = None,
    ) -> None:
        self.host = host
        self.port = _validate_port(port)
        self.connect_timeout = connect_timeout
        self.io_timeout = io_timeout
        self._fallback_tools = list(fallback_tools) if fallback_tools is not None else load_fallback_tools()
        self._cached_tools: list[dict[str, Any]] | None = None
        self._log = log_stream if log_stream is not None else sys.stderr
        self._client = DevMcpClient(
            host=self.host,
            port=self.port,
            timeout=self.io_timeout,
            connect_timeout=self.connect_timeout,
            auto_initialize=False,
        )

    def _log_msg(self, message: str) -> None:
        try:
            self._log.write(f"[uumc-mcp-bridge] {message}\n")
            self._log.flush()
        except Exception:
            pass

    def close(self) -> None:
        self._client.close()

    def _forward_rpc(
        self,
        req_id: Any,
        method: str,
        params: Any,
        *,
        allow_retry: bool,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Forwards a JSON-RPC request via DevMcpClient and validates response structure."""
        resp = self._client.raw_request(method, params, auto_connect=True, allow_retry=allow_retry)
        err = resp.get("error")
        if err is not None:
            raw_code = err.get("code", -32603)
            code = raw_code if isinstance(raw_code, int) and not isinstance(raw_code, bool) else -32603
            msg = str(err.get("message", f"DevMCP {method} error"))
            return None, _jsonrpc_error(req_id, code, msg)

        result = resp.get("result")
        if not isinstance(result, dict):
            raise ValueError(f"Invalid DevMCP response for {method}: expected object result")
        return result, None

    def _offline_tool_error(self, req_id: Any, reason: str) -> dict[str, Any]:
        msg = (
            f"Unity game is not running or DevMCP is unreachable ({reason}) at {self.host}:{self.port}. "
            "Launch the game with Uumc.DevMcp plugin (e.g. `uumc test --launch`) and retry."
        )
        return _jsonrpc_result(
            req_id,
            {
                "content": [{"type": "text", "text": msg}],
                "isError": True,
            },
        )

    def _handle_initialize(self, req_id: Any) -> dict[str, Any]:
        return _jsonrpc_result(
            req_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "logging": {},
                },
                "serverInfo": {
                    "name": "Uumc.DevMcp",
                    "version": "1.0.0",
                },
                "instructions": SERVER_INSTRUCTIONS,
            },
        )

    def _handle_tools_list(self, req_id: Any, params: Any) -> dict[str, Any]:
        try:
            result, err_resp = self._forward_rpc(req_id, "tools/list", params, allow_retry=True)
            if err_resp is not None:
                return err_resp
            assert result is not None
            tools = result.get("tools")
            if isinstance(tools, list):
                self._cached_tools = tools
                return _jsonrpc_result(req_id, {"tools": tools})
            self._log_msg("Invalid tools/list payload (missing tools list); serving cached/manifest tools.")
        except (OSError, ConnectionError, ValueError) as exc:
            self._log_msg(f"Game offline/invalid during tools/list ({exc}); serving cached/manifest tools.")

        fallback = self._cached_tools if self._cached_tools is not None else self._fallback_tools
        return _jsonrpc_result(req_id, {"tools": fallback})

    def _handle_tools_call(self, req_id: Any, params: Any) -> dict[str, Any]:
        try:
            result, err_resp = self._forward_rpc(req_id, "tools/call", params, allow_retry=False)
            if err_resp is not None:
                return err_resp
            assert result is not None
            return _jsonrpc_result(req_id, result)
        except ValueError as exc:
            self._log_msg(f"Invalid tools/call response ({exc})")
            return _jsonrpc_error(req_id, -32603, str(exc))
        except (OSError, ConnectionError) as exc:
            self._log_msg(f"tools/call failed ({exc})")
            return self._offline_tool_error(req_id, str(exc))

    def _handle_resources_list(self, req_id: Any, params: Any) -> dict[str, Any]:
        try:
            result, err_resp = self._forward_rpc(req_id, "resources/list", params, allow_retry=True)
            if err_resp is not None:
                return err_resp
            assert result is not None
            return _jsonrpc_result(req_id, result)
        except (OSError, ConnectionError, ValueError):
            return _jsonrpc_result(req_id, {"resources": STATIC_RESOURCES})

    def _handle_resources_read(self, req_id: Any, params: Any) -> dict[str, Any]:
        try:
            result, err_resp = self._forward_rpc(req_id, "resources/read", params, allow_retry=True)
            if err_resp is not None:
                return err_resp
            assert result is not None
            return _jsonrpc_result(req_id, result)
        except ValueError as exc:
            return _jsonrpc_error(req_id, -32603, str(exc))
        except (OSError, ConnectionError) as exc:
            return _jsonrpc_error(
                req_id,
                -32000,
                f"Unity game is offline or DevMCP is unreachable at {self.host}:{self.port}: {exc}",
            )

    def handle_message(self, msg: Any) -> dict[str, Any] | None:
        """Handles one parsed JSON-RPC message. Returns response dict or None for notifications."""
        if not isinstance(msg, dict):
            return _jsonrpc_error(None, -32600, "Invalid JSON-RPC request: expected object")

        req_id = msg.get("id")
        method = msg.get("method")
        if not isinstance(method, str) or not method:
            if req_id is None:
                return None
            return _jsonrpc_error(req_id, -32600, "Invalid JSON-RPC request: missing method")

        # JSON-RPC 2.0 notifications have no id (or null id) and must not receive a response.
        if "id" not in msg or req_id is None:
            return None

        params = msg.get("params")
        if method == "initialize":
            return self._handle_initialize(req_id)
        if method == "ping":
            return _jsonrpc_result(req_id, {})
        if method == "tools/list":
            return self._handle_tools_list(req_id, params)
        if method == "tools/call":
            return self._handle_tools_call(req_id, params)
        if method == "resources/list":
            return self._handle_resources_list(req_id, params)
        if method == "resources/read":
            return self._handle_resources_read(req_id, params)

        return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")

    def handle_raw_line(self, raw_line: bytes) -> bytes | None:
        """Processes a single UTF-8 JSON-RPC line and returns serialized response bytes or None."""
        text = raw_line.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        try:
            msg = json.loads(text)
        except json.JSONDecodeError as exc:
            self._log_msg(f"Parse error: {exc}")
            err = _jsonrpc_error(None, -32700, f"Parse error: {exc}")
            return (json.dumps(err, ensure_ascii=False) + "\n").encode("utf-8")

        response = self.handle_message(msg)
        if response is None:
            return None
        return (json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")

    def run_stdio(self, stdin_stream: BinaryIO | None = None, stdout_stream: BinaryIO | None = None) -> int:
        """Runs the blocking stdio loop until EOF."""
        in_stream = stdin_stream if stdin_stream is not None else sys.stdin.buffer
        out_stream = stdout_stream if stdout_stream is not None else sys.stdout.buffer

        self._log_msg(f"Ready (target={self.host}:{self.port}, fallback_tools={len(self._fallback_tools)})")
        try:
            while True:
                raw_line = in_stream.readline()
                if not raw_line:
                    break
                out_bytes = self.handle_raw_line(raw_line)
                if out_bytes is not None:
                    out_stream.write(out_bytes)
                    out_stream.flush()
        finally:
            self.close()
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MCP stdio <-> TCP мост для Uumc.DevMcp (Unity/BepInEx 5).",
    )
    parser.add_argument("--workspace", type=Path, default=None, help="Корневая папка проекта мода")
    parser.add_argument("--profile", type=Path, default=None, help="Путь к uumc.profile.yaml")
    parser.add_argument("--host", type=str, default=None, help="TCP хост DevMCP (по умолчанию 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="TCP порт DevMCP (по умолчанию из профиля или 27015)")
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="Вывести готовый JSON-фрагмент для mcp_config.json и выйти",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        host, port, resolved_ws = resolve_bridge_target(
            explicit_host=args.host,
            explicit_port=args.port,
            workspace_dir=args.workspace,
            profile_path=args.profile,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    if args.print_config:
        snippet = build_mcp_config_snippet(
            workspace_dir=resolved_ws,
            host=args.host,
            port=args.port if args.port is not None else (port if port != DEFAULT_PORT else None),
            profile_path=args.profile,
        )
        sys.stdout.buffer.write((json.dumps(snippet, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
        sys.exit(0)

    bridge = McpStdioBridge(host=host, port=port)
    sys.exit(bridge.run_stdio())


if __name__ == "__main__":
    main()
