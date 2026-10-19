"""
Operator Stop for a bot's workshop (spec 5.5): stop its sandboxes through
the msb CLI, so their running jobs end with the bot. Only the operator's
Stop does this; restart, crash recovery and daemon shutdown leave sandboxes
running, and the runtime's idle timeout stops idle ones on its own.

`msb stop --label` is repeatable and AND-matched (verified on 0.7.8: with
sandboxes labelled install=A/bot=x, install=B/bot=x and install=A/bot=y,
`--label install=A --label bot=x` stopped only the first). Both labels
matter: a dev checkout and the app share MSB_HOME and can share a bot id.
"""

import asyncio
import importlib.util
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .paths import SupervisorRoot

logger = logging.getLogger(__name__)

DEFAULT_HOME = Path.home() / ".discord-agents" / "msb"   # the bot's MSB_HOME
STOP_SECONDS = 30      # msb's graceful budget; the client gets 10 s more, then is killed


def runtime_env(home: Path = DEFAULT_HOME) -> Optional[Dict[str, str]]:
    """The env the bot pins before its SDK loads, rebuilt here because the
    supervisor never imports bot code: keep in step with pin_runtime() in
    core/workshop/backends/microsandbox.py. None when the microsandbox wheel
    (and so its bundled runtime) isn't installed. find_spec doesn't import."""
    spec = importlib.util.find_spec("microsandbox")
    if spec is None or not spec.submodule_search_locations:
        return None
    bundled = Path(next(iter(spec.submodule_search_locations))) / "_bundled"
    exe = bundled / "bin" / ("msb.exe" if os.name == "nt" else "msb")
    libs = sorted((bundled / "lib").glob("libkrunfw*"))
    if not exe.exists() or not libs:
        return None
    return {**os.environ, "MSB_PATH": str(exe), "MSB_LIBKRUNFW_PATH": str(libs[0]),
            "MSB_HOME": str(home)}


def install_label(root: SupervisorRoot, bot_id: str) -> Optional[str]:
    """The install id the bot's sandboxes carry, from its start-gate status
    (core/workshop/gate.py writes it on every start, whatever the outcome)."""
    try:
        status = json.loads(root.workshop_status(bot_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return status.get("install")


async def run_msb(argv: List[str], env: Dict[str, str],
                  timeout_s: float) -> Tuple[Optional[int], str]:
    """(exit code or None on timeout, output). Output goes to a temp file and
    we wait on the process, not on EOF: a pipe end a runtime process
    inherited would never close (plan B V5)."""
    with tempfile.TemporaryFile() as out:
        proc = await asyncio.create_subprocess_exec(
            *argv, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        try:
            code = await asyncio.wait_for(proc.wait(), timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            code = None
        out.seek(0)
        return code, out.read().decode("utf-8", "replace").strip()


async def stop_sandboxes(root: SupervisorRoot, bot_id: str, home: Path = DEFAULT_HOME) -> None:
    """Best effort, never raises: a failed stop is logged and the operator's
    Stop still succeeds (idle sandboxes stop by themselves within the idle
    timeout; running jobs keep their sandbox up until they end)."""
    install = install_label(root, bot_id)
    if not install:
        return   # the bot never got as far as its start gate
    env = runtime_env(home)
    if env is None:
        logger.warning(f"Not stopping {bot_id}'s sandboxes: the sandbox runtime isn't installed")
        return
    # -q: silent on success (none matching is a success too); errors still print
    argv = [env["MSB_PATH"], "stop", "-q", "--label", f"install={install}", "--label", f"bot={bot_id}",
            "--timeout", str(STOP_SECONDS)]
    try:
        code, output = await run_msb(argv, env, STOP_SECONDS + 10)
    except Exception as e:
        logger.warning(f"Could not stop {bot_id}'s sandboxes: {e}")
        return
    if code == 0:
        logger.info(f"Stopped {bot_id}'s sandboxes (install {install})")
    else:
        logger.warning(f"Stopping {bot_id}'s sandboxes failed "
                       f"({'timed out' if code is None else f'exit {code}'}): {output}")
