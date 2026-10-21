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
import shlex
import sys
from collections import defaultdict
from typing import Dict, List, Optional

from core.internal_constants import (
    WORKSHOP_GUEST_CPUS,
    WORKSHOP_GUEST_MEMORY_MIB,
    WORKSHOP_IDLE_STOP_SECONDS,
    WORKSHOP_IMAGE,
    WORKSHOP_ROOT_DISK_MIB,
    WORKSHOP_VOLUME_MIB,
)
from core.workshop.backend import (BootFailure, DirEntry, FileTooLarge, JobOutcome, SandboxInfo,
                                   SandboxSpec, SandboxUnavailable, TemplateFailed, VolumeInfo)

logger = logging.getLogger(__name__)

DEFAULT_HOME = pathlib.Path.home() / ".discord-agents" / "msb"
SDK_CALL_SECONDS = 120.0   # file ops on large files included
CREATE_SECONDS = 600.0     # a create may pull the image first
BOOT_SECONDS = 180.0       # one test-boot attempt
BOOT_RETRY_SECONDS = (5, 15)   # transient runtime or registry hiccups get two more tries
# The runtime's Unix socket path is MSB_HOME + ~50 bytes and must fit sun_path
_SOCKET_LIMIT = {"darwin": 104, "linux": 108}
_KINDS = {"file": "file", "directory": "dir", "symlink": "link"}


class RuntimeMissing(Exception):
    """The microsandbox package (and so its bundled runtime) isn't installed."""


def _allocated_bytes(path: pathlib.Path) -> Optional[int]:
    """Bytes a (sparse) file really takes on disk, or None."""
    try:
        if not path.is_file():
            return None
        if os.name == "nt":
            import ctypes
            size = ctypes.WinDLL("kernel32", use_last_error=True).GetCompressedFileSizeW
            size.restype = ctypes.c_ulong
            high = ctypes.c_ulong(0)
            low = size(str(path), ctypes.byref(high))
            if low == 0xFFFFFFFF and ctypes.get_last_error():
                return None
            return (high.value << 32) + low
        return path.stat().st_blocks * 512
    except OSError:
        return None


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
    if isinstance(error, asyncio.TimeoutError):
        return (f"The workshop's test sandbox didn't boot within {BOOT_SECONDS:.0f} seconds. "
                f"The bot retries on its own; if it keeps failing, check virtualization (see "
                f"DEPLOYMENT.md) or set workshop.enabled: false.")
    if _is_download_failure(error):
        return (f"The workshop couldn't download its base image ({detail}). Check the "
                f"internet connection; the bot retries on its own.")
    if isinstance(error, (RuntimeMissing, ImportError)):   # installed wrong is installed
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


def _is_download_failure(error: BaseException) -> bool:
    try:
        from microsandbox.errors import CloudHttpError, ImagePullFailedError, TlsError
    except ImportError:
        return False
    return isinstance(error, (ImagePullFailedError, CloudHttpError, TlsError))


async def _bounded(what: str, coro, timeout_s: float, passthrough=()):
    """A runtime call that isn't an op on a connected sandbox: bounded in
    time, and every runtime failure (other than `passthrough`) becomes
    SandboxUnavailable, which the tools report instead of crashing a turn."""
    from microsandbox.errors import MicrosandboxError
    try:
        return await asyncio.wait_for(coro, timeout_s)
    except passthrough:
        raise
    except asyncio.TimeoutError:
        raise SandboxUnavailable(f"{what} didn't answer within {timeout_s:.0f}s") from None
    except MicrosandboxError as e:
        raise SandboxUnavailable(f"{what}: {e}") from None


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
        self._locks = defaultdict(asyncio.Lock)   # per sandbox: one slow create never blocks another

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
            await _bounded("volume lookup", Volume.get(vol), SDK_CALL_SECONDS, (VolumeNotFoundError,))
        except VolumeNotFoundError:
            await _bounded("volume create", Volume.create(vol, kind=VolumeKind.DISK, size_mib=self.volume_mib,
                                                          labels=dict(labels)), SDK_CALL_SECONDS)
        return Volume.named(vol)

    async def _handle(self, name: str):
        """The runtime's handle for this sandbox, or None if it doesn't exist."""
        from microsandbox import Sandbox
        from microsandbox.errors import SandboxNotFoundError
        try:
            return await _bounded(f"{name} lookup", Sandbox.get(name), SDK_CALL_SECONDS,
                                  (SandboxNotFoundError,))
        except SandboxNotFoundError:
            return None

    async def ensure(self, spec: SandboxSpec) -> None:
        from microsandbox import Image, RootDisk, Sandbox, SecurityProfile
        async with self._locks[spec.name]:
            live = self._live.get(spec.name)
            if live is not None:
                try:
                    await asyncio.wait_for(live.ping(), SDK_CALL_SECONDS)
                    return
                except Exception:
                    self._live.pop(spec.name, None)
            handle = await self._handle(spec.name)
            if handle is not None:
                if str(handle.status) == "running":
                    self._live[spec.name] = await _bounded(f"{spec.name} connect", handle.connect(),
                                                           SDK_CALL_SECONDS)
                else:
                    self._live[spec.name] = await _bounded(
                        f"{spec.name} start", Sandbox.start(spec.name, detached=True), CREATE_SECONDS)
                return
            volumes = {"/workspace": await self._volume(spec.name, spec.labels)}
            if self.template:
                sb = await self._restore(spec, volumes)
            else:
                sb = await _bounded(f"{spec.name} create", Sandbox.create(
                    spec.name, image=Image.oci(self.image, root_disk=RootDisk.managed(self.root_disk_mib)),
                    cpus=self.cpus, memory=self.memory_mib, security=SecurityProfile.RESTRICTED,
                    network=self.network(spec.proxy_port), volumes=volumes, labels=dict(spec.labels),
                    idle_timeout=self.idle_timeout_s, detached=True), CREATE_SECONDS)
            self._live[spec.name] = sb
            logger.info(f"workshop sandbox {spec.name} created")

    async def _restore(self, spec: SandboxSpec, volumes):
        """Restore from the template, then label it (restore takes no labels).
        Both or neither: a sandbox left unlabelled would be invisible to the
        running cap and to the supervisor's Stop, so a failed label step
        removes it again (its /workspace volume stays)."""
        from microsandbox import ModificationPolicy, Sandbox, SecurityProfile
        sb = await _bounded(f"{spec.name} restore", Sandbox.restore(
            self.template, name=spec.name, cpus=self.cpus, memory=self.memory_mib,
            security=SecurityProfile.RESTRICTED, network_policy=self.network(spec.proxy_port).policy,
            volumes=volumes, idle_timeout=self.idle_timeout_s), CREATE_SECONDS)
        if not spec.labels:
            return sb
        try:
            await _bounded(f"{spec.name} labels", sb.modify(labels=dict(spec.labels),
                                                             policy=ModificationPolicy.RESTART),
                           SDK_CALL_SECONDS)
            handle = await self._handle(spec.name)
            if handle is None:
                raise SandboxUnavailable(f"{spec.name} vanished after its restart")
            return await _bounded(f"{spec.name} connect", handle.connect(), SDK_CALL_SECONDS)
        except SandboxUnavailable:
            try:
                await self.remove(spec.name)
            except SandboxUnavailable as e:
                logger.error(f"couldn't remove half-made sandbox {spec.name}: {e}")
            raise

    async def _sandbox(self, name: str):
        """The connected sandbox, reconnecting if the runtime restarted it."""
        sb = self._live.get(name)
        if sb is not None:
            return sb
        handle = await self._handle(name)
        if handle is None:
            raise SandboxUnavailable(f"{name} does not exist")
        if str(handle.status) != "running":
            raise SandboxUnavailable(f"{name} is {handle.status}")
        sb = await _bounded(f"{name} connect", handle.connect(), SDK_CALL_SECONDS)
        self._live[name] = sb
        return sb

    async def _call(self, name: str, op, timeout_s: float = SDK_CALL_SECONDS):
        """Run one SDK call; a stopped or vanished sandbox becomes
        SandboxUnavailable and is forgotten so the next ensure() reconnects.
        A call that doesn't answer in time counts as unavailable too: a hung
        runtime call must never hang a turn."""
        from microsandbox.errors import (FilesystemError, MicrosandboxError, PathNotFoundError,
                                         SandboxNotRunningError)
        sb = await self._sandbox(name)
        try:
            return await asyncio.wait_for(op(sb), timeout_s)
        except PathNotFoundError as e:
            raise FileNotFoundError(str(e)) from None
        except FilesystemError as e:   # an ordinary file problem, said as one
            if "no such file" in str(e).lower():
                raise FileNotFoundError(str(e)) from None
            raise OSError(str(e)) from None
        except SandboxNotRunningError as e:
            self._live.pop(name, None)
            raise SandboxUnavailable(str(e)) from None
        except asyncio.TimeoutError:
            self._live.pop(name, None)
            raise SandboxUnavailable(f"{name} didn't answer within {timeout_s:.0f}s") from None
        except MicrosandboxError as e:
            raise SandboxUnavailable(f"{name}: {e}") from None

    async def start_job(self, name: str, argv: List[str], *, env: Dict[str, str],
                        cwd: str = "/workspace", timeout_s: Optional[int] = None) -> str:
        job = await self._call(name, lambda sb: sb.exec_detached(
            argv[0], argv[1:], cwd=cwd, env=dict(env), timeout=timeout_s))
        return job.id

    async def wait_job(self, name: str, job_id: str,
                       timeout_s: Optional[float]) -> Optional[JobOutcome]:
        from microsandbox.errors import MicrosandboxError
        job = await self._call(name, lambda sb: sb.get_job(job_id))
        waiting = asyncio.ensure_future(job.wait())
        done, _ = await asyncio.wait({waiting}, timeout=timeout_s)
        if not done:
            waiting.cancel()     # Phase 0 V6: cancelling a wait never kills the job
            return None
        try:
            exit_ = waiting.result()
        except MicrosandboxError as e:
            self._live.pop(name, None)
            raise SandboxUnavailable(f"{name}: waiting on {job_id}: {e}") from None
        return JobOutcome(int(exit_.code), timed_out=bool(exit_.timed_out))

    async def kill_job(self, name: str, job_id: str) -> None:
        job = await self._call(name, lambda sb: sb.get_job(job_id))
        await job.kill()

    async def read_file(self, name: str, path: str, *, max_bytes: Optional[int] = None) -> bytes:
        """Stat first: the SDK's fs.read never returns on a directory, and a
        size cap must hold before a multi-GB log is pulled into memory."""
        async def op(sb):
            meta = await sb.fs.stat(path)
            if str(meta.kind) == "directory":
                raise IsADirectoryError(path)
            if max_bytes is not None and int(meta.size or 0) > max_bytes:
                raise FileTooLarge(path, int(meta.size))
            return await sb.fs.read(path)
        try:
            return await self._call(name, op)
        except FileNotFoundError:
            raise FileNotFoundError(path) from None

    async def write_file(self, name: str, path: str, data: bytes) -> None:
        parent = path.rsplit("/", 1)[0]

        async def op(sb):
            if parent and not await sb.fs.exists(parent):
                await sb.shell(f"mkdir -p {shlex.quote(parent)}")
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
        self._live.pop(name, None)
        handle = await self._handle(name)
        if handle is not None and str(handle.status) in ("running", "starting", "paused"):
            await _bounded(f"{name} stop", handle.stop(), SDK_CALL_SECONDS)

    async def remove(self, name: str, *, keep_volume: bool = True) -> None:
        from microsandbox import Sandbox, Volume
        from microsandbox.errors import SandboxNotFoundError, VolumeNotFoundError
        await self.stop(name)
        try:
            await _bounded(f"{name} remove", Sandbox.remove(name), SDK_CALL_SECONDS, (SandboxNotFoundError,))
        except SandboxNotFoundError:
            pass
        if not keep_volume:
            try:
                await _bounded(f"{name} volume remove", Volume.remove(self.volume_name(name)),
                               SDK_CALL_SECONDS, (VolumeNotFoundError,))
            except VolumeNotFoundError:
                pass

    async def has_sandbox(self, name: str) -> bool:
        return await self._handle(name) is not None

    async def has_snapshot(self, ref: str) -> bool:
        from microsandbox import Snapshot
        group, _, snap = ref.partition(":")
        return any(s.group == group and s.name == snap
                   for s in await _bounded("snapshot list", Snapshot.list(), SDK_CALL_SECONDS))

    async def status(self) -> List[SandboxInfo]:
        from microsandbox import Sandbox
        out, cursor = [], None
        while True:
            page = await _bounded("sandbox list", Sandbox.list_with(cursor=cursor), SDK_CALL_SECONDS)
            for h in page.sandboxes:
                labels = (h.config() or {}).get("labels") or {}
                out.append(SandboxInfo(h.name, str(h.status), dict(labels)))
            cursor = page.next_cursor
            if not cursor:
                return out

    async def volume_usage(self) -> List[VolumeInfo]:
        """A disk volume is a sparse disk.raw: the SDK reports 0 used, so
        what it takes from the host drive is read from the file itself."""
        from microsandbox import Volume
        suffix = self.volume_name("")
        home = pathlib.Path(os.environ.get("MSB_HOME") or DEFAULT_HOME)
        return [VolumeInfo(v.name[:-len(suffix)],
                           _allocated_bytes(home / "volumes" / v.name / "disk.raw") or v.used_bytes,
                           v.capacity_bytes)
                for v in await _bounded("volume list", Volume.list(), SDK_CALL_SECONDS)
                if v.name.endswith(suffix)]

    def use_template(self, ref: Optional[str]) -> None:
        self.template = ref

    async def build_template(self, name: str, script: str, *, env: Dict[str, str],
                             proxy_port: int, snapshot: str) -> str:
        """Phase 0 V2: provision once, snapshot (disk scope), restore per
        scope. The build sandbox stays (stopped) as the snapshot's group;
        restored sandboxes don't depend on either afterwards."""
        from microsandbox import Image, RootDisk, Sandbox, SecurityProfile, Snapshot
        # A provisioning change leaves the previous template's snapshot behind
        # (10 GiB apparent). Restored scopes don't depend on it (Phase 0 V2).
        for old in await _bounded("snapshot list", Snapshot.list(), SDK_CALL_SECONDS):
            if old.group == name and old.name != snapshot:
                try:
                    await _bounded("old template removal", Snapshot.remove(f"{name}:{old.name}"),
                                   SDK_CALL_SECONDS)
                    logger.info(f"removed the previous workshop template {name}:{old.name}")
                except SandboxUnavailable as e:
                    logger.warning(f"couldn't remove the previous template {name}:{old.name}: {e}")
        sb = await _bounded("template create", Sandbox.create(
            name, image=Image.oci(self.image, root_disk=RootDisk.managed(self.root_disk_mib)),
            cpus=self.cpus, memory=self.memory_mib, security=SecurityProfile.RESTRICTED,
            network=self.network(proxy_port), replace=True), CREATE_SECONDS)
        try:
            out = await _bounded("template provisioning", sb.shell(script, env=dict(env), timeout=1800),
                                 1860)
            if out.exit_code != 0:
                raise TemplateFailed(f"exit {out.exit_code}: "
                                     f"{(out.stdout_text + out.stderr_text)[-1500:]}")
        finally:
            await _bounded("template stop", sb.stop(), SDK_CALL_SECONDS)
        ref = f"{name}:{snapshot}"
        # Grouped snapshots are immutable (create(force=True) is refused). The
        # name carries the provisioning fingerprint, so one that already exists
        # (a run that died after snapshotting) holds this exact content.
        if not await self.has_snapshot(ref):
            await _bounded("template snapshot", Snapshot.create(snapshot, from_sandbox=name), CREATE_SECONDS)
        return ref

    async def test_boot(self) -> Optional[BootFailure]:
        """A throwaway sandbox must boot and run a command. Three tries: a
        registry hiccup or a runtime that was still settling shouldn't keep
        a bot offline."""
        error: Optional[BaseException] = None
        for attempt, pause in enumerate((0, *BOOT_RETRY_SECONDS)):
            if pause:
                await asyncio.sleep(pause)
            try:
                await asyncio.wait_for(self._boot_once(), BOOT_SECONDS)
                return None
            except Exception as e:   # anything here means "can't run sandboxes" (yet)
                error = e
                logger.warning(f"workshop test boot attempt {attempt + 1} failed: {e!r}")
        logger.error(f"workshop test boot failed: {error!r}")
        return BootFailure(remediation(error), "network" if _is_download_failure(error) else "boot")

    async def _boot_once(self) -> None:
        from microsandbox import Sandbox, SecurityProfile
        name = f"da-boot-check-{os.getpid()}"
        sb = await Sandbox.create(name, image=self.image, memory=512,
                                  security=SecurityProfile.RESTRICTED, replace=True)
        try:
            out = await sb.shell("echo ok", timeout=60)
            if out.stdout_text.strip() != "ok":
                raise RuntimeError(f"unexpected boot output {out.stdout_text!r}")
        finally:
            try:
                await sb.stop()
                await Sandbox.remove(name)
            except Exception as e:   # a leftover check sandbox is replaced next time
                logger.warning(f"couldn't clean up {name}: {e!r}")
