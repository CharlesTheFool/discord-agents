"""In-memory executor backend for tests: a dict filesystem per sandbox and
scripted jobs, so tool, job and approval tests run without a runtime.

A runner decides what a job does: `runner(sandbox, argv, env) -> (exit_code,
seconds)`, and may write files into `sandbox.files` (e.g. a job's log). The
default understands `sleep S` and `sh -c "exit N"`; everything else exits 0.
"""
import asyncio
import itertools
import posixpath
import shlex
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from core.workshop.backend import (DirEntry, JobOutcome, SandboxInfo, SandboxSpec,
                                   SandboxUnavailable, TemplateFailed)


@dataclass
class FakeSandbox:
    spec: SandboxSpec
    running: bool = True
    root: Dict[str, bytes] = field(default_factory=dict)       # lost on remove
    workspace: Dict[str, bytes] = field(default_factory=dict)  # the volume: survives remove
    jobs: Dict[str, asyncio.Task] = field(default_factory=dict)
    execs: List[Tuple[List[str], Dict[str, str], str]] = field(default_factory=list)

    def store(self, path: str) -> Dict[str, bytes]:
        return self.workspace if path == "/workspace" or path.startswith("/workspace/") else self.root

    @property
    def files(self) -> Dict[str, bytes]:
        """Both stores, for runners and assertions."""
        return {**self.root, **self.workspace}

    def write(self, path: str, data: bytes) -> None:
        self.store(path)[path] = data


Runner = Callable[[FakeSandbox, List[str], Dict[str, str]], Tuple[int, float]]


def default_runner(sandbox: FakeSandbox, argv: List[str], env: Dict[str, str]):
    if argv[:1] == ["sleep"]:
        return 0, float(argv[1])
    if argv[:2] == ["sh", "-c"]:
        words = shlex.split(argv[2])
        if words[:1] == ["exit"]:
            return int(words[1]), 0.0
    return 0, 0.0


class FakeBackend:
    def __init__(self, runner: Runner = default_runner, boot_error: Optional[str] = None,
                 template_error: Optional[str] = None):
        self.runner = runner
        self.boot_error = boot_error
        self.template_error = template_error
        self.template: Optional[str] = None
        self.template_builds: List[dict] = []
        self.sandboxes: Dict[str, FakeSandbox] = {}
        self.volumes: Dict[str, Dict[str, bytes]] = {}   # survive remove(keep_volume=True)
        self._ids = itertools.count(1)

    def _running(self, name: str) -> FakeSandbox:
        sb = self.sandboxes.get(name)
        if sb is None or not sb.running:
            raise SandboxUnavailable(f"{name} is not running")
        return sb

    async def ensure(self, spec: SandboxSpec) -> None:
        sb = self.sandboxes.get(spec.name)
        if sb is None:
            root = {"/etc/template": self.template.encode()} if self.template else {}
            sb = FakeSandbox(spec, root=root, workspace=self.volumes.setdefault(spec.name, {}))
            self.sandboxes[spec.name] = sb
        sb.running = True

    async def start_job(self, name: str, argv: List[str], *, env: Dict[str, str],
                        cwd: str = "/workspace", timeout_s: Optional[int] = None) -> str:
        sb = self._running(name)
        sb.execs.append((list(argv), dict(env), cwd))
        job_id = f"fakejob_{next(self._ids)}"

        async def run() -> JobOutcome:
            code, seconds = self.runner(sb, list(argv), dict(env))
            if timeout_s is not None and seconds > timeout_s:
                await asyncio.sleep(timeout_s)
                return JobOutcome(-1, timed_out=True)
            await asyncio.sleep(seconds)
            return JobOutcome(code)

        sb.jobs[job_id] = asyncio.ensure_future(run())
        return job_id

    async def wait_job(self, name: str, job_id: str,
                       timeout_s: Optional[float]) -> Optional[JobOutcome]:
        task = self._running(name).jobs[job_id]
        done, _ = await asyncio.wait({task}, timeout=timeout_s)
        if not done:
            return None
        if task.cancelled():
            return JobOutcome(-1)
        return task.result()

    async def kill_job(self, name: str, job_id: str) -> None:
        self._running(name).jobs[job_id].cancel()

    async def read_file(self, name: str, path: str) -> bytes:
        sb = self._running(name)
        try:
            return sb.store(path)[path]
        except KeyError:
            if any(p.startswith(path.rstrip("/") + "/") for p in sb.files):
                raise IsADirectoryError(path) from None
            raise FileNotFoundError(path) from None

    async def write_file(self, name: str, path: str, data: bytes) -> None:
        self._running(name).write(path, data)

    async def list_dir(self, name: str, path: str) -> List[DirEntry]:
        sb = self._running(name)
        prefix = path.rstrip("/") + "/"
        entries = {}
        for p, data in sb.files.items():
            if p.startswith(prefix):
                head, _, rest = p[len(prefix):].partition("/")
                entries[head] = DirEntry(head, "dir" if rest else "file", 0 if rest else len(data))
        return sorted(entries.values(), key=lambda e: e.name)

    async def exists(self, name: str, path: str) -> bool:
        sb = self._running(name)
        path = posixpath.normpath(path)
        return path in sb.files or any(p.startswith(path.rstrip("/") + "/") for p in sb.files)

    async def stop(self, name: str) -> None:
        if name in self.sandboxes:
            self.sandboxes[name].running = False

    async def remove(self, name: str, *, keep_volume: bool = True) -> None:
        self.sandboxes.pop(name, None)
        if not keep_volume:
            self.volumes.pop(name, None)

    async def status(self) -> List[SandboxInfo]:
        return [SandboxInfo(n, "running" if sb.running else "stopped", dict(sb.spec.labels))
                for n, sb in self.sandboxes.items()]

    async def test_boot(self) -> Optional[str]:
        return self.boot_error

    async def build_template(self, name: str, script: str, *, env: Dict[str, str],
                             proxy_port: int, snapshot: str) -> str:
        self.template_builds.append({"name": name, "script": script, "env": dict(env),
                                     "proxy_port": proxy_port, "snapshot": snapshot})
        if self.template_error:
            raise TemplateFailed(self.template_error)
        return f"{name}:{snapshot}"

    def use_template(self, ref: Optional[str]) -> None:
        self.template = ref
