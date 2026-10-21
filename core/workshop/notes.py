"""Persisted context notes and the quiet-hours gate, shared by everything that
speaks into a channel without being asked: the agentic engine's watch
relays, workshop completion notes, approval decisions (0.14) and project
check-ins (0.14.1). They live here, not in AgenticEngine, which exists only
when `agentic.enabled`.
"""
from datetime import datetime
from typing import Dict, Optional

NOTE_HEAD = "<context_update>\n(Automated context, not a user message)\n"
NOTE_TAIL = "\n</context_update>"


class ContextNoteWriter:
    """Appends a persisted user turn to a channel's conversation state -
    durable context, unlike the per-request volatile tail. Takes the
    channel's lock: a note never lands between a turn's tool pairs."""

    def __init__(self, conversation_state_manager, channel_locks):
        self.states = conversation_state_manager
        self.locks = channel_locks

    async def write(self, channel_id: str, note: str) -> bool:
        if self.states is None:
            return False
        async with self.locks.lock(str(channel_id)):
            state = await self.states.get_or_create(str(channel_id))
            state.add_message("user", note)
            state.enforce_message_cap()
            await self.states.save(state)
        return True


def quiet_now(config, now: Optional[datetime] = None) -> bool:
    """Quiet hours are local host-clock hours (config.agentic.proactive)."""
    hours = config.agentic.proactive.quiet_hours or []
    return (now or datetime.now()).hour in hours


class ProactiveLimits:
    """Daily caps on turns the bot starts unasked (the proactive intensity
    preset), one count for everything that does: proactive engagement,
    coordination sends, project check-ins. In memory, reset each local day."""

    def __init__(self, config):
        values = config.agentic.proactive.get_intensity_values()
        self.max_global = values.max_per_day_global
        self.max_per_channel = values.max_per_day_per_channel
        self._day = datetime.now().date()
        self.total = 0
        self.per_channel: Dict[str, int] = {}

    def _roll(self) -> None:
        if datetime.now().date() > self._day:
            self._day, self.total, self.per_channel = datetime.now().date(), 0, {}

    def allows(self, channel_id: str) -> bool:
        self._roll()
        return (self.total < self.max_global
                and self.per_channel.get(str(channel_id), 0) < self.max_per_channel)

    def count(self, channel_id: str) -> None:
        self._roll()
        self.total += 1
        self.per_channel[str(channel_id)] = self.per_channel.get(str(channel_id), 0) + 1


def _duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {seconds} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


def completion_note(job, tail: str, now: Optional[datetime] = None) -> str:
    """What a finished background job tells its home channel."""
    started = datetime.fromisoformat(job.started_at)
    finished = datetime.fromisoformat(job.finished_at) if job.finished_at else (now or started)
    took = _duration((finished - started).total_seconds())
    if job.kind == "worker":
        return _worker_note(job, took)
    if job.state == "interrupted":
        outcome = f"was interrupted ({job.result})"
    elif job.result:
        outcome = f"{job.result} (exit code {job.exit_code})"
    else:
        outcome = f"finished with exit code {job.exit_code} after {took}"
    whose = (f"A command your helper {job.executor_key[len('worker:'):]} started ({job.id})"
             if job.executor_key.startswith("worker:") else f"Your background job {job.id}")
    lines = [f"{whose} in the workshop {outcome}.",
             f"Command: {job.command[:300]}"]
    if tail.strip():
        lines += ["Last lines of its output:", "```", tail.rstrip()[-2000:], "```"]
    if job.log_path:
        lines.append(f"Full log: {job.log_path}")
    lines.append("Pick it up from here if it matters to the conversation.")
    return NOTE_HEAD + "\n".join(lines) + NOTE_TAIL


def _worker_note(job, took: str) -> str:
    """A worker's report (0.15.0); interrupted ones say nothing more is coming."""
    if job.state == "interrupted":
        head = (f"Your helper {job.id} was interrupted ({job.result}); nothing more is coming from "
                f"it. Delegate again if the task still matters.")
    elif job.state == "failed":
        head = f"Your helper {job.id} stopped after {took}: {job.result}."
    else:
        head = f"Your helper {job.id} finished after {took}. Its report:\n{job.result}"
    lines = [head, f"The task was: {job.command[:300]}",
             "Pick it up from here if it matters to the conversation."]
    return NOTE_HEAD + "\n".join(lines) + NOTE_TAIL
