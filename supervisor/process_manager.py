"""
ProcessManager - bot instances as managed subprocesses.

Start/stop/restart `python bot_manager.py spawn <id>` against the managed
root, remember which bots SHOULD be running (supervisor_state.json), and
restart crashes with exponential backoff (cap 5 retries/hour, then mark
crashed and wait for the operator).
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .paths import SupervisorRoot

logger = logging.getLogger(__name__)

CRASH_RETRY_CAP_PER_HOUR = 5
BACKOFF_BASE_SECONDS = 60  # 1, 2, 4, 8, 16 minutes


class ProcessManager:
    def __init__(self, root: SupervisorRoot,
                 spawner: Optional[Callable] = None):
        self.root = root
        self._spawner = spawner or self._real_spawner
        self._procs: Dict[str, object] = {}      # bot_id -> process
        self._started_at: Dict[str, float] = {}
        self._crash_times: Dict[str, List[float]] = {}
        self._next_retry: Dict[str, float] = {}
        self._crashed: Dict[str, bool] = {}      # gave up until operator start
        self._desired: List[str] = self._load_desired()

    # --- spawning ---------------------------------------------------------

    async def _real_spawner(self, bot_id: str):
        return await asyncio.create_subprocess_exec(
            sys.executable, "bot_manager.py", "spawn", bot_id,
            cwd=str(self.root.root),
        )

    # --- desired state ------------------------------------------------------

    def _load_desired(self) -> List[str]:
        path = self.root.supervisor_state()
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8")).get(
                    "desired_running", [])
            except (json.JSONDecodeError, OSError):
                logger.error("Unreadable supervisor state - starting empty")
        return []

    def _save_desired(self) -> None:
        path = self.root.supervisor_state()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"desired_running": self._desired}, indent=2),
                        encoding="utf-8")

    # --- queries ------------------------------------------------------------

    def is_running(self, bot_id: str) -> bool:
        proc = self._procs.get(bot_id)
        return proc is not None and proc.returncode is None

    def status(self, bot_id: str) -> dict:
        proc = self._procs.get(bot_id)
        running = self.is_running(bot_id)
        return {
            "running": running,
            "pid": proc.pid if running else None,
            "uptime_s": int(time.monotonic() - self._started_at[bot_id])
            if running and bot_id in self._started_at else 0,
            "crashed": self._crashed.get(bot_id, False),
        }

    # --- lifecycle ------------------------------------------------------------

    async def start(self, bot_id: str) -> dict:
        if bot_id not in self.root.bot_ids():
            raise ValueError(f"unknown bot: {bot_id}")
        if self.is_running(bot_id):
            return self.status(bot_id)
        self._crashed.pop(bot_id, None)
        self._crash_times.pop(bot_id, None)
        self._next_retry.pop(bot_id, None)
        proc = await self._spawner(bot_id)
        self._procs[bot_id] = proc
        self._started_at[bot_id] = time.monotonic()
        if bot_id not in self._desired:
            self._desired.append(bot_id)
        self._save_desired()
        logger.info(f"Started {bot_id} (pid {proc.pid})")
        return self.status(bot_id)

    async def stop(self, bot_id: str) -> dict:
        if bot_id in self._desired:
            self._desired.remove(bot_id)
            self._save_desired()
        proc = self._procs.get(bot_id)
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                logger.warning(f"{bot_id} ignored SIGTERM - killing")
                proc.kill()
                await proc.wait()
        self._procs.pop(bot_id, None)
        logger.info(f"Stopped {bot_id}")
        return self.status(bot_id)

    async def restart(self, bot_id: str) -> dict:
        await self.stop(bot_id)
        return await self.start(bot_id)

    # --- crash watching ------------------------------------------------------

    async def check_crashed(self) -> None:
        """One pass: respawn exited-but-desired bots, with backoff + cap.
        Called every few seconds by the watch loop."""
        now = time.monotonic()
        for bot_id in list(self._desired):
            proc = self._procs.get(bot_id)
            if proc is None or proc.returncode is None:
                continue  # never started here, or still alive
            if self._crashed.get(bot_id):
                continue
            if now < self._next_retry.get(bot_id, 0):
                continue

            window = [t for t in self._crash_times.get(bot_id, [])
                      if now - t < 3600]
            if len(window) >= CRASH_RETRY_CAP_PER_HOUR:
                logger.error(
                    f"{bot_id} crashed {len(window)} times in an hour - "
                    f"giving up until the operator starts it")
                self._crashed[bot_id] = True
                self._crash_times[bot_id] = window
                continue

            window.append(now)
            self._crash_times[bot_id] = window
            self._next_retry[bot_id] = now + BACKOFF_BASE_SECONDS * (2 ** (len(window) - 1))
            logger.warning(f"{bot_id} exited (code {proc.returncode}) - restarting")
            new_proc = await self._spawner(bot_id)
            self._procs[bot_id] = new_proc
            self._started_at[bot_id] = now

    async def watch_loop(self, interval: float = 5.0) -> None:
        while True:
            try:
                await self.check_crashed()
            except Exception:
                logger.exception("Crash watcher error")
            await asyncio.sleep(interval)

    async def shutdown(self) -> None:
        """Stop every managed process WITHOUT clearing desired state - the
        bots come back when the supervisor does."""
        desired_snapshot = list(self._desired)
        for bot_id in list(self._procs):
            await self.stop(bot_id)
        self._desired = desired_snapshot
        self._save_desired()
