"""
Anthropic Messages adapter: renders a ModelRequest into exactly the SDK call
the bot made before the driver existed. tests/test_golden_requests.py holds
it to that, byte for byte.

Cache layout (LOAD-BEARING, see CLAUDE.md): the hierarchy is tools ->
system -> messages, so this adapter marks the one system block and the last
cacheable message block, then appends the volatile tail after both marks.
"""

import asyncio
import logging
import re
import time
from dataclasses import replace
from typing import Awaitable, Callable, List, Optional, Tuple

from anthropic import AsyncAnthropic, BadRequestError, NotFoundError

from core.internal_constants import (
    CONSOLIDATION_BATCH_POLL_SECONDS,
    CONSOLIDATION_BATCH_TIMEOUT_HOURS,
    MODEL_CALL_SLOTS,
)
from core.model.catalog import ModelSpec, spec_for
from core.model.driver import (
    BatchItem,
    BatchTimeoutError,
    DocumentRefusedError,
    ModelRequest,
    ModelResponse,
    StaleFileError,
    Usage,
)

logger = logging.getLogger(__name__)

_EPHEMERAL = {"type": "ephemeral"}
_CACHEABLE_BLOCK_TYPES = frozenset({"text", "tool_result", "image", "document"})
_STOP_REASONS = {
    "end_turn": "end_turn", "tool_use": "tool_use", "max_tokens": "max_tokens",
    "refusal": "refusal", "pause_turn": "pause",
}


def serialize_assistant_blocks(content) -> list:
    """
    Convert SDK response content into dicts safe to persist and replay.

    Server tool blocks (server_tool_use, code execution, web search/fetch and
    their results) are deliberately dropped: detached from the live response
    their content shapes can't be reproduced (a stringified result 400s on
    replay), and the assistant's final text already carries the conclusions.

    When server blocks are dropped, thinking blocks go with them: their
    signatures bind to the original block sequence, so a thinking block
    re-sent without the server blocks it surrounded 400s as "modified".
    History thinking is optional - mid-loop continuations use the live
    response.content, never this serialized form.
    """
    serialized = []
    dropped_server_block = False
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            serialized.append({"type": "text", "text": block.text})
        elif block_type == "thinking":
            # Signature required for tool-use continuity on replay
            serialized.append({
                "type": "thinking",
                "thinking": block.thinking,
                "signature": block.signature,
            })
        elif block_type == "redacted_thinking":
            serialized.append({"type": "redacted_thinking", "data": block.data})
        elif block_type == "tool_use":
            serialized.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
        else:
            dropped_server_block = True
    if dropped_server_block:
        serialized = [
            b for b in serialized
            if b["type"] not in ("thinking", "redacted_thinking")
        ]
    return serialized


def with_message_cache_breakpoint(messages: list) -> list:
    """
    Copy of messages with cache_control on the last cacheable block, so each
    request caches the whole conversation up to its tail (the system-prefix
    breakpoint alone re-reads the full history every turn). Old breakpoints
    need no removal: the marked message is rebuilt fresh from state each
    request, and markers don't affect prefix matching - the next turn's
    breakpoint lands further down and reads the previous one's cache.

    This only pays off because NOTHING upstream of the messages is volatile:
    per-request content must ride AFTER this breakpoint (the volatile tail),
    never in a system block, or every lookup here would miss.

    The source messages are never mutated (their dicts are shared with
    conversation state). SDK objects (in-loop assistant content) and
    unmarkable block types are skipped by walking backwards.
    """
    for mi in range(len(messages) - 1, -1, -1):
        content = messages[mi].get("content") if isinstance(messages[mi], dict) else None
        if isinstance(content, str):
            new_content = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
        elif isinstance(content, list):
            for bi in range(len(content) - 1, -1, -1):
                block = content[bi]
                if isinstance(block, dict) and block.get("type") in _CACHEABLE_BLOCK_TYPES:
                    new_content = list(content)
                    new_content[bi] = {**block, "cache_control": {"type": "ephemeral"}}
                    break
            else:
                continue
        else:
            continue
        new_msg = {**messages[mi], "content": new_content}
        return messages[:mi] + [new_msg] + messages[mi + 1:]
    return messages


# What a document read on request can be refused for: past the PDF page
# limit, an unreadable PDF, or a prompt it made too long
DOCUMENT_REFUSAL = re.compile(r"pdf|document|prompt is too long|too large", re.IGNORECASE)


def _newest_read_ref(messages: list) -> Optional[str]:
    """The attachment id of the last document in the request that was read
    on request (an attachment_ref marked read), if any."""
    for m in reversed(messages):
        content = m.get("content")
        for block in reversed(content if isinstance(content, list) else []):
            if _is_ref(block) and block.get("read"):
                return block["attachment_id"]
    return None


def stale_file_id(error: Exception) -> Optional[str]:
    """The file id in a 'File `file_xxx` not found.' API error, if any."""
    match = re.search(r"File `(file_[A-Za-z0-9]+)` not found", str(error))
    return match.group(1) if match else None


# =============================================================================
# Rendering: ModelRequest -> (SDK surface, kwargs)
# =============================================================================

def _render_system(request: ModelRequest):
    system = request.system
    if not request.cache_system:
        return system
    if isinstance(system, str):
        system = [{"type": "text", "text": system}]
    return system[:-1] + [{**system[-1], "cache_control": dict(_EPHEMERAL)}]


def _render_output_config(request: ModelRequest) -> dict:
    config = {}
    if request.output_schema is not None:
        config["format"] = {"type": "json_schema", "schema": request.output_schema}
    if request.effort and spec_for(request.model).effort:
        config["effort"] = request.effort
    return config


def render(request: ModelRequest) -> Tuple[str, dict]:
    """The SDK surface to call and its kwargs. Pure: the request and the
    message dicts it references are never mutated."""
    kwargs = {"model": request.model, "max_tokens": request.max_tokens}
    if request.system is not None:
        kwargs["system"] = _render_system(request)

    messages = request.messages
    if request.cache_messages:
        messages = with_message_cache_breakpoint(messages)
    if request.volatile_tail:
        messages = messages + [{"role": "user", "content": [
            {"type": "text", "text": request.volatile_tail}]}]
    kwargs["messages"] = messages

    if request.tools:
        typed = spec_for(request.model).typed_tools
        kwargs["tools"] = [tool.anthropic if typed or tool.schema is None else tool.plain()
                           for tool in request.tools]
    if request.betas is not None:
        kwargs["betas"] = request.betas
    if request.thinking:
        kwargs["thinking"] = {"type": "adaptive"}
    output_config = _render_output_config(request)
    if output_config:
        kwargs["output_config"] = output_config

    api = "beta.messages" if request.beta else "messages"
    return f"{api}.{'stream' if request.stream else 'create'}", kwargs


def to_response(message) -> ModelResponse:
    """An SDK Message as a ModelResponse; content stays the SDK's own blocks."""
    usage = message.usage
    return ModelResponse(
        content=message.content,
        stop_reason=_STOP_REASONS.get(message.stop_reason, "other"),
        stop_detail=message.stop_reason or "",
        usage=Usage(
            input_tokens=usage.input_tokens or 0,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
        ),
        model=message.model,
        persistable=serialize_assistant_blocks(message.content),
        raw=message,
    )


def _is_ref(block) -> bool:
    return isinstance(block, dict) and block.get("type") == "attachment_ref"


def _has_ref(message: dict) -> bool:
    """Top level or inside a tool_result: old states heal file blocks there
    too (0.5-era get_attachment put documents in tool results)."""
    content = message.get("content")
    return isinstance(content, list) and any(
        _is_ref(b) or (isinstance(b, dict) and b.get("type") == "tool_result"
                       and isinstance(b.get("content"), list) and any(_is_ref(c) for c in b["content"]))
        for b in content)


class AnthropicMessagesDriver:
    """ModelDriver over the Anthropic Messages API (live and Batches).

    max_concurrent caps the model calls in flight across the whole bot, so
    turns in many channels at once can't fan out into a burst of parallel
    requests; a call over the cap waits for a slot."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 client=None, max_concurrent: int = MODEL_CALL_SLOTS,
                 poll_interval_seconds: float = CONSOLIDATION_BATCH_POLL_SECONDS,
                 timeout_hours: float = CONSOLIDATION_BATCH_TIMEOUT_HOURS):
        # The raw client stays reachable for the Anthropic-only managers
        # outside the driver (Files API, Skills API)
        self.client = client or AsyncAnthropic(api_key=api_key, base_url=base_url)
        self._slots = asyncio.Semaphore(max_concurrent)
        # attachment_id -> request block (spec 4.6); set once attachments run
        self.resolve_attachment: Optional[Callable[[str], Awaitable[dict]]] = None
        self.poll_interval = poll_interval_seconds
        self.timeout_seconds = timeout_hours * 3600

    def spec_for(self, model: str) -> ModelSpec:
        return spec_for(model)

    def _surface(self, name: str):
        api = self.client
        for part in name.split("."):
            api = getattr(api, part)
        return api

    async def _resolve_refs(self, request: ModelRequest) -> ModelRequest:
        """attachment_ref blocks become what this provider reads: Files API
        documents, inline images, or a note. New message dicts for this call
        only - persisted state keeps its refs."""
        if not any(_has_ref(m) for m in request.messages):
            return request
        messages = []
        for m in request.messages:
            if _has_ref(m):
                m = {**m, "content": [await self._resolve_any(b) for b in m["content"]]}
            messages.append(m)
        return replace(request, messages=messages)

    async def _resolve_any(self, block):
        if _is_ref(block):
            return await self._resolve_block(block)
        if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), list):
            return {**block, "content": [await self._resolve_block(c) if _is_ref(c) else c
                                         for c in block["content"]]}
        return block

    async def _resolve_block(self, block: dict) -> dict:
        if self.resolve_attachment is None:
            return {"type": "text", "text": "\n[An attachment that can't be opened here]"}
        return await self.resolve_attachment(block["attachment_id"], read=bool(block.get("read")))

    async def generate(self, request: ModelRequest) -> ModelResponse:
        read = _newest_read_ref(request.messages)   # gone once refs resolve
        request = await self._resolve_refs(request)
        surface, kwargs = render(request)
        try:
            async with self._slots:
                if request.stream:
                    # Streamed so large max_tokens are allowed (the SDK refuses
                    # a non-streaming request it estimates could exceed 10
                    # minutes), accumulated to the Message create() would return
                    async with self._surface(surface)(**kwargs) as stream:
                        message = await stream.get_final_message()
                else:
                    message = await self._surface(surface)(**kwargs)
        except NotFoundError as e:
            file_id = stale_file_id(e)
            if file_id:
                raise StaleFileError(file_id) from e
            raise
        except BadRequestError as e:
            # Only a refusal about a document or size, with a document read on
            # request in the call: then that one is the likely cause (ordinary
            # documents are small enough never to be)
            reason = getattr(e, "message", str(e))
            if read and DOCUMENT_REFUSAL.search(reason):
                raise DocumentRefusedError(read, reason[:200]) from e
            raise
        return to_response(message)

    async def batch(self, items: List[Tuple[str, ModelRequest]]) -> dict:
        """Submit one Batches API job and wait for it: latency-tolerant work
        at half price, on rate limits separate from live traffic. Raises
        BatchTimeoutError (nothing applied) if it doesn't end in time."""
        requests = [{"custom_id": custom_id, "params": render(request)[1]}
                    for custom_id, request in items]
        batches = self.client.beta.messages.batches
        async with self._slots:   # the submit only - polling holds no slot
            batch = await batches.create(requests=requests)
        logger.info(f"Batch {batch.id} submitted ({len(requests)} requests)")

        deadline = time.monotonic() + self.timeout_seconds
        # "canceling" resolves to "ended"; any unknown future status is
        # bounded by the timeout rather than treated as terminal
        while batch.processing_status != "ended":
            if time.monotonic() > deadline:
                raise BatchTimeoutError(
                    f"Batch {batch.id} still {batch.processing_status} after "
                    f"{self.timeout_seconds / 3600:.0f}h")
            await asyncio.sleep(self.poll_interval)
            batch = await batches.retrieve(batch.id)

        results = {}
        async for item in await batches.results(batch.id):
            result = item.result
            succeeded = result.type == "succeeded"
            results[item.custom_id] = BatchItem(
                type=result.type,
                message=to_response(result.message) if succeeded else None,
                error=getattr(result, "error", None),
            )
        done = sum(1 for r in results.values() if r.type == "succeeded")
        logger.info(f"Batch {batch.id} ended: {done}/{len(results)} succeeded")
        return results
