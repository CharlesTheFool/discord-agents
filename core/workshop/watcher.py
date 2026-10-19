"""The job watcher: follows every background job to its end, writes the
completion note into the job's home channel, and asks for a note-driven
turn there so the bot can pick the work up on its own (spec 6).

One task per running job, awaiting the runtime's own wait (Phase 0 V6: a
wait survives reconnects and never kills the job). Wake-up turns are held
through quiet hours (the note itself is written at once) and coalesce per
channel: notes that arrive while a channel's turn runs get one more turn.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, Set

from core.workshop.backend import SandboxUnavailable
from core.workshop.jobs import RUNNING, JobRecord
from core.workshop.notes import ContextNoteWriter, completion_note, quiet_now
from core.workshop.scopes import Scope

logger = logging.getLogger(__name__)

HELD_CHECK_SECONDS = 300
TAIL_LINES = 20


class JobWatcher:
    def __init__(self, workshop, notes: ContextNoteWriter,
                 wake: Callable[[str], Awaitable[None]], config, events=None):
        self.workshop = workshop
        self.notes = notes
        self.wake = wake
        self.config = config
        self.events = events
        self._tasks: Set[asyncio.Task] = set()
        self._wanted: Dict[str, bool] = {}
        self._running_wakes: Set[str] = set()
        self._held: Set[str] = set()
        self._held_task = None
        self._delivering: Set[str] = set()   # job ids with a note being written

    async def start(self) -> None:
        """Settle the jobs table against the runtime, then follow what still runs
        and deliver what finished while the bot was away. The held-turn loop
        starts first, so a failure below can't leave quiet-hours holds stuck."""
        self._held_task = asyncio.ensure_future(self._release_held_loop())
        for job in await self.workshop.reconcile():
            self.follow(job)
        for job in await self.workshop.jobs.unnotified_finished():
            await self.deliver(job.id)

    async def close(self) -> None:
        for task in list(self._tasks) + ([self._held_task] if self._held_task else []):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def follow(self, job: JobRecord) -> None:
        task = asyncio.ensure_future(self._watch(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _sandbox_name(self, job: JobRecord) -> str:
        kind, _, scope_id = job.scope_key.partition(":")
        return Scope(kind, scope_id).sandbox_name(self.workshop.install_id, self.workshop.bot_id)

    async def _watch(self, job: JobRecord) -> None:
        name = self._sandbox_name(job)
        try:
            outcome = await self.workshop.backend.wait_job(name, job.sandbox_job_id, None)
        except SandboxUnavailable as e:
            await self.workshop.jobs.mark_interrupted([job.id], f"its sandbox stopped ({e})")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"watching {job.id} failed")
            await self.workshop.jobs.mark_interrupted([job.id], f"the workshop lost track of it ({e})")
        else:
            current = await self.workshop.jobs.get(job.id)
            if current and current.state == RUNNING:
                await self.workshop.finish(current, outcome)
        await self.deliver(job.id)

    async def _tail(self, job: JobRecord) -> str:
        if not job.log_path:
            return ""
        try:
            preview = await self.workshop.preview(self._sandbox_name(job), job.log_path)
        except Exception:
            return ""
        text = preview.decode("utf-8", errors="replace").partition("\n")[2]
        return "\n".join(text.rstrip().splitlines()[-TAIL_LINES:])

    async def deliver(self, job_id: str) -> None:
        """The completion note, its event, then a wake-up turn. Once per job:
        the boot catch-up and a watch that just finished can both get here,
        and the tail takes a moment, so the claim is taken up front."""
        if job_id in self._delivering:
            return
        self._delivering.add(job_id)
        try:
            job = await self.workshop.jobs.get(job_id)
            if job is None or job.notified or job.state == RUNNING:
                return
            note = completion_note(job, await self._tail(job))
            if not await self.notes.write(job.home_channel_id, note):
                return
            await self.workshop.jobs.mark_notified(job.id)
        finally:
            self._delivering.discard(job_id)
        await self._event(job)
        self.request_turn(job.home_channel_id)

    async def _event(self, job: JobRecord) -> None:
        if self.events is None:
            return
        started = datetime.fromisoformat(job.started_at)
        finished = datetime.fromisoformat(job.finished_at) if job.finished_at else datetime.now(timezone.utc)
        await self.events.add_event("job", job.home_server_id, job.home_channel_id, {
            "job_id": job.id, "state": job.state, "exit_code": job.exit_code,
            "seconds": round((finished - started).total_seconds(), 1),
            "command": job.command[:300], "result": job.result,
        })

    # --- wake-up turns -----------------------------------------------------

    def request_turn(self, channel_id: str) -> None:
        if quiet_now(self.config):
            self._held.add(channel_id)
            return
        self._wanted[channel_id] = True
        if channel_id not in self._running_wakes:
            # claimed here, not in the task: two requests before the task first
            # runs must coalesce into one loop, not start two
            self._running_wakes.add(channel_id)
            task = asyncio.ensure_future(self._run_wakes(channel_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_wakes(self, channel_id: str) -> None:
        try:
            while self._wanted.pop(channel_id, False):
                try:
                    await self.wake(channel_id)
                except Exception:
                    logger.exception(f"wake-up turn in {channel_id} failed")
        finally:
            self._running_wakes.discard(channel_id)

    async def _release_held_loop(self) -> None:
        while True:
            await asyncio.sleep(HELD_CHECK_SECONDS)
            self.release_held()

    def release_held(self) -> None:
        if quiet_now(self.config) or not self._held:
            return
        held, self._held = self._held, set()
        for channel_id in held:
            self.request_turn(channel_id)
