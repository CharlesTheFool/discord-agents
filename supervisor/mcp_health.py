"""
MCP health poller - the bot's MCPManager checks per-call and never
monitors; the daemon owns the status chips (connected / error /
connecting / disabled), latency, and discovered tool names.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict

import aiohttp

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60


class MCPHealthPoller:
    def __init__(self, load_servers):
        """load_servers: () -> list of mcp_servers.json entries."""
        self._load_servers = load_servers
        self._status: Dict[str, dict] = {}

    def status_for(self, name: str) -> dict:
        return self._status.get(name, {"status": "connecting", "latency_ms": None,
                                       "last_check": None, "tools": []})

    async def check_server(self, server: dict) -> dict:
        name = server.get("name", "?")
        if not server.get("enabled", True):
            result = {"status": "disabled", "latency_ms": None,
                      "last_check": datetime.now(timezone.utc).isoformat(),
                      "tools": []}
            self._status[name] = result
            return result
        url = (server.get("url") or "").rstrip("/")
        if not url:
            result = {"status": "error", "latency_ms": None,
                      "last_check": datetime.now(timezone.utc).isoformat(),
                      "tools": [], "error": "no url configured"}
            self._status[name] = result
            return result
        started = time.monotonic()
        try:
            timeout = aiohttp.ClientTimeout(total=server.get("timeout_seconds", 10))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(f"{url}/mcp/tools",
                                        headers=server.get("headers") or {}) as resp:
                    body = await resp.json()
                    latency = int((time.monotonic() - started) * 1000)
                    if resp.status == 200:
                        result = {
                            "status": "connected",
                            "latency_ms": latency,
                            "last_check": datetime.now(timezone.utc).isoformat(),
                            "tools": [t.get("name", "?")
                                      for t in body.get("tools", [])],
                        }
                    else:
                        result = {"status": "error", "latency_ms": latency,
                                  "last_check": datetime.now(timezone.utc).isoformat(),
                                  "tools": [], "error": f"HTTP {resp.status}"}
        except Exception as e:
            result = {"status": "error", "latency_ms": None,
                      "last_check": datetime.now(timezone.utc).isoformat(),
                      "tools": [], "error": str(e)[:200]}
        self._status[name] = result
        return result

    async def check_all(self) -> None:
        for server in self._load_servers():
            await self.check_server(server)

    async def poll_loop(self) -> None:
        while True:
            try:
                await self.check_all()
            except Exception:
                logger.exception("MCP health poll error")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
