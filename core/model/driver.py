"""
The model driver protocol: one request shape in, one response shape out,
whatever provider sits behind it.

Engines build a ModelRequest in our own vocabulary and never touch an SDK.
An adapter (core/model/adapters/) renders it for its wire API, makes the
call, and hands back a ModelResponse whose content blocks are
Anthropic-shaped (type, text, name, input, id, thinking, signature, ...),
so engine code that walks response.content works on any driver. The raw
blocks are kept as returned, which lets the tool loop append them for
in-turn continuation exactly as before (signatures and server blocks
round-trip); `persistable` is the same turn as plain dicts in the persisted
vocabulary (core/model/messages.py), which is what conversation state
stores.
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol, Tuple, Union

from core.model.catalog import ModelSpec

STOP_REASONS = ("end_turn", "tool_use", "max_tokens", "refusal", "pause", "other")


@dataclass
class ModelRequest:
    """One model call, in our vocabulary.

    Caching is declared, not built: the engine says what is stable and the
    adapter places the markers its provider needs (cache_system: the system
    prompt; cache_messages: everything up to the last message). The
    volatile tail rides after both, as a final user turn, and is never
    persisted.
    """
    model: str
    max_tokens: int
    messages: list                     # message dicts; in-loop turns may hold raw blocks
    system: Union[str, list, None] = None   # text or text blocks, no cache markers
    tools: list = field(default_factory=list)   # ToolDefs from the registry
    cache_system: bool = False
    cache_messages: bool = False
    volatile_tail: str = ""
    thinking: bool = False
    effort: Optional[str] = None       # dropped where the model doesn't support it
    output_schema: Optional[dict] = None
    # Anthropic transport. None means "don't send the parameter at all",
    # which differs from an empty list on the wire.
    betas: Optional[list] = None
    container: Optional[dict] = None   # skills container (removed in 0.14.0)
    stream: bool = False
    beta: bool = False


@dataclass
class Usage:
    """Token usage with every field present. input_tokens is the uncached
    part only; total_input_tokens() adds the cache fields back."""
    input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ModelResponse:
    content: list                      # Anthropic-shaped block objects, as returned
    stop_reason: str                   # one of STOP_REASONS
    usage: Usage
    model: str
    persistable: list = field(default_factory=list)   # content as dicts safe to store
    # Provider extras: the code-execution container (removed in 0.14.0)
    container_id: Optional[str] = None
    container_file_ids: list = field(default_factory=list)
    raw: Any = None                    # the provider's own object, for logging only


@dataclass
class BatchItem:
    """One batch result: type "succeeded" carries message, others error."""
    type: str
    message: Optional[ModelResponse] = None
    error: Any = None


class StaleFileError(Exception):
    """A file referenced in the request no longer exists provider-side. The
    caller re-resolves the attachment and retries."""

    def __init__(self, file_id: str):
        super().__init__(f"file {file_id} not found")
        self.file_id = file_id


class BatchTimeoutError(Exception):
    """A batch didn't finish within its time budget; nothing was applied."""


def total_input_tokens(usage) -> int:
    """Full context size: uncached input plus both cache fields."""
    return (
        usage.input_tokens
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    )


class ModelDriver(Protocol):
    def spec_for(self, model: str) -> ModelSpec: ...

    async def generate(self, request: ModelRequest) -> ModelResponse: ...

    async def batch(self, items: List[Tuple[str, ModelRequest]]) -> dict:
        """[(custom_id, request)] -> {custom_id: BatchItem}. Drivers without
        a batch API run the requests one by one."""
        ...
