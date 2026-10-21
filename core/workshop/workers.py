"""Workers (spec 7.2, 0.15.0): delegated work that runs on its own.

A worker is a job (kind `worker`) whose executor is a model loop instead of a
shell command: the same jobs table, per-scope cap, completion note and
awareness line as a background command. It works in its scope's workshop
with its own shell state, reads memory, messages and files the way the
resident can from the home channel (same vault rules), and writes nothing a
person sees: no messages, no memory writes, no ask_prime, no network
approvals, no further delegation. That holds twice over, mechanically: the
tool set is assembled for the worker role, and the dispatch here refuses
every other name, whatever the model emits.

Its loop is its own, not the resident's: a worker has no Discord message,
conversation state or send path. A bot restart interrupts it (the next boot
marks the row interrupted and the note says so); resumable workers are later.
"""
import asyncio
import json
import logging
import pathlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from core.internal_constants import (
    WORKER_BUDGET_NOTE,
    WORKER_CALL_SLOTS,
    WORKER_COMMAND_CEILING_SECONDS,
    WORKER_MAX_ITERATIONS,
    WORKER_MAX_SECONDS,
    WORKER_PROMPT,
    WORKER_REPORT_CHARS,
    WORKER_WEB_LINE,
    WORKSHOP_BACKGROUND_PER_SCOPE,
)
from core.model.driver import ModelRequest
from core.model.registry import ToolContext, assemble_tools
from core.workshop.backend import SandboxUnavailable
from core.workshop.jobs import DONE, FAILED, new_job_id
from core.workshop.scopes import Scope
from core.workshop.service import WorkshopUnavailable
from tools.bash_tool import run_bash
from tools.editor_tool import EDITOR_NAME, run_editor
from tools.skills_tool import run_request_skill

logger = logging.getLogger(__name__)

WORKER_TOOLS = frozenset({"memory", "discord_tools", "bash", EDITOR_NAME, "request_skill"})


@dataclass(frozen=True)
class Home:
    """Where a worker was delegated from: its reads, vault rules and report
    belong to this conversation."""
    scope: Scope
    server_id: Optional[str]
    channel_id: str
    parent_id: Optional[str]
    is_thread: bool


async def copy_to_inbox(workshop, attachment_manager, metadata: dict, scope: Scope) -> str:
    """A fetched file, as bytes, into the scope's /workspace/inbox (its own
    vault check already ran in get_attachment)."""
    data = await attachment_manager.local_bytes(metadata["attachment_id"])
    if data is None:
        return "Its stored copy is gone, so it can't be opened."
    name = re.sub(r"[^A-Za-z0-9._-]", "_", metadata["filename"]).lstrip(".") or "attachment"
    try:
        sandbox = await workshop.sandbox(scope)
        await workshop.backend.write_file(sandbox, f"/workspace/inbox/{name}", data)
    except (WorkshopUnavailable, SandboxUnavailable) as e:
        return f"Couldn't put it in the workshop ({e})."
    except Exception as e:   # a broken sandbox must not break reading the file itself
        logger.warning(f"inbox copy of {name} failed: {e!r}")
        return f"Couldn't put it in the workshop ({type(e).__name__}: {e})."
    return f"Saved into your workshop at /workspace/inbox/{name}."


def _text(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text").strip()


def _for_transcript(content):
    """Block dicts with image bytes left out: the transcript is for reading."""
    if isinstance(content, list):
        return [_for_transcript(c) for c in content]
    if isinstance(content, dict):
        if content.get("type") == "image":
            return {"type": "image", "omitted": True}
        return {k: _for_transcript(v) for k, v in content.items()}
    return content


class Transcript:
    """JSON lines, one per step, for the dashboard; never channel state."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kind: str, **data) -> None:
        line = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **data}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(_for_transcript(line), ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            logger.warning(f"worker transcript write failed ({self.path.name}): {e}")


class Workers:
    def __init__(self, *, workshop, config, memory, skills, deliver, transcripts: pathlib.Path,
                 discord_tools: Callable = lambda: None, attachments: Callable = lambda: None,
                 driver_factory: Optional[Callable] = None):
        self.workshop = workshop
        self.config = config
        self.memory = memory
        self.skills = skills
        self.deliver = deliver                  # the job watcher's: note, event, wake-up turn
        self.transcripts = transcripts
        self.discord_tools = discord_tools      # late-bound: built in on_ready
        self.attachments = attachments
        self.driver_factory = driver_factory or self._default_driver
        self._driver = None
        self._tasks = set()
        self.clock = time.monotonic

    def _default_driver(self):
        from core.model.factory import build_driver
        return build_driver(self.config.api, max_concurrent=WORKER_CALL_SLOTS)

    @property
    def driver(self):
        """The workers' own driver: its call slots are separate from the
        resident's, so workers never starve conversation turns."""
        if self._driver is None:
            self._driver = self.driver_factory()
        return self._driver

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # --- delegate ---------------------------------------------------------------

    async def delegate(self, tool_input: dict, *, server_id, channel_id, parent_id=None,
                       is_thread=False) -> str:
        task = (tool_input.get("task") or "").strip()
        if not task:
            return "Error: no task given."
        try:
            scope = self.workshop.scope_for_turn(server_id, channel_id, parent_id=parent_id,
                                                 is_thread=is_thread)
        except WorkshopUnavailable as e:
            return f"The workshop isn't available: {e}."
        if await self.workshop.jobs.background_count(scope.key) >= WORKSHOP_BACKGROUND_PER_SCOPE:
            return (f"This workshop already has {WORKSHOP_BACKGROUND_PER_SCOPE} jobs and helpers "
                    f"running - wait for one to finish (a note lands here) before delegating more.")
        job_id = new_job_id()
        job = await self.workshop.jobs.create(
            scope_key=scope.key, home_server_id=server_id, home_channel_id=channel_id,
            kind="worker", executor_key=f"worker:{job_id}", command=task, foreground=False,
            job_id=job_id)
        home = Home(scope, server_id, str(channel_id), str(parent_id) if parent_id else None, is_thread)
        runner = asyncio.ensure_future(self._run(job, home, task, (tool_input.get("context") or "").strip()))
        self._tasks.add(runner)
        runner.add_done_callback(self._tasks.discard)
        return (f"Delegated as {job.id}. It works on its own; a note with its report lands here "
                f"when it's done.")

    async def _run(self, job, home: Home, task: str, context: str) -> None:
        transcript = Transcript(self.transcripts / f"{job.id}.jsonl")
        try:
            report = await self._loop(job, home, task, context, transcript)
            state, result = DONE, report
        except asyncio.CancelledError:
            raise   # shutdown: the row stays running and the next boot marks it interrupted
        except Exception as e:
            logger.exception(f"worker {job.id} failed")
            state, result = FAILED, f"it stopped on an error ({type(e).__name__}: {e})"
        transcript.write("end", state=state, result=result)
        await self.workshop.jobs.mark_finished(job.id, state, result=result[:WORKER_REPORT_CHARS])
        await self.deliver(job.id)

    # --- the loop -------------------------------------------------------------------

    def _request(self, task: str, context: str) -> ModelRequest:
        api = self.config.api
        model = api.worker_model or api.model
        spec = self.driver.spec_for(model)
        tools, betas = assemble_tools(ToolContext(
            role="worker",
            discord_tools=self.discord_tools() is not None,
            web_search=api.web_search.enabled,
            web_citations=self.config.get_web_search_config()["citations_enabled"],
        ), spec)
        web = any(t.mode == "server" for t in tools)
        system = WORKER_PROMPT.format(name=self.config.name, bot_id=self.config.bot_id,
                                      web=WORKER_WEB_LINE if web else "",
                                      date=datetime.now().strftime("%Y-%m-%d"))
        skills = self.skills.prompt(workshop=True)
        if skills:
            system += "\n\n" + skills
        brief = f"<task>\n{task}\n</task>"
        if context:
            brief += f"\n\n<context>\n{context}\n</context>"
        return ModelRequest(
            model=model,
            max_tokens=min(api.context_tokens, spec.max_output),
            system=[{"type": "text", "text": system}],
            cache_system=True,
            messages=[{"role": "user", "content": [{"type": "text", "text": brief}]}],
            cache_messages=True,
            tools=tools,
            betas=betas,
            thinking=api.thinking.enabled and spec.thinking,
            effort=api.effort,
            stream=True,
            beta=True,
        )

    async def _loop(self, job, home: Home, task: str, context: str, transcript: Transcript) -> str:
        request = self._request(task, context)
        transcript.write("task", task=task, context=context, model=request.model)
        started = self.clock()
        steps = 0
        while True:
            if steps >= WORKER_MAX_ITERATIONS:
                why = f"{WORKER_MAX_ITERATIONS} steps"
                break
            if self.clock() - started >= WORKER_MAX_SECONDS:
                why = f"{WORKER_MAX_SECONDS // 60} minutes"
                break
            steps += 1
            response = await self.driver.generate(request)
            transcript.write("assistant", content=response.persistable, stop=response.stop_reason,
                             usage=vars(response.usage))
            request.messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "pause":   # a long server-tool turn, resumed as is
                continue
            if response.stop_reason != "tool_use":
                return _text(response) or "It finished without writing a report."
            remaining = WORKER_MAX_SECONDS - (self.clock() - started)
            results = await self._dispatch(response, job, home, remaining)
            transcript.write("tool_results", content=results)
            request.messages.append({"role": "user", "content": results})
        # Out of budget: one last call for whatever it has
        note = {"type": "text", "text": WORKER_BUDGET_NOTE.format(why=why)}
        last = request.messages[-1]
        if last["role"] == "user":
            last["content"] = list(last["content"]) + [note]
        else:
            request.messages.append({"role": "user", "content": [note]})
        response = await self.driver.generate(request)
        transcript.write("assistant", content=response.persistable, stop=response.stop_reason,
                         usage=vars(response.usage))
        summary = _text(response)
        return (f"It used up its budget ({why}) before finishing. "
                + (f"Its summary: {summary}" if summary else "It left no summary."))

    async def _dispatch(self, response, job, home: Home, remaining: float) -> list:
        """Every tool_use answered, in order; a name outside the worker's set
        is refused here even though it was never offered."""
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name not in WORKER_TOOLS:
                logger.warning(f"worker {job.id} called {block.name}, outside its tools: refused")
                results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                "content": f"{block.name} isn't one of your tools. You work and read; "
                                           f"the one who delegated sends messages, writes memory and "
                                           f"asks for network access - put what's needed in your report."})
                continue
            try:
                content = await self._tool(block, job, home, remaining)
                result = {"type": "tool_result", "tool_use_id": block.id, "content": content}
            except Exception as e:
                logger.error(f"worker {job.id}: {block.name} failed: {e!r}", exc_info=True)
                result = {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                          "content": f"{block.name} hit an unexpected error ({type(e).__name__}: {e})."}
            results.append(result)
        return results

    async def _tool(self, block, job, home: Home, remaining: float):
        where = dict(server_id=home.server_id, channel_id=home.channel_id,
                     parent_id=home.parent_id, is_thread=home.is_thread)
        if block.name == "memory":
            return self.memory.execute(block.input, current_server_id=home.server_id,
                                       current_channel_id=home.channel_id, parent_id=home.parent_id,
                                       is_thread=home.is_thread, read_only=True)
        if block.name == "bash":
            ceiling = int(max(60, min(WORKER_COMMAND_CEILING_SECONDS, remaining)))
            return await run_bash(self.workshop, block.input, worker=job.id, ceiling_s=ceiling, **where)
        if block.name == EDITOR_NAME:
            return await run_editor(self.workshop, block.input, **where)
        if block.name == "request_skill":
            return run_request_skill(self.skills, block.input, workshop=True)
        executor = self.discord_tools()   # discord_tools: every command is a read
        if executor is None:
            return "Message search isn't available right now."
        result = await executor.execute(block.input, current_server_id=home.server_id,
                                        current_channel_id=home.channel_id)
        if isinstance(result, dict) and result.get("_structured"):
            return await self._attachment(result, home)
        return result

    async def _attachment(self, result: dict, home: Home) -> list:
        """A fetched file: an image inline, anything else into the inbox."""
        blocks = [{"type": "text", "text": result["text"]}]
        file_data = result["file_data"]
        if file_data.get("method") == "base64":
            blocks.append({"type": "image", "source": {"type": "base64",
                                                        "media_type": file_data["media_type"],
                                                        "data": file_data["data"]}})
        elif self.attachments() is not None:
            blocks[0]["text"] += "\n" + await copy_to_inbox(self.workshop, self.attachments(),
                                                             result["metadata"], home.scope)
        return blocks
