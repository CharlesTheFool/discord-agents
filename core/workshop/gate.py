"""The start gate (spec 5.5): where any scope runs the workshop, the bot
starts only after the sandbox runtime passes a test boot.

The outcome is written to a small status file the supervisor reads (it
never imports bot code): on, off with the reason, or blocked with what the
operator does about it. A blocked start exits with START_BLOCKED_EXIT_CODE,
which the supervisor holds instead of crash-restarting: retrying a missing
hypervisor five times an hour fixes nothing.
"""
import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Optional

from core.workshop.jobs import JobStore
from core.workshop.service import Workshop, WorkshopUnavailable
from core.workshop.state import WorkshopState

logger = logging.getLogger(__name__)


class StartBlocked(Exception):
    """Said to the operator as is: why the bot can't start, and the fix."""


def status_path(root: pathlib.Path, bot_id: str) -> pathlib.Path:
    return root / "persistence" / "workshop" / bot_id / "status.json"


def write_status(root: pathlib.Path, bot_id: str, state: str, reason: Optional[str] = None) -> None:
    path = status_path(root, bot_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"state": state, "reason": reason,
                               "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}),
                   encoding="utf-8")
    tmp.replace(path)


def off_reason(config) -> Optional[str]:
    """Why no scope can run the workshop, or None when one can."""
    workshop = config.workshop
    if not workshop.enabled:
        return "workshop.enabled is false"
    if workshop.default_setting != "container" and "container" not in workshop.scopes.values():
        return "no scope is set to container"
    return None


async def open_workshop(config, bot_id: str, *, root: pathlib.Path, vaults, db,
                        skills_dir: pathlib.Path) -> Optional[Workshop]:
    """The bot's Workshop, started; None when it's off. Raises StartBlocked."""
    reason = off_reason(config)
    if reason:
        logger.warning(f"Workshop off ({reason}): this bot has no execution capability")
        write_status(root, bot_id, "off", reason)
        return None

    from core.workshop.backends import microsandbox as msb
    try:
        msb.pin_runtime(msb.DEFAULT_HOME)
        backend = msb.MicrosandboxBackend()
    except (msb.RuntimeMissing, ImportError) as e:
        raise blocked(root, bot_id, msb.remediation(e)) from None
    error = await backend.test_boot()
    if error:
        raise blocked(root, bot_id, error)

    jobs = JobStore(db, bot_id)
    await jobs.create_schema()
    workshop = Workshop(config=config, bot_id=bot_id, data_root=root, backend=backend, jobs=jobs,
                        vaults=vaults, state=WorkshopState(root / "persistence" / "workshop" / bot_id /
                                                           "state.json"),
                        skills_dir=skills_dir, runtime_home=msb.DEFAULT_HOME)
    try:
        await workshop.start()
    except WorkshopUnavailable as e:
        raise blocked(root, bot_id, f"The workshop couldn't start: {e}.") from None
    write_status(root, bot_id, "on")
    logger.info(f"Workshop on (proxy port {workshop.proxy_port})")
    return workshop


def blocked(root: pathlib.Path, bot_id: str, reason: str) -> StartBlocked:
    write_status(root, bot_id, "blocked", reason)
    return StartBlocked(reason)
