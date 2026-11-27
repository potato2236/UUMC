#!/usr/bin/env python3
"""
Unified TCP JSON-RPC 2.0 client for Uumc.DevMcp.

Reuses one persistent socket with buffered newline framing across
runtime test runners and game-indexer runtime enrichment tools.
"""

from __future__ import annotations

import base64
import json
import select
import socket
import time
from pathlib import Path
from typing import Any


class DevMcpError(RuntimeError):
    """Raised when DevMCP returns a JSON-RPC or tool execution error."""


class DevMcpClient:
    """Persistent TCP JSON-RPC 2.0 client for Uumc.DevMcp."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 27015,
        timeout: float = 10.0,
        connect_timeout: float | None = None,
        client_name: str = "uumc-client",
        client_version: str = "1.0.0",
        auto_initialize: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.connect_timeout = connect_timeout if connect_timeout is not None else timeout
        self.client_name = client_name
        self.client_version = client_version
        self.auto_initialize = auto_initialize
        self._sock: socket.socket | None = None
        self._recv_buf = bytearray()
        self._req_id = 0

    def __enter__(self) -> DevMcpClient:
        if not self.connect():
            raise ConnectionError(f"Could not connect to DevMCP at {self.host}:{self.port}")
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    @property
    def is_connected(self) -> bool:
        return self._sock is not None

    def connect(self, retries: int = 1, retry_delay: float = 1.0) -> bool:
        """Connects to DevMCP and performs the MCP initialize handshake."""
        self.close()
        attempts = max(1, retries)

        for attempt in range(attempts):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.connect_timeout)
            try:
                sock.connect((self.host, self.port))
                sock.settimeout(self.timeout)
                self._sock = sock
                self._recv_buf.clear()
                if self.auto_initialize:
                    self.request(
                        "initialize",
                        {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {
                                "name": self.client_name,
                                "version": self.client_version,
                            },
                        },
                    )
                return True
            except OSError:
                try:
                    sock.close()
                except OSError:
                    pass
                self._sock = None
                if attempt + 1 < attempts:
                    time.sleep(retry_delay)
            except Exception:
                self.close()
                if attempt + 1 < attempts:
                    time.sleep(retry_delay)

        return False

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._recv_buf.clear()

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _drop_if_stale(self) -> None:
        """Closes cached socket if the remote peer already closed or reset it (FIN/RST)."""
        if self._sock is None:
            return
        try:
            readable, _, exceptional = select.select([self._sock], [], [self._sock], 0)
        except (OSError, ValueError):
            self.close()
            return
        if exceptional:
            self.close()
            return
        if readable:
            try:
                peek = self._sock.recv(1, socket.MSG_PEEK)
                if not peek:
                    self.close()
            except OSError:
                self.close()

    def raw_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        auto_connect: bool = False,
        allow_retry: bool = False,
    ) -> dict[str, Any]:
        """Sends one JSON-RPC request and returns the raw parsed response dict without raising on JSON-RPC error."""
        self._drop_if_stale()
        max_attempts = 2 if auto_connect else 1

        for attempt in range(max_attempts):
            reused_socket = self._sock is not None
            if self._sock is None:
                if not auto_connect:
                    raise ConnectionError("DevMCP socket is not connected")
                if not self.connect(retries=1):
                    raise ConnectionError(f"Could not connect to DevMCP at {self.host}:{self.port}")
            assert self._sock is not None

            payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": method,
            }
            if params is not None:
                payload["params"] = params

            raw_msg = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            try:
                self._sock.sendall(raw_msg)
            except OSError:
                self.close()
                if auto_connect and reused_socket and attempt + 1 < max_attempts:
                    continue
                raise

            try:
                while b"\n" not in self._recv_buf:
                    chunk = self._sock.recv(8192)
                    if not chunk:
                        raise ConnectionError("DevMCP connection closed before response completed")
                    self._recv_buf.extend(chunk)

                nl_idx = self._recv_buf.index(b"\n")
                raw_line = bytes(self._recv_buf[:nl_idx])
                del self._recv_buf[: nl_idx + 1]
                response = json.loads(raw_line.decode("utf-8", errors="replace"))
            except (OSError, ConnectionError, ValueError):
                self.close()
                if allow_retry and reused_socket and attempt + 1 < max_attempts:
                    continue
                raise

            if not isinstance(response, dict):
                raise ValueError("Invalid JSON-RPC response from DevMCP: expected object")
            err = response.get("error")
            if err is not None and not isinstance(err, dict):
                raise ValueError("Invalid JSON-RPC error payload from DevMCP: expected object")
            return response

        raise ConnectionError(f"Could not complete DevMCP request '{method}'")

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Sends a JSON-RPC request and raises DevMcpError if DevMCP returns an error object."""
        response = self.raw_request(method, params, auto_connect=False, allow_retry=False)
        err = response.get("error")
        if err is not None:
            raise DevMcpError(f"JSON-RPC error {err.get('code')}: {err.get('message')}")
        return response

    def list_tools(self) -> list[dict[str, Any]]:
        resp = self.request("tools/list")
        result = resp.get("result")
        if not isinstance(result, dict):
            return []
        tools = result.get("tools", [])
        return tools if isinstance(tools, list) else []

    def call_tool_raw(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self.request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments or {},
            },
        )
        result = resp.get("result")
        if not isinstance(result, dict):
            raise DevMcpError("Invalid DevMCP response: expected object result")
        if result.get("isError"):
            content = result.get("content", [])
            first = content[0] if isinstance(content, list) and content and isinstance(content[0], dict) else {}
            msg = first.get("text", "Unknown tool error")
            raise DevMcpError(msg)
        return result

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Calls an MCP tool and returns its primary text content."""
        result = self.call_tool_raw(name, arguments)
        content = result.get("content", [])
        if content and isinstance(content, list):
            first = content[0]
            if isinstance(first, dict):
                return first.get("text", "")
        return ""

    def execute_csharp(self, code: str, timeout_ms: int = 5000) -> str:
        return self.call_tool("execute_csharp", {"code": code, "timeout": timeout_ms})

    def evaluate_expression(self, expression: str) -> str:
        return self.call_tool("evaluate_expression", {"expression": expression})

    def get_game_info(self) -> dict[str, Any]:
        raw = self.call_tool("get_game_info")
        return json.loads(raw) if raw else {}

    def read_logs(self, count: int = 100, filter_text: str | None = None, level: str = "all") -> str:
        args: dict[str, Any] = {"count": count, "level": level}
        if filter_text:
            args["filter"] = filter_text
        return self.call_tool("read_logs", args)

    def take_screenshot(
        self,
        save_local_path: Path | str | None = None,
        save_in_game_path: str | None = None,
    ) -> bytes:
        """Captures a screenshot via DevMCP, returning raw PNG bytes and optionally saving locally."""
        args: dict[str, Any] = {}
        if save_in_game_path:
            args["saveToFile"] = save_in_game_path

        result = self.call_tool_raw("take_screenshot", args)
        content = result.get("content", [])
        b64_data = ""
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image":
                b64_data = item.get("data", "")
                break

        if not b64_data:
            raise DevMcpError("take_screenshot returned no image payload")

        png_bytes = base64.b64decode(b64_data)
        if save_local_path is not None:
            out_path = Path(save_local_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(png_bytes)
        return png_bytes
