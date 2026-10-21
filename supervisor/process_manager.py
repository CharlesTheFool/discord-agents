"""
ProcessManager - bot instances as managed subprocesses.

Start/stop/restart `python bot_manager.py spawn <id>` against the managed
root, remember which bots SHOULD be running (supervisor_state.json), and
restart crashes with exponential backoff (cap 5 retries/hour, then mark
crashed and wait for the operator). A bot that exits with
START_BLOCKED_EXIT_CODE (its workshop can't start) is held as blocked
instead, and retried every BLOCKED_RETRY_SECONDS: fast restarts can't fix a
missing hypervisor, but a registry hiccup or a reboot that turned
virtualization on shouldn't need the operator either.
"""

import asyncio
import json
import os
import re
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.config import START_BLOCKED_EXIT_CODE

from .paths import SupervisorRoot

logger = logging.getLogger(__name__)

CRASH_RETRY_CAP_PER_HOUR = 5
BACKOFF_BASE_SECONDS = 60  # 1, 2, 4, 8, 16 minutes
BLOCKED_RETRY_SECONDS = 600


def _spawns(command_line: str, script: Path, bot_id: str) -> bool:
    """Does this command line run `<script> spawn <bot_id>` (quotes and, on
    Windows, case and slashes aside; `spawn alpha` isn't `spawn alpha-2`)?"""
    norm = lambda s: os.path.normcase(s.replace('"', ""))
    return re.search(re.escape(norm(f"{script} spawn {bot_id}")) + r"(\s|$)",
                     norm(command_line or "")) is not None


def find_orphan_pids(bot_id: str, script: Path, owned: Optional[List[int]] = None) -> List[int]:
    """Best-effort process-table scan for this install's `<script> spawn
    <bot_id>` processes this daemon doesn't own (e.g. a previous daemon was
    killed without shutting its bots down). Only this install's own script
    counts: a dev checkout and the app on one machine can share a bot id.
    A venv shim spawns the real interpreter as a child with an identical
    command line, so exclusion covers owned pids AND their direct children.
    Returns [] on any platform hiccup."""
    owned_set = set(owned or [])
    found = []  # (pid, ppid)
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\""
                 " | Select-Object ProcessId,ParentProcessId,CommandLine"
                 " | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=15).stdout
            rows = json.loads(out) if out.strip() else []
            if isinstance(rows, dict):
                rows = [rows]
            for r in rows:
                if _spawns(r.get("CommandLine"), script, bot_id):
                    found.append((int(r["ProcessId"]),
                                  int(r.get("ParentProcessId") or 0)))
        else:
            out = subprocess.run(["ps", "-eo", "pid,ppid,args"],
                                 capture_output=True, text=True, timeout=15).stdout
            for line in out.splitlines():
                if _spawns(line, script, bot_id):
                    parts = line.split(None, 2)
                    found.append((int(parts[0]), int(parts[1])))
    except Exception as e:
        logger.debug(f"Orphan scan failed: {e}")
        return []
    return [pid for pid, ppid in found
            if pid not in owned_set and ppid not in owned_set]


def kill_pids(pids: List[int]) -> None:
    """Kill exactly these processes, never their trees: the bot's sandbox
    VMMs are its children and must outlive it, as they do a normal stop
    (spec 5.5). find_orphan_pids lists the venv shim and its interpreter
    separately, so each gets its own kill."""
    for pid in pids:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                               capture_output=True, timeout=15)
            else:
                import os
                import signal
                os.kill(pid, signal.SIGTERM)
        except Exception as e:
            logger.warning(f"Could not kill orphan pid {pid}: {e}")


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
        self._blocked: Dict[str, bool] = {}      # start gate refused; until operator start
        self._desired: List[str] = self._load_desired()

    # --- spawning ---------------------------------------------------------

    async def _real_spawner(self, bot_id: str):
        # Absolute script path: with a code_root/data_root split the script
        # lives in the install while cwd stays the data root. In the git
        # model both are the same directory - identical to the old behavior.
        return await asyncio.create_subprocess_exec(
            sys.executable, str(self.root.bot_manager_script()), "spawn", bot_id,
            cwd=str(self.root.root),
        )

    def _owned_pids(self) -> List[int]:
        return [p.pid for p in self._procs.values() if p.returncode is None]

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
            "blocked": self._blocked.get(bot_id, False),
        }

    # --- lifecycle ------------------------------------------------------------

    async def start(self, bot_id: str) -> dict:
        if bot_id not in self.root.bot_ids():
            raise ValueError(f"unknown bot: {bot_id}")
        if self.is_running(bot_id):
            return self.status(bot_id)
        # Orphan guard: a bot process left over from a killed daemon would
        # double-login on the same token. Reclaim it before spawning.
        orphans = find_orphan_pids(bot_id, self.root.bot_manager_script(), owned=self._owned_pids())
        if orphans:
            logger.warning(f"{bot_id} already running unmanaged "
                           f"(pids {orphans}) - reclaiming")
            kill_pids(orphans)
            self.root.running_flag(bot_id).unlink(missing_ok=True)
            await asyncio.sleep(1.0)
        self._crashed.pop(bot_id, None)
        self._blocked.pop(bot_id, None)
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

    async def start_desired(self) -> None:
        """Boot-time restore: bring up every bot that SHOULD be running
        (supervisor_state.json). This is the other half of shutdown()'s
        promise - close the app, reopen it, your bots come back."""
        for bot_id in list(self._desired):
            if bot_id not in self.root.bot_ids():
                logger.warning(f"Desired bot {bot_id} no longer exists - dropping")
                self._desired.remove(bot_id)
                self._save_desired()
                continue
            if self.is_running(bot_id):
                continue
            try:
                await self.start(bot_id)
                logger.info(f"Restored desired bot {bot_id}")
            except Exception:
                logger.exception(f"Could not restore {bot_id}")

    # --- crash watching ------------------------------------------------------

    async def check_crashed(self) -> None:
        """One pass: respawn exited-but-desired bots, with backoff + cap.
        Called every few seconds by the watch loop."""
        now = time.monotonic()
        for bot_id in list(self._desired):
            proc = self._procs.get(bot_id)
            if proc is None or proc.returncode is None:
                if proc is not None:
                    self._blocked.pop(bot_id, None)   # a retry got past its start gate
                continue  # never started here, or still alive
            if self._crashed.get(bot_id):
                continue
            if proc.returncode == START_BLOCKED_EXIT_CODE:
                if not self._blocked.get(bot_id):
                    logger.error(f"{bot_id} can't start (its workshop is blocked) - "
                                 f"retrying every {BLOCKED_RETRY_SECONDS // 60} minutes")
                    self._blocked[bot_id] = True
                    self._next_retry[bot_id] = now + BLOCKED_RETRY_SECONDS
                    continue
                if now < self._next_retry.get(bot_id, 0):
                    continue
                self._next_retry[bot_id] = now + BLOCKED_RETRY_SECONDS
                self._procs[bot_id] = await self._spawner(bot_id)   # not a crash: no backoff count
                self._started_at[bot_id] = now
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
