"""
The OpenAI Responses adapter (0.15.1): a ModelRequest rendered as one
stateless `responses.create` call (store=false), and the Response read back
into our vocabulary.

It serves background roles (episode distillation, watch checks) and
workers. validate() keeps the main model, its conversation state and the
batch roles on Anthropic, so nothing written by another model, and no
attachment by reference, reaches this adapter; the renderer refuses those
blocks rather than guessing at them.

Caching (measured in plan C's Phase 0): explicit mode. OpenAI matches a
cache only at breakpoints present in the request, so a breakpoint the
previous request wrote must still be there: with cache_messages, the last
cacheable part of each of the last two user turns carries one (read the
older, write the newer). Never the volatile tail; none without
cache_messages, so one-shots write nothing.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from core.internal_constants import MODEL_CALL_SLOTS, estimate_cost_usd
from core.model.catalog import ModelSpec, spec_for
from core.model.driver import ModelRequest, ModelResponse, Usage

logger = logging.getLogger(__name__)

RESPONSE_TIMEOUT_SECONDS = 600     # one non-streaming call, reasoning included
MARK = {"mode": "explicit"}
REASONING_INCLUDE = ["reasoning.encrypted_content"]


class UnsupportedBlock(ValueError):
    """A block this adapter can't carry (it never should reach here)."""


class ResponsesError(Exception):
    """A response the API marked failed."""


@dataclass(frozen=True)
class Block:
    """One output item, with the attributes engine code reads (type, text,
    name, input, id) and the item itself, replayed as is in-turn."""
    type: str                       # text | commentary | refusal | tool_use | reasoning
    item: dict
    text: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)
    id: str = ""


# --- rendering ---------------------------------------------------------------------

def _instructions(system) -> str:
    if not system:
        return ""
    if isinstance(system, str):
        return system
    return "\n\n".join(block["text"] for block in system)


def _parts(content) -> List[dict]:
    """A user turn's non-tool content as input parts."""
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    parts = []
    for block in content:
        kind = block.get("type")
        if kind == "text":
            parts.append({"type": "input_text", "text": block["text"]})
        elif kind == "image" and block.get("source", {}).get("type") == "base64":
            source = block["source"]
            parts.append({"type": "input_image",
                          "image_url": f"data:{source['media_type']};base64,{source['data']}"})
        else:
            raise UnsupportedBlock(f"a {kind!r} block can't be sent to OpenAI")
    return parts


def _tool_output(result: dict) -> dict:
    content = result.get("content")
    parts = _parts(content) if content else [{"type": "input_text", "text": "(no output)"}]
    if result.get("is_error"):
        if parts[0]["type"] == "input_text":
            parts[0] = {**parts[0], "text": f"Error: {parts[0]['text']}"}
        else:
            parts.insert(0, {"type": "input_text", "text": "Error:"})
    return {"type": "function_call_output", "call_id": result["tool_use_id"], "output": parts}


def _user_turn(content) -> List[dict]:
    """Tool results first (as function_call_outputs), then one user message
    with the rest, if any."""
    if isinstance(content, str):
        return [{"role": "user", "content": _parts(content)}]
    outputs = [_tool_output(b) for b in content if b.get("type") == "tool_result"]
    rest = [b for b in content if b.get("type") != "tool_result"]
    return outputs + ([{"role": "user", "content": _parts(rest)}] if rest else [])


def _assistant_turn(content) -> List[dict]:
    """In-turn: this adapter's own blocks, their items replayed as returned.
    Persisted dicts (text, tool_use) render as an assistant message and
    id-less function calls."""
    if isinstance(content, str):
        return [{"role": "assistant", "content": content, "phase": "final_answer"}]
    items = []
    for block in content:
        if isinstance(block, Block):
            items.append(block.item)
        elif block.get("type") == "text":
            items.append({"role": "assistant", "content": block["text"], "phase": "final_answer"})
        elif block.get("type") == "tool_use":
            items.append({"type": "function_call", "call_id": block["id"], "name": block["name"],
                          "arguments": json.dumps(block.get("input", {}), ensure_ascii=False)})
        else:
            raise UnsupportedBlock(f"an assistant {block.get('type')!r} block can't be sent to OpenAI")
    return items


def _mark(item: dict) -> None:
    key = "content" if item.get("role") == "user" else "output"
    item[key][-1] = {**item[key][-1], "prompt_cache_breakpoint": MARK}


def _input(request: ModelRequest, explicit: bool = True) -> List[dict]:
    items, user_turns = [], []
    for message in request.messages:
        if message["role"] == "assistant":
            items += _assistant_turn(message["content"])
        else:
            turn = _user_turn(message["content"])
            items += turn
            user_turns.append(turn)
    if request.cache_messages and explicit:
        for turn in user_turns[-2:]:
            if turn:
                _mark(turn[-1])
    if request.volatile_tail:
        items.append({"role": "user", "content": [{"type": "input_text", "text": request.volatile_tail}]})
    return items


def _tool(tool) -> dict:
    if tool.mode != "client" or tool.json_schema is None:
        raise UnsupportedBlock(f"tool {tool.name!r} has no function form for OpenAI")
    return {"type": "function", "name": tool.name,
            "description": tool.description or tool.anthropic.get("description", ""),
            # optional fields abound in our schemas: no strict normalization
            "parameters": tool.json_schema, "strict": False}


def render(request: ModelRequest, spec: Optional[ModelSpec] = None) -> dict:
    """The responses.create kwargs for a request. Pure: the request is untouched."""
    spec = spec or spec_for(request.model)
    explicit = spec.cache_semantics == "explicit_breakpoints"   # GPT-5.6 and later
    kwargs = {"model": request.model, "max_output_tokens": request.max_tokens, "store": False,
              "include": REASONING_INCLUDE, "input": _input(request, explicit)}
    if explicit:
        kwargs["prompt_cache_options"] = {"mode": "explicit"}
    instructions = _instructions(request.system)
    if instructions:
        kwargs["instructions"] = instructions
    if request.tools:
        kwargs["tools"] = [_tool(t) for t in request.tools]
    if request.output_schema:
        kwargs["text"] = {"format": {"type": "json_schema", "name": "output",
                                     "schema": request.output_schema, "strict": True}}
    # Reasoning counts against max_output_tokens: thinking off means the
    # lowest effort the model takes, so a small budget isn't spent thinking
    effort = request.effort or (spec.min_effort if not request.thinking else None)
    if effort:
        kwargs["reasoning"] = {"effort": effort}
    return kwargs


# --- reading a response ------------------------------------------------------------

def _block(item) -> Block:
    data = item.model_dump(exclude_none=True, by_alias=True)   # replayed as the API wrote it
    if item.type == "message":
        refusal = " ".join(p.refusal for p in item.content if p.type == "refusal")
        if refusal:
            return Block("refusal", data, text=refusal)
        text = "".join(p.text for p in item.content if p.type == "output_text")
        return Block("commentary" if getattr(item, "phase", None) == "commentary" else "text", data, text=text)
    if item.type == "function_call":
        try:
            arguments = json.loads(item.arguments or "{}")
        except json.JSONDecodeError:
            arguments = None
        if not isinstance(arguments, dict):   # the tool sees no input and says so
            logger.warning(f"OpenAI {item.name} call whose arguments aren't a JSON object "
                           f"({len(item.arguments or '')} chars)")
            arguments = {}
        return Block("tool_use", data, name=item.name, input=arguments, id=item.call_id)
    if item.type == "reasoning":
        return Block("reasoning", data)
    raise ResponsesError(f"unexpected output item {item.type!r} (no hosted tool is offered)")


def _stop(response, blocks: List[Block]) -> str:
    if response.status == "incomplete":   # outranks a cut-off function call
        reason = getattr(response.incomplete_details, "reason", None)
        return {"max_output_tokens": "max_tokens", "content_filter": "refusal"}.get(reason, "other")
    if any(b.type == "refusal" for b in blocks):
        return "refusal"
    if any(b.type == "tool_use" for b in blocks):
        return "tool_use"
    return "end_turn"


def _usage(response) -> Usage:
    """OpenAI's input_tokens is the total; ours is the uncached part."""
    u = response.usage
    details = getattr(u, "input_tokens_details", None)
    read = getattr(details, "cached_tokens", 0) or 0
    written = getattr(details, "cache_write_tokens", 0) or 0
    return Usage(input_tokens=u.input_tokens - read - written, cache_read_input_tokens=read,
                 cache_creation_input_tokens=written, output_tokens=u.output_tokens)


def parse(response) -> ModelResponse:
    if response.status == "failed":
        error = response.error
        raise ResponsesError(f"{getattr(error, 'code', 'failed')}: {getattr(error, 'message', '')}")
    blocks = [_block(item) for item in response.output]
    persistable = [{"type": "text", "text": b.text} for b in blocks if b.type == "text"] + [
        {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
        for b in blocks if b.type == "tool_use"]
    reason = getattr(response.incomplete_details, "reason", None)
    return ModelResponse(content=blocks, stop_reason=_stop(response, blocks), usage=_usage(response),
                         model=response.model, stop_detail=f"{response.status}{f' ({reason})' if reason else ''}",
                         persistable=persistable, raw=response)


# --- the driver ----------------------------------------------------------------------

class OpenAIResponsesDriver:
    """Its own pool of call slots: rate limits are per provider."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, client=None,
                 max_concurrent: int = MODEL_CALL_SLOTS):
        if client is None:
            from openai import AsyncOpenAI   # only bots with an OpenAI role import it
            client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=RESPONSE_TIMEOUT_SECONDS)
        self.client = client
        self._slots = asyncio.Semaphore(max_concurrent)
        # The router assigns every adapter; nothing by reference reaches this one
        self.resolve_attachment: Optional[Callable] = None

    def spec_for(self, model: str) -> ModelSpec:
        return spec_for(model)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        kwargs = render(request)
        async with self._slots:
            response = await self.client.responses.create(**kwargs)
        result = parse(response)
        _log(result, response)
        return result

    async def batch(self, items) -> Any:
        raise NotImplementedError("batches run on Anthropic (validate keeps the batch roles there)")


def _log(result: ModelResponse, response) -> None:
    """One line per call: nothing else records what OpenAI work costs."""
    u = result.usage
    details = getattr(response.usage, "output_tokens_details", None)
    cost = estimate_cost_usd({"uncached_in": u.input_tokens, "cache_read": u.cache_read_input_tokens,
                              "cache_write": u.cache_creation_input_tokens, "out": u.output_tokens},
                             result.model)
    logger.info(f"OpenAI {result.model}: {result.stop_detail}, {result.stop_reason}; in "
                f"{u.input_tokens:,} uncached / {u.cache_read_input_tokens:,} read / "
                f"{u.cache_creation_input_tokens:,} written, out {u.output_tokens:,} "
                f"(reasoning {getattr(details, 'reasoning_tokens', 0) or 0:,})"
                + (f", ~${cost:.4f}" if cost is not None else ""))
