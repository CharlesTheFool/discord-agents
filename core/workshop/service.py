"""The workshop service the bot holds: scopes -> sandboxes, the egress proxy's
credentials and allowlists, the template, the disk floor and running cap.

Tools (bash, editor, network access) and the job watcher go through this;
nothing else talks to the backend.
"""
import asyncio
import ctypes
import hashlib
import hmac
import logging
import os
import pathlib
import posixpath
import secrets
import shutil
import socket
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from core.internal_constants import (
    WORKSHOP_BACKGROUND_PER_SCOPE,
    WORKSHOP_COMMIT_PER_SANDBOX_BYTES,
    WORKSHOP_DOWNLOAD_PRESET,
    WORKSHOP_FOREGROUND_CEILING_SECONDS,
    WORKSHOP_FREE_SPACE_FLOOR_BYTES,
    WORKSHOP_IMAGE,
    WORKSHOP_PROVISIONING_ALLOWLIST,
    WORKSHOP_RUNNING_SANDBOXES,
    WORKSHOP_SHELL_JOB_MAX_SECONDS,
    WORKSHOP_TOOL_OUTPUT_CHARS,
)
from core.workshop.backend import ExecutorBackend, FileTooLarge, JobOutcome, SandboxSpec, SandboxUnavailable
from core.workshop.jobs import DONE, FAILED, JobRecord, JobStore
from core.workshop.proxy import EgressGrant, EgressProxy
from core.workshop.scopes import Scope, setting_for
from core.workshop.shell import command_argv, format_result, preview_argv
from core.workshop.state import WorkshopState

logger = logging.getLogger(__name__)

PROXY_HOST = "host.microsandbox.internal"
# Runs once into the template every scope restores from (Phase 0 V2: ~70 s).
# apt goes over https: a CONNECT-only proxy can't carry apt's plain-HTTP requests.
PROVISION_SCRIPT = r"""
set -e
sed -i 's|http://|https://|g' /etc/apt/sources.list.d/debian.sources
apt-get update -q >/dev/null
apt-get install -y -q --no-install-recommends git ffmpeg ripgrep build-essential curl xz-utils ca-certificates >/dev/null
cd /tmp
curl -fsSO https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt
F=$(grep -o 'node-v[0-9.]*-linux-x64.tar.xz' SHASUMS256.txt | head -1)
curl -fsSLO https://nodejs.org/dist/latest-v24.x/$F
grep " $F\$" SHASUMS256.txt | sha256sum -c - >/dev/null
tar -xJf $F -C /usr/local --strip-components=1
rm -f $F SHASUMS256.txt
cd /
pip install -q --no-cache-dir --disable-pip-version-check --root-user-action=ignore python-docx python-pptx openpyxl pypdf reportlab matplotlib pandas pillow requests
apt-get clean && rm -rf /var/lib/apt/lists/*
mkdir -p /workspace /skills
"""
_SECRET_NAMES = (".env", "id_rsa", "id_ed25519")
SKILLS_MARKER = "/skills/.da-fingerprint"   # which skills/ version this sandbox holds
TEMPLATE_RETRY_SECONDS = 300
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


class WorkshopUnavailable(Exception):
    """Said to the model as is: why the workshop can't take work right now."""


@dataclass(frozen=True)
class CommandResult:
    text: str                 # the tool result
    job_id: Optional[str]
    running: bool             # carries on in the background


def install_id(data_root: pathlib.Path) -> str:
    """Short and stable per install: MSB_HOME is per OS user, so two installs
    (a dev checkout and the app) must not reuse each other's sandboxes."""
    return hashlib.sha256(str(data_root.resolve()).lower().encode()).hexdigest()[:8]


def proxy_env(user: str, password: str, port: int) -> Dict[str, str]:
    url = f"http://{user}:{password}@{PROXY_HOST}:{port}"
    # curl reads only lowercase http_proxy; Node's fetch ignores proxies without NODE_USE_ENV_PROXY
    return {"HTTPS_PROXY": url, "https_proxy": url, "HTTP_PROXY": url, "http_proxy": url,
            "NODE_USE_ENV_PROXY": "1"}


def available_commit_bytes() -> Optional[int]:
    """Windows commit headroom (Phase 0 V7: the real per-sandbox cost)."""
    if os.name != "nt":
        return None

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return status.ullAvailPageFile


def running_cap(running: int, available: Optional[int]) -> int:
    """How many sandboxes may run: the constant, lowered when the remaining
    commit charge can't hold another one (keeping 4 GiB for the host)."""
    if available is None:
        return WORKSHOP_RUNNING_SANDBOXES
    more = max(0, (available - 4 * 1024**3) // WORKSHOP_COMMIT_PER_SANDBOX_BYTES)
    return max(1, min(WORKSHOP_RUNNING_SANDBOXES, running + int(more)))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WorkspaceFiles:
    """One scope's /workspace, for tools that move bytes between it and the
    host (send_message attachments, the repository). Paths are judged after
    normalization; the sandbox starts only when bytes actually move."""

    def __init__(self, workshop: "Workshop", scope: Scope):
        self.workshop = workshop
        self.scope = scope

    @staticmethod
    def jail(path: str) -> str:
        norm = posixpath.normpath(path or "")
        if not norm.startswith("/workspace/"):
            raise WorkshopUnavailable(f"{path!r} isn't a file under /workspace")
        return norm

    async def read(self, path: str, *, max_bytes: Optional[int] = None) -> bytes:
        name = await self.workshop.sandbox(self.scope)
        return await self.workshop.backend.read_file(name, self.jail(path), max_bytes=max_bytes)

    async def write(self, path: str, data: bytes) -> str:
        path = self.jail(path)
        name = await self.workshop.sandbox(self.scope)
        await self.workshop.backend.write_file(name, path, data)
        return path


class Workshop:
    def __init__(self, *, config, bot_id: str, data_root: pathlib.Path,
                 backend: ExecutorBackend, jobs: JobStore, vaults, state: WorkshopState,
                 skills_dir: Optional[pathlib.Path] = None, runtime_home: Optional[pathlib.Path] = None):
        self.config = config
        self.bot_id = bot_id
        self.install_id = install_id(data_root)
        self.backend = backend
        self.jobs = jobs
        self.vaults = vaults
        self.state = state
        self.skills_dir = skills_dir
        self.runtime_home = runtime_home or data_root
        self.proxy = EgressProxy(self.authorize)
        self.proxy_port: Optional[int] = None
        self.last_used: Dict[str, float] = {}
        self._ensure_locks = defaultdict(asyncio.Lock)
        self._template_task: Optional[asyncio.Task] = None
        self._template_tried = 0.0                  # monotonic time of the last build attempt
        self._skills_seen: Dict[str, str] = {}      # sandbox -> skills fingerprint it holds
        self._one_shot: Optional[tuple] = None
        self.on_background: Optional[Callable[[JobRecord], None]] = None   # the job watcher

    # --- where and whether ---------------------------------------------------

    def scope(self, server_id, channel_id, *, parent_id=None, is_thread=False) -> Optional[Scope]:
        return self.vaults.resolve_scope(server_id, channel_id, parent_id=parent_id, is_thread=is_thread)

    def setting(self, scope: Optional[Scope], *, server_id=None, channel_id=None, parent_id=None) -> str:
        return setting_for(scope, self.config, server_id=server_id, channel_id=channel_id,
                           parent_id=parent_id)

    def scope_for_turn(self, server_id, channel_id, *, parent_id=None, is_thread=False) -> Scope:
        """The gate every workshop tool call passes, whatever the model was
        offered: a model can emit any tool name, so 'not offered' is not a
        guarantee (the ask_prime precedent)."""
        scope = self.scope(server_id, channel_id, parent_id=parent_id, is_thread=is_thread)
        if self.setting(scope, server_id=server_id, channel_id=channel_id, parent_id=parent_id) != "container":
            raise WorkshopUnavailable("the workshop is off here")
        return scope

    def spec(self, scope: Scope) -> SandboxSpec:
        return SandboxSpec(
            scope.sandbox_name(self.install_id, self.bot_id),
            {"install": self.install_id, "bot": self.bot_id, "scope": scope.key},
            self.proxy_port or 0)

    # --- egress --------------------------------------------------------------

    def authorize(self, user: str, password: str) -> Optional[EgressGrant]:
        if self._one_shot and hmac.compare_digest(f"{user}:{password}", ":".join(self._one_shot)):
            return EgressGrant("provisioning", frozenset(WORKSHOP_PROVISIONING_ALLOWLIST))
        scope_key = self.state.scope_for_user(user)
        if scope_key is None:
            return None
        expected = self.state.data["credentials"][scope_key]["password"]
        if not hmac.compare_digest(password.encode(), expected.encode()):
            return None
        allow, open_internet = self.allowed(scope_key)
        return EgressGrant(scope_key, allow, open_internet=open_internet)

    def allowed(self, scope_key: str):
        """(exact hosts this scope may reach, whether it is open-internet)."""
        scope_id = scope_key.split(":", 1)[1]
        allow = {*WORKSHOP_DOWNLOAD_PRESET, *self.config.workshop.allowlist,
                 *self.state.approved_hosts(scope_key)}
        return frozenset(allow), scope_id in self.config.workshop.open_internet

    def exec_env(self, scope: Scope) -> Dict[str, str]:
        """What every command in this scope carries: its own proxy credential."""
        cred = self.state.credential(scope.key)
        return proxy_env(cred["user"], cred["password"], self.proxy_port)

    # --- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Bind the pinned proxy port, then build the template in the
        background (the first sandbox waits for it)."""
        port = self.state.proxy_port or free_port()
        try:
            self.proxy_port = await self.proxy.start(port)
        except OSError as e:
            raise WorkshopUnavailable(
                f"the workshop's proxy port {port} is taken ({e}); every sandbox is set up "
                f"to reach exactly that port - free it and restart") from None
        if self.state.proxy_port != self.proxy_port:
            self.state.proxy_port = self.proxy_port
        self._template_task = asyncio.ensure_future(self._ensure_template())

    async def close(self) -> None:
        if self._template_task and not self._template_task.done():
            self._template_task.cancel()
        await self.proxy.close()

    def template_fingerprint(self) -> str:
        return hashlib.sha256(f"{WORKSHOP_IMAGE}\n{PROVISION_SCRIPT}".encode()).hexdigest()

    def template_ready(self) -> bool:
        current = self.state.template
        return bool(current and current.get("fingerprint") == self.template_fingerprint()
                    and self.backend.template == current["ref"])

    def _retry_template(self) -> None:
        """A failed build (a network blip mid-provisioning) tries again,
        at most every TEMPLATE_RETRY_SECONDS."""
        if (self._template_task is None or self._template_task.done()) and \
                time.monotonic() - self._template_tried > TEMPLATE_RETRY_SECONDS:
            self._template_task = asyncio.ensure_future(self._ensure_template())

    async def _ensure_template(self) -> None:
        self._template_tried = time.monotonic()
        fingerprint = self.template_fingerprint()
        current = self.state.template
        # state.json can outlive the snapshot it names (a restored backup, a
        # deleted runtime home): trust it only if the runtime still has it
        if current and current.get("fingerprint") == fingerprint:
            try:
                present = await self.backend.has_snapshot(current["ref"])
            except SandboxUnavailable as e:
                logger.error(f"workshop template check failed: {e}")
                return
            if present:
                self.backend.use_template(current["ref"])
                return
            logger.warning(f"workshop template {current['ref']} is gone from the runtime; rebuilding")
        self._one_shot = ("p" + secrets.token_hex(6), secrets.token_urlsafe(32))
        name = f"{self.install_id}-{self.bot_id}-template"
        try:
            ref = await self.backend.build_template(
                name, PROVISION_SCRIPT, env={**proxy_env(*self._one_shot, self.proxy_port),
                                             "DEBIAN_FRONTEND": "noninteractive"},
                proxy_port=self.proxy_port, snapshot=f"tpl-{fingerprint[:12]}")
        except Exception as e:
            logger.error(f"workshop template build failed; new scopes wait for a retry: {e}")
            return
        finally:
            self._one_shot = None   # revoked: the proxy refuses it from here on
        self.state.template = {"ref": ref, "fingerprint": fingerprint}
        self.backend.use_template(ref)
        logger.info(f"workshop template ready: {ref}")

    async def sandbox(self, scope: Scope) -> str:
        """Make this scope's sandbox run; returns its runtime name."""
        free = shutil.disk_usage(self.runtime_home).free
        if free < WORKSHOP_FREE_SPACE_FLOOR_BYTES:
            raise WorkshopUnavailable(
                f"the host's drive is nearly full ({free // 2**20} MiB left); the workshop "
                f"takes no new work below {WORKSHOP_FREE_SPACE_FLOOR_BYTES // 2**30} GiB")
        if self._template_task and not self._template_task.done():
            await asyncio.shield(self._template_task)
        spec = self.spec(scope)
        # A scope made without the template would keep the bare image for
        # good (sandboxes are reused, never re-provisioned): new ones wait
        if not self.template_ready() and not await self.backend.has_sandbox(spec.name):
            self._retry_template()
            raise WorkshopUnavailable(
                "the workshop is still being set up (its base image didn't finish building); "
                "it retries on its own - try again in a few minutes")
        async with self._ensure_locks[spec.name]:
            await self._make_room(spec.name)
            await self.backend.ensure(spec)
            await self._sync_skills(spec.name)
        self.last_used[spec.name] = time.monotonic()
        return spec.name

    def _mine(self, labels: Dict[str, str]) -> bool:
        return labels.get("install") == self.install_id and labels.get("bot") == self.bot_id

    async def _make_room(self, name: str) -> None:
        """Stay under the running cap: stop the least recently used sandbox
        that has no running work. Never stop one that has."""
        running = [i for i in await self.backend.status()
                   if i.status == "running" and self._mine(i.labels)]
        if any(i.name == name for i in running):
            return
        if len(running) < running_cap(len(running), available_commit_bytes()):
            return
        busy = {j.scope_key for j in await self.jobs.running()}
        idle = [i for i in running if i.labels.get("scope") not in busy]
        if not idle:
            raise WorkshopUnavailable(
                "every workshop sandbox this bot may run is busy with running work - "
                "try again when one finishes")
        victim = min(idle, key=lambda i: self.last_used.get(i.name, 0.0))
        logger.info(f"workshop running cap: stopping {victim.name}")
        await self.backend.stop(victim.name)

    # --- commands --------------------------------------------------------------

    async def run_command(self, scope: Scope, executor_key: str, command: str, *,
                          home_server_id, home_channel_id, background: bool = False,
                          timeout_s: Optional[int] = None,
                          ceiling_s: int = WORKSHOP_FOREGROUND_CEILING_SECONDS) -> CommandResult:
        """Every command is a detached job with a jobs row (spec 5.7). A
        foreground one is waited on up to the ceiling; still running then,
        it carries on in the background unless the scope is at its cap, in
        which case it stops (otherwise foreground commands would make
        background work, and the completion turns it costs, unbounded)."""
        if background and await self.jobs.background_count(scope.key) >= WORKSHOP_BACKGROUND_PER_SCOPE:
            return CommandResult(self._at_cap(), None, False)
        name = await self.sandbox(scope)
        job = await self.jobs.create(scope_key=scope.key, home_server_id=home_server_id,
                                     home_channel_id=home_channel_id, kind="shell",
                                     executor_key=executor_key, command=command,
                                     foreground=not background)
        log = f"/workspace/.logs/{job.id}.log"
        limit = min(timeout_s or WORKSHOP_SHELL_JOB_MAX_SECONDS, WORKSHOP_SHELL_JOB_MAX_SECONDS)
        try:
            sandbox_job = await self.backend.start_job(
                name, command_argv(executor_key, log, command), env=self.exec_env(scope),
                timeout_s=limit)
        except Exception as e:
            await self.jobs.mark_finished(job.id, FAILED, result=f"didn't start: {e}", notified=True)
            raise
        await self.jobs.set_sandbox_job(job.id, sandbox_job, log)
        if background:
            self._followed(await self.jobs.get(job.id))
            return CommandResult(
                f"Started in the background as {job.id}; a note lands here when it finishes. "
                f"Its output goes to {log}.", job.id, True)
        try:
            outcome = await self.backend.wait_job(name, sandbox_job, min(ceiling_s, limit + 5))
        except SandboxUnavailable as e:
            # the job may still be running: the watcher settles it either way,
            # so the row never sits in `running` with nobody following it
            await self.jobs.to_background(job.id)
            self._followed(await self.jobs.get(job.id))
            return CommandResult(
                f"Lost track of it while waiting ({e}); it's handed to the background as {job.id}, "
                f"and a note lands here when it's settled. Output so far: {log}.", job.id, True)
        if outcome is None:
            if await self.jobs.background_count(scope.key) >= WORKSHOP_BACKGROUND_PER_SCOPE:
                try:
                    await self.backend.kill_job(name, sandbox_job)
                except SandboxUnavailable as e:
                    logger.warning(f"couldn't stop {job.id} at the ceiling: {e}")
                await self.jobs.mark_finished(
                    job.id, FAILED, notified=True,
                    result="stopped at the foreground ceiling: the scope was at its background cap")
                return CommandResult(
                    f"Stopped after {ceiling_s // 60} minutes: it was still running, and this "
                    f"workshop already has {WORKSHOP_BACKGROUND_PER_SCOPE} jobs in the background. "
                    f"Partial output is in {log}.", job.id, False)
            await self.jobs.to_background(job.id)
            self._followed(await self.jobs.get(job.id))
            return CommandResult(
                f"Still running after {ceiling_s // 60} minutes, so it carries on in the "
                f"background as {job.id}; a note lands here when it finishes. Output so far: {log}.",
                job.id, True)
        await self.finish(job, outcome, notified=True)
        preview = await self.preview(name, log)
        return CommandResult(format_result(outcome.exit_code, preview, log, timed_out=outcome.timed_out),
                             job.id, False)

    def _at_cap(self) -> str:
        return (f"This workshop already has {WORKSHOP_BACKGROUND_PER_SCOPE} jobs running in the "
                f"background - wait for one to finish (a note lands here) before starting another.")

    def _followed(self, job: JobRecord) -> None:
        """Hand a background job to the watcher (completion notes, Task 9)."""
        if self.on_background:
            self.on_background(job)

    async def preview(self, name: str, log: str) -> bytes:
        """The head and tail of a job's log, computed in the guest."""
        path = f"/tmp/{log.rsplit('/', 1)[-1]}.preview"
        try:
            job = await self.backend.start_job(name, preview_argv(log, path), env={})
            await self.backend.wait_job(name, job, 60)
            return await self.backend.read_file(name, path, max_bytes=4 * WORKSHOP_TOOL_OUTPUT_CHARS)
        except (FileNotFoundError, FileTooLarge, SandboxUnavailable, OSError):
            return b"0\n[the output couldn't be read back]"

    # --- boot reconciliation -------------------------------------------------

    async def reconcile(self) -> List[JobRecord]:
        """After a restart: settle every `running` row against the runtime.
        Jobs that finished while the bot was away get their outcome (their
        completion note is still due); jobs whose sandbox or runtime job is
        gone become `interrupted`; the rest are still running and returned
        for the watcher to follow."""
        live = []
        for job in await self.jobs.running():
            kind, _, scope_id = job.scope_key.partition(":")
            name = Scope(kind, scope_id).sandbox_name(self.install_id, self.bot_id)
            if not job.sandbox_job_id:
                await self.jobs.mark_interrupted([job.id], "the bot stopped before the command started")
                continue
            try:
                outcome = await self.backend.wait_job(name, job.sandbox_job_id, 2.0)
            except SandboxUnavailable:
                await self.jobs.mark_interrupted([job.id], "its sandbox stopped while the bot was away")
                continue
            except Exception as e:   # the runtime no longer knows the job
                await self.jobs.mark_interrupted([job.id], f"the sandbox lost track of it ({e})")
                continue
            if outcome is None:
                live.append(job)
            else:
                await self.finish(job, outcome)
        if live:
            logger.info(f"workshop: {len(live)} job(s) still running after restart")
        return live

    async def finish(self, job: JobRecord, outcome: JobOutcome, *, notified: bool = False) -> None:
        if outcome.timed_out:
            await self.jobs.mark_finished(job.id, FAILED, exit_code=outcome.exit_code,
                                          result="stopped at the workshop's time limit", notified=notified)
        else:
            await self.jobs.mark_finished(job.id, DONE if outcome.exit_code == 0 else FAILED,
                                          exit_code=outcome.exit_code, notified=notified)

    # --- skills --------------------------------------------------------------

    def _skill_files(self):
        """Regular files under skills/, symlinks never followed, secrets skipped."""
        if not self.skills_dir or not self.skills_dir.exists():
            return []
        out = []
        for path in sorted(self.skills_dir.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(self.skills_dir)
            if any(part.startswith(".") for part in rel.parts[:-1]):
                continue
            if path.name in _SECRET_NAMES or path.name.startswith(".env") or path.suffix in _SECRET_SUFFIXES:
                continue
            out.append((rel.as_posix(), path))
        return out

    def skills_fingerprint(self) -> str:
        h = hashlib.sha256()
        for rel, path in self._skill_files():
            st = path.stat()
            h.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
        return h.hexdigest()

    async def _sync_skills(self, name: str) -> None:
        """Copy skills/ into /skills as bytes (no mount, spec principle 5),
        when the folder changed since this sandbox last got it. Which
        version a sandbox holds is written inside it, so a recreated
        sandbox can't be mistaken for a synced one."""
        if not self.skills_dir:
            return
        fingerprint = self.skills_fingerprint()
        if self._skills_seen.get(name) == fingerprint:
            return
        try:
            held = (await self.backend.read_file(name, SKILLS_MARKER, max_bytes=256)).decode().strip()
        except (FileNotFoundError, FileTooLarge, OSError):
            held = None
        if held != fingerprint:
            job = await self.backend.start_job(name, ["rm", "-rf", "/skills"], env={})
            await self.backend.wait_job(name, job, 60)
            for rel, path in self._skill_files():
                await self.backend.write_file(name, f"/skills/{rel}", path.read_bytes())
            await self.backend.write_file(name, SKILLS_MARKER, fingerprint.encode())
        self._skills_seen[name] = fingerprint
