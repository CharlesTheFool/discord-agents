"""The default executor backend (spec 5.5): one microVM per scope, via
microsandbox, with the runtime pinned to the binaries in its own wheel.

`pin_runtime()` must run before `microsandbox` is first imported: the SDK
reads MSB_PATH / MSB_LIBKRUNFW_PATH / MSB_HOME at import, and an existing
~/.microsandbox install must never win. Everything else imports the SDK
lazily, so a bot with the workshop off never loads it.

Phase 0 facts this relies on (plan B Notes): RESTRICTED denies mount and
sets NoNewPrivs; a disk-kind volume is a hard cap; a managed root disk caps
the root filesystem; the runtime's idle_timeout counts running jobs as
activity and works with the bot down; a timed-out or cancelled wait never
kills a job; TerminateProcess on the bot leaves the VM running; msb keeps
detached stdout on the HOST, so the shell wrapper writes output into the
workspace instead.
"""
import asyncio
import importlib.util
import logging
import os
import pathlib
import platform
import sys
from typing import Dict, List, Optional

from core.internal_constants import (
    WORKSHOP_GUEST_CPUS,
    WORKSHOP_GUEST_MEMORY_MIB,
    WORKSHOP_IDLE_STOP_SECONDS,
    WORKSHOP_IMAGE,
    WORKSHOP_ROOT_DISK_MIB,
    WORKSHOP_VOLUME_MIB,
)
from core.workshop.backend import (DirEntry, JobOutcome, SandboxInfo, SandboxSpec,
                                   SandboxUnavailable, TemplateFailed)

logger = logging.getLogger(__name__)

DEFAULT_HOME = pathlib.Path.home() / ".discord-agents" / "msb"
SDK_CALL_SECONDS = 120.0   # file ops on large files included
# The runtime's Unix socket path is MSB_HOME + ~50 bytes and must fit sun_path
_SOCKET_LIMIT = {"darwin": 104, "linux": 108}
_KINDS = {"file": "file", "directory": "dir", "symlink": "link"}


class RuntimeMissing(Exception):
    """The microsandbox package (and so its bundled runtime) isn't installed."""


def pin_runtime(home: pathlib.Path = DEFAULT_HOME) -> None:
    """Point the SDK at its own bundled msb + libkrunfw and at our MSB_HOME.
    Found from the package's files without importing it (venv or not: never
    by resolving interpreter paths, which are symlinks on macOS)."""
    spec = importlib.util.find_spec("microsandbox")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeMissing("the microsandbox package is not installed")
    bundled = pathlib.Path(next(iter(spec.submodule_search_locations))) / "_bundled"
    exe = bundled / "bin" / ("msb.exe" if os.name == "nt" else "msb")
    libs = sorted((bundled / "lib").glob("libkrunfw*"))
    if not exe.exists() or not libs:
        raise RuntimeMissing(f"the microsandbox wheel has no bundled runtime under {bundled}")
    limit = _SOCKET_LIMIT.get(sys.platform)
    if limit and len(str(home).encode()) + 50 >= limit:
        raise RuntimeMissing(
            f"the sandbox runtime home {home} is too long for a Unix socket path "
            f"({limit} bytes with ~50 appended); use a shorter home directory")
    home.mkdir(parents=True, exist_ok=True)
    os.environ["MSB_PATH"] = str(exe)
    os.environ["MSB_LIBKRUNFW_PATH"] = str(libs[0])
    os.environ["MSB_HOME"] = str(home)


def runtime_available() -> bool:
    return importlib.util.find_spec("microsandbox") is not None


def remediation(error: BaseException) -> str:
    """What the operator does when a test boot fails (spec 5.5)."""
    detail = f"{type(error).__name__}: {error}"
    if isinstance(error, RuntimeMissing):
        return (f"The sandbox runtime is missing ({error}). Reinstall the app's "
                f"dependencies: pip install -r requirements.txt.")
    system = platform.system()
    if system == "Windows":
        return ("The workshop's sandbox couldn't start. Turn on virtualization in "
                "your PC's BIOS/UEFI if it's off, then enable 'Windows Hypervisor "
                "Platform' (Windows Features) and restart. Or set workshop.enabled: "
                f"false to run without it. ({detail})")
    if system == "Linux":
        in_docker = pathlib.Path("/.dockerenv").exists()
        if in_docker:
            return ("The workshop's sandbox needs KVM: run the container with "
                    f"--device /dev/kvm. Or set workshop.enabled: false. ({detail})")
        return ("The workshop's sandbox needs KVM: /dev/kvm must exist and this user "
                "must be in the kvm group (on a VPS, a plan with nested "
                f"virtualization). Or set workshop.enabled: false. ({detail})")
    return f"The workshop's sandbox couldn't start ({detail}). Or set workshop.enabled: false."


class MicrosandboxBackend:
    def __init__(self, *, image: str = WORKSHOP_IMAGE, template: Optional[str] = None,
                 cpus: int = WORKSHOP_GUEST_CPUS, memory_mib: int = WORKSHOP_GUEST_MEMORY_MIB,
                 root_disk_mib: int = WORKSHOP_ROOT_DISK_MIB, volume_mib: int = WORKSHOP_VOLUME_MIB,
                 idle_timeout_s: int = WORKSHOP_IDLE_STOP_SECONDS):
        import microsandbox  # noqa: F401  (pin_runtime ran first)
        self.image = image
        self.template = template          # a snapshot ref new scopes restore from
        self.cpus = cpus
        self.memory_mib = memory_mib
        self.root_disk_mib = root_disk_mib
        self.volume_mib = volume_mib
        self.idle_timeout_s = idle_timeout_s
        self._live: Dict[str, object] = {}   # name -> connected Sandbox
        self._lock = asyncio.Lock()

    @classmethod
    def for_tests(cls) -> "MicrosandboxBackend":
        pin_runtime(pathlib.Path.home() / ".discord-agents" / "msb-test")
        return cls(memory_mib=512, root_disk_mib=2048, volume_mib=256, idle_timeout_s=600)

    @staticmethod
    def volume_name(name: str) -> str:
        return f"{name}-ws"

    def network(self, proxy_port: int):
        """Egress: nothing but TCP to the host's proxy port. DNS is denied
        (host.microsandbox.internal comes from the guest's /etc/hosts)."""
        from microsandbox import (Action, DestGroup, Destination, Network, NetworkPolicy,
                                  Protocol, Rule)
        rules = [*Rule.deny_dns()]
        if proxy_port:
            rules.append(Rule.allow(protocol=Protocol.TCP, port=proxy_port,
                                    destination=Destination.group(DestGroup.HOST)))
        return Network(policy=NetworkPolicy(default_egress=Action.DENY, rules=tuple(rules)))

    async def _volume(self, name: str, labels: Dict[str, str]):
        from microsandbox import Volume, VolumeKind
        from microsandbox.errors import VolumeNotFoundError
        vol = self.volume_name(name)
        try:
            await Volume.get(vol)
        except VolumeNotFoundError:
            await Volume.create(vol, kind=VolumeKind.DISK, size_mib=self.volume_mib, labels=dict(labels))
        return Volume.named(vol)

    async def ensure(self, spec: SandboxSpec) -> None:
        from microsandbox import Image, RootDisk, Sandbox, SecurityProfile
        from microsandbox.errors import SandboxNotFoundError
        async with self._lock:
            live = self._live.get(spec.name)
            if live is not None:
                try:
                    await live.ping()
                    return
                except Exception:
                    self._live.pop(spec.name, None)
            try:
                handle = await Sandbox.get(spec.name)
            except SandboxNotFoundError:
                handle = None
            if handle is not None:
                if str(handle.status) == "running":
                    self._live[spec.name] = await handle.connect()
                else:
                    self._live[spec.name] = await Sandbox.start(spec.name, detached=True)
                return
            volumes = {"/workspace": await self._volume(spec.name, spec.labels)}
            if self.template:
                from microsandbox import ModificationPolicy
                sb = await Sandbox.restore(
                    self.template, name=spec.name, cpus=self.cpus, memory=self.memory_mib,
                    security=SecurityProfile.RESTRICTED, network_policy=self.network(spec.proxy_port).policy,
                    volumes=volumes, idle_timeout=self.idle_timeout_s)
                if spec.labels:
                    await sb.modify(labels=dict(spec.labels), policy=ModificationPolicy.RESTART)
                    sb = await (await Sandbox.get(spec.name)).connect()
            else:
                sb = await Sandbox.create(
                    spec.name, image=Image.oci(self.image, root_disk=RootDisk.managed(self.root_disk_mib)),
                    cpus=self.cpus, memory=self.memory_mib, security=SecurityProfile.RESTRICTED,
                    network=self.network(spec.proxy_port), volumes=volumes, labels=dict(spec.labels),
                    idle_timeout=self.idle_timeout_s, detached=True)
            self._live[spec.name] = sb
            logger.info(f"workshop sandbox {spec.name} created")

    async def _sandbox(self, name: str):
        """The connected sandbox, reconnecting if the runtime restarted it."""
        from microsandbox import Sandbox
        from microsandbox.errors import SandboxNotFoundError
        sb = self._live.get(name)
        if sb is not None:
            return sb
        try:
            handle = await Sandbox.get(name)
        except SandboxNotFoundError:
            raise SandboxUnavailable(f"{name} does not exist") from None
        if str(handle.status) != "running":
            raise SandboxUnavailable(f"{name} is {handle.status}")
        sb = await handle.connect()
        self._live[name] = sb
        return sb

    async def _call(self, name: str, op, timeout_s: float = SDK_CALL_SECONDS):
        """Run one SDK call; a stopped or vanished sandbox becomes
        SandboxUnavailable and is forgotten so the next ensure() reconnects.
        A call that doesn't answer in time counts as unavailable too: a hung
        runtime call must never hang a turn."""
        from microsandbox.errors import SandboxNotRunningError
        sb = await self._sandbox(name)
        try:
            return await asyncio.wait_for(op(sb), timeout_s)
        except SandboxNotRunningError as e:
            self._live.pop(name, None)
            raise SandboxUnavailable(str(e)) from None
        except asyncio.TimeoutError:
            self._live.pop(name, None)
            raise SandboxUnavailable(f"{name} didn't answer within {timeout_s:.0f}s") from None

    async def start_job(self, name: str, argv: List[str], *, env: Dict[str, str],
                        cwd: str = "/workspace", timeout_s: Optional[int] = None) -> str:
        job = await self._call(name, lambda sb: sb.exec_detached(
            argv[0], argv[1:], cwd=cwd, env=dict(env), timeout=timeout_s))
        return job.id

    async def wait_job(self, name: str, job_id: str,
                       timeout_s: Optional[float]) -> Optional[JobOutcome]:
        job = await self._call(name, lambda sb: sb.get_job(job_id))
        waiting = asyncio.ensure_future(job.wait())
        done, _ = await asyncio.wait({waiting}, timeout=timeout_s)
        if not done:
            waiting.cancel()     # Phase 0 V6: cancelling a wait never kills the job
            return None
        exit_ = waiting.result()
        return JobOutcome(int(exit_.code), timed_out=bool(exit_.timed_out))

    async def kill_job(self, name: str, job_id: str) -> None:
        job = await self._call(name, lambda sb: sb.get_job(job_id))
        await job.kill()

    async def read_file(self, name: str, path: str) -> bytes:
        """Stat first: the SDK's fs.read never returns on a directory."""
        from microsandbox.errors import FilesystemError, PathNotFoundError

        async def op(sb):
            meta = await sb.fs.stat(path)
            if str(meta.kind) == "directory":
                raise IsADirectoryError(path)
            return await sb.fs.read(path)
        try:
            return await self._call(name, op)
        except (PathNotFoundError, FilesystemError) as e:
            if isinstance(e, PathNotFoundError) or "no such file" in str(e).lower():
                raise FileNotFoundError(path) from None
            raise

    async def write_file(self, name: str, path: str, data: bytes) -> None:
        parent = path.rsplit("/", 1)[0]

        async def op(sb):
            if parent and not await sb.fs.exists(parent):
                await sb.shell(f"mkdir -p '{parent}'")
            await sb.fs.write(path, data)
        await self._call(name, op)

    async def list_dir(self, name: str, path: str) -> List[DirEntry]:
        entries = await self._call(name, lambda sb: sb.fs.list(path))
        return sorted((DirEntry(e.path.rstrip("/").rsplit("/", 1)[-1],
                                _KINDS.get(str(e.kind), "file"), int(e.size or 0))
                       for e in entries), key=lambda e: e.name)

    async def exists(self, name: str, path: str) -> bool:
        return await self._call(name, lambda sb: sb.fs.exists(path))

    async def stop(self, name: str) -> None:
        from microsandbox import Sandbox
        from microsandbox.errors import SandboxNotFoundError
        self._live.pop(name, None)
        try:
            handle = await Sandbox.get(name)
        except SandboxNotFoundError:
            return
        if str(handle.status) in ("running", "starting", "paused"):
            await handle.stop()

    async def remove(self, name: str, *, keep_volume: bool = True) -> None:
        from microsandbox import Sandbox, Volume
        from microsandbox.errors import SandboxNotFoundError, VolumeNotFoundError
        await self.stop(name)
        try:
            await Sandbox.remove(name)
        except SandboxNotFoundError:
            pass
        if not keep_volume:
            try:
                await Volume.remove(self.volume_name(name))
            except VolumeNotFoundError:
                pass

    async def status(self) -> List[SandboxInfo]:
        from microsandbox import Sandbox
        out, cursor = [], None
        while True:
            page = await Sandbox.list_with(cursor=cursor)
            for h in page.sandboxes:
                labels = (h.config() or {}).get("labels") or {}
                out.append(SandboxInfo(h.name, str(h.status), dict(labels)))
            cursor = page.next_cursor
            if not cursor:
                return out

    def use_template(self, ref: Optional[str]) -> None:
        self.template = ref

    async def build_template(self, name: str, script: str, *, env: Dict[str, str],
                             proxy_port: int, snapshot: str) -> str:
        """Phase 0 V2: provision once, snapshot (disk scope), restore per
        scope. The build sandbox stays (stopped) as the snapshot's group;
        restored sandboxes don't depend on either afterwards."""
        from microsandbox import Image, RootDisk, Sandbox, SecurityProfile, Snapshot
        sb = await Sandbox.create(
            name, image=Image.oci(self.image, root_disk=RootDisk.managed(self.root_disk_mib)),
            cpus=self.cpus, memory=self.memory_mib, security=SecurityProfile.RESTRICTED,
            network=self.network(proxy_port), replace=True)
        try:
            out = await sb.shell(script, env=dict(env), timeout=1800)
            if out.exit_code != 0:
                raise TemplateFailed(f"exit {out.exit_code}: "
                                     f"{(out.stdout_text + out.stderr_text)[-1500:]}")
        finally:
            await sb.stop()
        ref = f"{name}:{snapshot}"
        # Grouped snapshots are immutable (create(force=True) is refused). The
        # name carries the provisioning fingerprint, so one that already exists
        # (a run that died after snapshotting) holds this exact content.
        if not any(s.group == name and s.name == snapshot for s in await Snapshot.list()):
            await Snapshot.create(snapshot, from_sandbox=name)
        return ref

    async def test_boot(self) -> Optional[str]:
        """A throwaway sandbox must boot and run a command."""
        try:
            from microsandbox import Sandbox, SecurityProfile
            name = f"da-boot-check-{os.getpid()}"
            sb = await Sandbox.create(name, image=self.image, memory=512,
                                      security=SecurityProfile.RESTRICTED, replace=True)
            try:
                out = await sb.shell("echo ok", timeout=60)
                if out.stdout_text.strip() != "ok":
                    raise RuntimeError(f"unexpected boot output {out.stdout_text!r}")
            finally:
                await sb.stop()
                await Sandbox.remove(name)
            return None
        except Exception as e:  # anything here means "can't run sandboxes"
            logger.error(f"workshop test boot failed: {e}")
            return remediation(e)
