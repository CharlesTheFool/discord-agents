"""The executor backend: what the workshop needs from a sandbox runtime.

Implementations: backends/microsandbox.py (the default, a microVM per scope),
backends/fake.py (in-memory, for tests), and from 0.14.2 a host backend.
Sandboxes are addressed by their runtime name (Scope.sandbox_name); the
backend knows nothing about scopes, vaults or Discord.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol


@dataclass(frozen=True)
class SandboxSpec:
    """Everything `ensure` needs to create or reconnect a scope's sandbox."""
    name: str
    labels: Dict[str, str] = field(default_factory=dict)
    proxy_port: int = 0


@dataclass(frozen=True)
class JobOutcome:
    exit_code: int
    timed_out: bool = False   # the runtime's own timeout (the 6 h ceiling) ended it


@dataclass(frozen=True)
class DirEntry:
    name: str
    kind: str        # file | dir | link
    size: int = 0


@dataclass(frozen=True)
class SandboxInfo:
    name: str
    status: str      # running | stopped | crashed | ...
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class VolumeInfo:
    """A sandbox's /workspace volume (0.15.0, the dashboard's disk use)."""
    sandbox: str                     # the sandbox the volume belongs to
    used_bytes: int
    capacity_bytes: Optional[int]


class SandboxUnavailable(Exception):
    """The runtime can't serve this sandbox (not running, crashed, no
    runtime, didn't answer in time). Every runtime failure surfaces as this
    or as an ordinary file error, never as an SDK type."""


class FileTooLarge(Exception):
    def __init__(self, path: str, size: int):
        super().__init__(f"{path} is {size} bytes")
        self.path = path
        self.size = size


@dataclass(frozen=True)
class BootFailure:
    """Why a test boot failed, said to the operator, and what kind of
    failure it was: "boot" (a VM won't start: virtualization) or "network"
    (the base image couldn't be downloaded)."""
    reason: str
    kind: str = "boot"


class ExecutorBackend(Protocol):
    async def ensure(self, spec: SandboxSpec) -> None:
        """Make the sandbox exist and run: create it, start it, or reconnect."""

    async def start_job(self, name: str, argv: List[str], *, env: Dict[str, str],
                        cwd: str = "/workspace", timeout_s: Optional[int] = None) -> str:
        """Start a detached job; returns the runtime's job id. `env` is per
        exec (proxy settings ride here, never in the sandbox's own env)."""

    async def wait_job(self, name: str, job_id: str,
                       timeout_s: Optional[float]) -> Optional[JobOutcome]:
        """The job's outcome, or None if it is still running after
        `timeout_s`. A wait that times out never kills the job."""

    async def kill_job(self, name: str, job_id: str) -> None: ...

    async def read_file(self, name: str, path: str, *, max_bytes: Optional[int] = None) -> bytes:
        """Raises FileNotFoundError, IsADirectoryError for a directory, or
        FileTooLarge past max_bytes (checked before anything is read)."""

    async def write_file(self, name: str, path: str, data: bytes) -> None: ...

    async def list_dir(self, name: str, path: str) -> List[DirEntry]: ...

    async def exists(self, name: str, path: str) -> bool: ...

    async def stop(self, name: str) -> None: ...

    async def remove(self, name: str, *, keep_volume: bool = True) -> None:
        """Delete the sandbox; its /workspace volume survives unless
        keep_volume is False (the dashboard's wipe)."""

    async def status(self) -> List[SandboxInfo]: ...

    async def volume_usage(self) -> List[VolumeInfo]: ...

    async def has_sandbox(self, name: str) -> bool: ...

    async def has_snapshot(self, ref: str) -> bool:
        """Does the runtime still hold this template snapshot? (state.json
        can outlive it: a restored backup, a deleted runtime home.)"""

    async def test_boot(self) -> Optional[BootFailure]:
        """None if a throwaway sandbox boots; else what the operator must do."""

    async def build_template(self, name: str, script: str, *, env: Dict[str, str],
                             proxy_port: int, snapshot: str) -> str:
        """Provision a template sandbox with `script` (env per exec only),
        snapshot it, and return the snapshot's reference."""

    def use_template(self, ref: Optional[str]) -> None:
        """New sandboxes restore from this snapshot (None: the plain image)."""


class TemplateFailed(Exception):
    """Provisioning exited non-zero; the message carries its output's tail."""
