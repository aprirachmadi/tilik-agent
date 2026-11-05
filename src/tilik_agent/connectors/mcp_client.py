"""Synchronous bridge from Tilik's graph to its local source MCP servers."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from tilik_agent.config import project_root

_BPS_TOOLS = frozenset(
    {
        "bps_search_datasets",
        "bps_describe_dataset",
        "bps_get_observations",
        "bps_select_series",
        "bps_load_series",
        "bps_available_years",
        "bps_search_publication",
    }
)
_SERVERS = {
    "bps": ("tilik_agent.connectors.bps_mcp", _BPS_TOOLS),
    "bnpb": ("tilik_agent.connectors.bnpb.mcp", frozenset({"bnpb_query_series"})),
    "bmkg": (
        "tilik_agent.connectors.bmkg.mcp",
        frozenset({"bmkg_forecast", "bmkg_earthquakes", "bmkg_active_warnings"}),
    ),
}


class LocalMcpClient:
    """Keep one MCP session alive across the graph's synchronous tool calls."""

    def __init__(self, server: str) -> None:
        self._server = server
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: ClientSession | None = None
        self._error: BaseException | None = None
        threading.Thread(target=self._run, name=f"tilik-{server}-mcp", daemon=True).start()

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self._error = exc
            self._ready.set()

    async def _serve(self) -> None:
        url = os.getenv(f"TILIK_{self._server.upper()}_MCP_URL", "").strip()
        if url:
            async with streamablehttp_client(url) as (reader, writer, _):
                await self._session_loop(reader, writer)
            return
        env = os.environ.copy()
        source_dir = str(project_root() / "src")
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [source_dir, env.get("PYTHONPATH", "")]))
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", _SERVERS[self._server][0]],
            env=env,
        )
        async with stdio_client(params) as (reader, writer):
            await self._session_loop(reader, writer)

    async def _session_loop(self, reader: Any, writer: Any) -> None:
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            self._loop = asyncio.get_running_loop()
            self._session = session
            self._ready.set()
            await asyncio.Event().wait()

    def call(self, name: str, arguments: dict[str, Any], *, timeout: float = 45) -> Any:
        if name not in _SERVERS[self._server][1]:
            raise ValueError(f"Unapproved {self._server} MCP tool: {name}")
        if not self._ready.wait(timeout=timeout):
            raise TimeoutError(f"{self._server} MCP server did not start")
        if self._error is not None:
            raise RuntimeError(f"{self._server} MCP server failed: {self._error}") from self._error
        if self._loop is None or self._session is None:
            raise RuntimeError(f"{self._server} MCP session is unavailable")
        future = asyncio.run_coroutine_threadsafe(self._session.call_tool(name, arguments=arguments), self._loop)
        try:
            result = future.result(timeout=timeout)
        except FutureTimeout as exc:
            future.cancel()
            raise TimeoutError(f"{self._server} MCP tool {name} timed out") from exc
        except Exception as exc:
            raise RuntimeError(f"{self._server} MCP tool {name} could not complete: {exc}") from exc
        if result.isError:
            message = "; ".join(getattr(item, "text", "") for item in result.content)
            raise RuntimeError(f"{self._server} MCP tool {name} failed: {message[:500]}")
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            if isinstance(structured, dict) and set(structured) == {"result"}:
                return structured["result"]
            return structured
        texts = [item.text for item in result.content if getattr(item, "type", None) == "text"]
        if not texts:
            raise ValueError(f"{self._server} MCP tool {name} returned no text")
        return json.loads(texts[0])


_clients: dict[str, LocalMcpClient] = {}
_lock = threading.Lock()


def call_connector(server: str, name: str, arguments: dict[str, Any]) -> Any:
    if server not in _SERVERS:
        raise ValueError(f"Unknown connector: {server}")
    with _lock:
        if server not in _clients:
            _clients[server] = LocalMcpClient(server)
        client = _clients[server]
    return client.call(name, arguments)


def call_bps(name: str, arguments: dict[str, Any]) -> Any:
    return call_connector("bps", name, arguments)
