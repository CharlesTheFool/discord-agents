"""The dashboard's workshop panel, the bot's half (spec 8.2, 0.15.0).

The bot is the one writer of its workshop state. The supervisor reads what
this publishes (panel.json: each scope's sandbox and disk use, the outcome
of recent requests) and asks for changes by writing request files into
requests/; each is applied on the next tick, deleted, and its outcome
published. Nothing here ever writes a credential where the supervisor reads.
"""
import asyncio
import json
import logging
import os
import pathlib
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Optional

from core.workshop.approvals import normalize_host
from core.workshop.backend import SandboxUnavailable
from core.workshop.scopes import KINDS, Scope
from core.workshop.service import WorkshopUnavailable

logger = logging.getLogger(__name__)

REQUEST_TICK_SECONDS = 5
SNAPSHOT_SECONDS = 60
ACTIONS = ("allow_host", "remove_host", "reset", "wipe", "decide")
SCOPE_KEY = re.compile(r"^(server|vault|dm|dm-channel):\d{1,20}$")
REQUEST_FILE = re.compile(r"^[0-9a-f]{12}\.json$")


class Panel:
    def __init__(self, workshop, folder: pathlib.Path, approvals: Callable = lambda: None):
        self.workshop = workshop
        self.requests = folder / "requests"
        self.snapshot_path = folder / "panel.json"
        self.approvals = approvals           # late-bound: built in on_ready
        self.outcomes = deque(maxlen=20)
        self._published = float("-inf")

    async def run(self) -> None:
        while True:
            try:
                changed = await self.apply_requests()
                if changed or time.monotonic() - self._published >= SNAPSHOT_SECONDS:
                    await self.publish()
            except Exception:
                logger.exception("workshop panel tick failed")
            await asyncio.sleep(REQUEST_TICK_SECONDS)

    # --- requests -------------------------------------------------------------

    async def apply_requests(self) -> bool:
        """Apply and delete every waiting request, oldest first."""
        if not self.requests.is_dir():
            return False
        paths = sorted((p for p in self.requests.iterdir() if REQUEST_FILE.match(p.name)),
                       key=lambda p: p.stat().st_mtime_ns)
        for path in paths:
            request = {}
            try:
                request = json.loads(path.read_text(encoding="utf-8"))
                outcome, ok = await self._apply(request), True
            except (ValueError, WorkshopUnavailable, SandboxUnavailable) as e:
                outcome, ok = str(e), False
            except Exception as e:
                logger.exception(f"workshop panel request {path.name} failed")
                outcome, ok = f"{type(e).__name__}: {e}", False
            path.unlink(missing_ok=True)
            logger.info(f"workshop panel: {request.get('action')} {request.get('scope', '')} -> {outcome}")
            self.outcomes.appendleft({
                "id": path.stem, "action": request.get("action"), "scope": request.get("scope"),
                "host": request.get("host"), "ok": ok, "outcome": outcome,
                "at": datetime.now(timezone.utc).isoformat()})
        return bool(paths)

    async def _apply(self, request: dict) -> str:
        action = request.get("action")
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        if action == "decide":
            approvals = self.approvals()
            if approvals is None:
                raise ValueError("network approvals aren't running yet")
            return await approvals.decide_from_dashboard(str(request.get("approval_id")),
                                                         request.get("allow") is True)
        scope_key = str(request.get("scope") or "")
        if not SCOPE_KEY.match(scope_key):
            raise ValueError(f"{scope_key!r} isn't a workshop scope")
        state = self.workshop.state
        if action == "allow_host":
            host = normalize_host(request.get("host"))
            state.approve_host(scope_key, host)
            return f"{host} allowed"
        if action == "remove_host":
            host = normalize_host(request.get("host"))
            if not state.revoke_host(scope_key, host):
                raise ValueError(f"{host} wasn't allowed there")
            return f"{host} removed"
        kind, _, scope_id = scope_key.partition(":")
        return await self.workshop.reset(Scope(kind, scope_id), wipe=action == "wipe")

    # --- the snapshot ------------------------------------------------------------

    def _scope_of(self, sandbox_name: str) -> Optional[str]:
        """A scope key back from its sandbox name (install-bot-kind-id)."""
        prefix = Scope("server", "0").sandbox_name(self.workshop.install_id,
                                                   self.workshop.bot_id)[:-len("server-0")]
        if not sandbox_name.startswith(prefix):
            return None
        kind, _, scope_id = sandbox_name[len(prefix):].rpartition("-")
        return f"{kind}:{scope_id}" if kind in KINDS and scope_id.isdigit() else None

    async def publish(self) -> None:
        scopes, runtime_error = {}, None
        try:
            for info in await self.workshop.backend.status():
                key = self._scope_of(info.name)
                if key and self.workshop._mine(info.labels):
                    scopes.setdefault(key, {})["sandbox"] = info.status
            for volume in await self.workshop.backend.volume_usage():
                key = self._scope_of(volume.sandbox)
                if key:
                    scopes.setdefault(key, {}).update(used_bytes=volume.used_bytes,
                                                      capacity_bytes=volume.capacity_bytes)
        except Exception as e:   # a runtime hiccup must not blank the rest of the panel
            runtime_error = f"{type(e).__name__}: {e}"
        snapshot = {
            "written_at": datetime.now(timezone.utc).isoformat(),
            "template_ready": self.workshop.template_ready(),
            "proxy_port": self.workshop.proxy_port,
            "runtime_error": runtime_error,
            "scopes": scopes,
            "requests": list(self.outcomes),
        }
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.snapshot_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
        os.replace(tmp, self.snapshot_path)
        self._published = time.monotonic()
