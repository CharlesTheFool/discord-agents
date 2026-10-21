"""The `bash` tool (spec 5.7): a command in this scope's workshop sandbox.

Our own JSON schema on every driver (no provider-typed tool): the semantics
- stateless commands with restored cwd/env, jobs past a ceiling - are ours.
"""
from core.internal_constants import WORKSHOP_FOREGROUND_CEILING_SECONDS, WORKSHOP_SHELL_JOB_MAX_SECONDS
from core.workshop.backend import SandboxUnavailable
from core.workshop.service import WorkshopUnavailable

BASH_TOOL = {
    "name": "bash",
    "description": (
        "Run a shell command in your workshop: a small Linux machine of your own "
        "(Debian, Python 3.12, Node 24, git, ffmpeg) whose /workspace keeps its files "
        "between conversations. A command starts where your last one here left off, "
        "with the variables it exported. Anything still running after three minutes "
        "carries on in the background and a note lands here when it finishes; set "
        "background to start it there directly. The internet reaches package "
        "downloads only - for anything else, ask with request_network_access."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "A bash command line"},
            "background": {"type": "boolean",
                           "description": "Start it as a background job and return at once"},
            "timeout_s": {"type": "integer", "minimum": 1, "maximum": WORKSHOP_SHELL_JOB_MAX_SECONDS,
                          "description": "Stop it after this many seconds (default: 6 hours)"},
        },
        "required": ["command"],
    },
}


def executor_key(scope, channel_id) -> str:
    """The resident's shell state is per scope AND channel: two channels of
    one server run turns at once and must not share a working directory."""
    return f"resident:{scope.key}:{channel_id}"


async def run_bash(workshop, tool_input: dict, *, server_id, channel_id,
                   parent_id=None, is_thread=False, worker: str = "",
                   ceiling_s: int = WORKSHOP_FOREGROUND_CEILING_SECONDS) -> str:
    """worker: the job id of a worker running the command (0.15.0): its own
    shell state, and never a background start (a worker waits on its work)."""
    command = (tool_input.get("command") or "").strip()
    if not command:
        return "Error: no command given."
    timeout_s = tool_input.get("timeout_s")
    try:
        scope = workshop.scope_for_turn(server_id, channel_id, parent_id=parent_id, is_thread=is_thread)
        result = await workshop.run_command(
            scope, f"worker:{worker}" if worker else executor_key(scope, channel_id), command,
            home_server_id=server_id, home_channel_id=channel_id,
            background=bool(tool_input.get("background")) and not worker,
            timeout_s=int(timeout_s) if timeout_s else None, ceiling_s=ceiling_s)
    except WorkshopUnavailable as e:
        return f"The workshop isn't available: {e}."
    except SandboxUnavailable as e:
        return f"The workshop's machine isn't reachable right now ({e}); try again in a moment."
    return result.text
