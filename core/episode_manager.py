"""
Episode Manager - Episodic Session Boundaries and Distillation (v0.6.0)

Boundaries are properties of the channel's message timeline (idle gaps, span
mass), computable from the message store at any time - never of the bot
process being up. One code path serves live triggers, startup catch-up, and
(future) retroactive runs. See REDESIGN.md section 2.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from core.internal_constants import (
    EPISODE_IDLE_GAP_HOURS,
    EPISODE_MASS_TOKEN_LIMIT,
    EPISODE_MIN_MESSAGES,
    EPISODE_BOOTSTRAP_DAYS,
    EPISODE_SEED_TAIL_MESSAGES,
    EPISODE_INDEX_SEED_TAIL,
    EPISODE_DISTILL_MODEL,
    EPISODE_DISTILL_MAX_TOKENS,
    EPISODE_RETRY_COOLDOWN_MINUTES,
)

logger = logging.getLogger(__name__)


def segment_open_span(
    messages: list,
    now: datetime,
    idle_gap: timedelta,
    mass_token_limit: int,
    min_messages: int,
    force: bool = False,
) -> Tuple[List[list], list]:
    """
    Find episode boundaries in an open span of messages (chronological).

    A boundary is an idle gap >= idle_gap between consecutive messages, or
    accumulated estimated span mass (chars/4) >= mass_token_limit. The final
    run of messages stays open as the live tail unless it is itself stale
    (now - last message >= idle_gap) or force=True.

    Segments smaller than min_messages merge forward into the next segment
    (a tiny final segment still closes - the watermark must advance).

    Returns:
        (closed_segments, open_tail)
    """
    if not messages:
        return [], []

    # Pass 1: split on idle gaps and mass
    raw_segments: List[list] = []
    current: list = [messages[0]]
    current_mass = len(messages[0].content or "") // 4

    for prev, msg in zip(messages, messages[1:]):
        gap = msg.timestamp - prev.timestamp
        if gap >= idle_gap or current_mass >= mass_token_limit:
            raw_segments.append(current)
            current = []
            current_mass = 0
        current.append(msg)
        current_mass += len(msg.content or "") // 4

    # The final run: close it if stale or forced, else it is the open tail
    tail: list = []
    if force or (now - current[-1].timestamp) >= idle_gap:
        raw_segments.append(current)
    else:
        tail = current

    # Pass 2: merge tiny segments forward
    segments: List[list] = []
    carry: list = []
    for seg in raw_segments:
        seg = carry + seg
        carry = []
        if len(seg) < min_messages:
            carry = seg
        else:
            segments.append(seg)
    if carry:
        # tiny leftover: merge backward if possible, else close as-is
        if segments:
            segments[-1].extend(carry)
        else:
            segments.append(carry)

    return segments, tail


# JSON schema for the distillation call (structured outputs - all fields required)
EPISODE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short episode title (<= 8 words)"},
        "slug": {"type": "string", "description": "kebab-case filename slug, <= 5 words"},
        "summary_markdown": {
            "type": "string",
            "description": "What happened, what was settled, what stayed open, artifacts. Markdown, <= 300 words.",
        },
        "participants": {"type": "array", "items": {"type": "string"}},
        "standing_facts": {
            "type": "array", "items": {"type": "string"},
            "description": "UPDATED standing facts for the channel state (merge existing with new; drop obsolete)",
        },
        "settled_questions": {
            "type": "array", "items": {"type": "string"},
            "description": "UPDATED settled-questions ledger (existing entries plus newly settled ones)",
        },
        "used_jokes": {
            "type": "array", "items": {"type": "string"},
            "description": "UPDATED used-jokes/bits ledger (existing plus jokes/references the BOT made this episode)",
        },
        "open_threads": {
            "type": "array", "items": {"type": "string"},
            "description": "UPDATED open threads (carry over still-open ones, drop resolved, add new)",
        },
        "artifacts": {
            "type": "array", "items": {"type": "string"},
            "description": "Attachment/file references that appeared (empty if none)",
        },
        "index_hook": {"type": "string", "description": "One-sentence hook for the episode index"},
    },
    "required": [
        "title", "slug", "summary_markdown", "participants", "standing_facts",
        "settled_questions", "used_jokes", "open_threads", "artifacts", "index_hook",
    ],
    "additionalProperties": False,
}

DISTILL_SYSTEM_PROMPT = """You are the episode distiller for a Discord bot. A bounded episode of \
channel conversation has ended. Produce a JSON record that (1) summarizes the episode and \
(2) returns the UPDATED rolling channel state.

The channel state ledgers exist to stop the bot from re-answering settled questions and \
repeating its own jokes. Integrate the current state with this episode: carry forward what is \
still true/open, drop what is obsolete, add what is new. Keep every list entry to one line. \
Never replace the current state wholesale - it may contain notes the bot wrote itself; preserve \
anything still relevant."""


def render_episode_file(data: dict, first_msg, last_msg) -> str:
    """Render the episode markdown file (first-class, model-visible artifact)."""
    participants = ", ".join(data["participants"])
    artifacts = "\n".join(f"- {a}" for a in data["artifacts"]) or "- none"
    return f"""---
range_start: {first_msg.message_id}
range_end: {last_msg.message_id}
started: {first_msg.timestamp.isoformat()}
ended: {last_msg.timestamp.isoformat()}
participants: {participants}
---

# {data["title"]}

{data["summary_markdown"]}

## Artifacts
{artifacts}
"""


def render_channel_state(channel_id: str, data: dict, index_lines: List[str]) -> str:
    """
    Render the rolling channel state file. Ledger sections come from the
    distiller; the episode index is code-maintained (deterministic format).
    """
    def section(items):
        return "\n".join(f"- {item}" for item in items) or "- (none yet)"

    index = "\n".join(index_lines) or "- (no episodes yet)"
    return f"""# Channel State - {channel_id}

> Maintained by the episode distiller; the bot may also edit via the memory tool.

## Standing Facts
{section(data["standing_facts"])}

## Settled Questions
{section(data["settled_questions"])}

## Used Jokes / Bits
{section(data["used_jokes"])}

## Open Threads
{section(data["open_threads"])}

## Episode Index
{index}
"""


def split_channel_state(content: Optional[str]) -> Tuple[str, List[str]]:
    """
    Split an existing channel state file into (body, episode_index_lines).
    Tolerates files with no index section and None (missing file).
    """
    if not content:
        return "", []
    parts = content.split("\n## Episode Index")
    body = parts[0]
    index_lines = []
    if len(parts) > 1:
        index_lines = [
            line for line in parts[1].splitlines() if line.strip().startswith("- ")
        ]
        # drop the "(no episodes yet)" placeholder
        index_lines = [l for l in index_lines if "(no episodes yet)" not in l]
    return body, index_lines


class EpisodeManager:
    """
    Owns the single episodization code path: find boundaries in the open span,
    distill each closed segment in order, advance the watermark.
    """

    def __init__(
        self,
        message_memory,
        memory_manager,
        conversation_state_manager,
        anthropic_client,
    ):
        self.message_memory = message_memory
        self.memory_manager = memory_manager
        self.conversation_state_manager = conversation_state_manager
        self.anthropic = anthropic_client
        self._locks: dict = {}
        # Distillation-failure cooldowns: the usage trigger re-fires every
        # turn while over threshold, which would re-attempt (and re-bill) a
        # failing distillation once per message
        self._retry_after: dict = {}

        logger.info("EpisodeManager initialized")

    def _lock_for(self, channel_id: str) -> asyncio.Lock:
        if channel_id not in self._locks:
            self._locks[channel_id] = asyncio.Lock()
        return self._locks[channel_id]

    async def episodize_channel(self, channel_id: str, force: bool = False) -> int:
        """
        Episodize the channel's open span. Returns number of episodes created.

        force=True closes the live tail too (usage-threshold trigger); otherwise
        the tail closes only when stale (idle trigger / catch-up).
        """
        async with self._lock_for(channel_id):
            retry_after = self._retry_after.get(channel_id)
            if retry_after and datetime.utcnow() < retry_after:
                logger.debug(
                    f"Episodization for channel {channel_id} on cooldown until {retry_after}"
                )
                return 0

            watermark = await self.message_memory.get_episode_watermark(channel_id)
            if watermark is None:
                watermark = await self._bootstrap_watermark(channel_id)

            span = await self.message_memory.get_messages_after_id(channel_id, watermark)
            if not span:
                return 0

            segments, tail = segment_open_span(
                span,
                now=datetime.utcnow(),
                idle_gap=timedelta(hours=EPISODE_IDLE_GAP_HOURS),
                mass_token_limit=EPISODE_MASS_TOKEN_LIMIT,
                min_messages=EPISODE_MIN_MESSAGES,
                force=force,
            )
            if not segments:
                return 0

            tail_closed = not tail  # the live conversation itself got episodized

            distilled = 0
            for segment in segments:
                try:
                    await self._distill_segment(channel_id, segment)
                except Exception as e:
                    logger.error(
                        f"Distillation failed for channel {channel_id} "
                        f"(range {segment[0].message_id}-{segment[-1].message_id}): {e}",
                        exc_info=True,
                    )
                    # Watermark stays before this segment; back off so the
                    # per-turn usage trigger doesn't re-bill a failing call
                    self._retry_after[channel_id] = datetime.utcnow() + timedelta(
                        minutes=EPISODE_RETRY_COOLDOWN_MINUTES
                    )
                    break
                await self.message_memory.set_episode_watermark(
                    channel_id, segment[-1].message_id
                )
                distilled += 1

            if distilled:
                self._retry_after.pop(channel_id, None)

            # Reseed the live session if its tail was episodized
            if distilled and tail_closed:
                state = await self.conversation_state_manager.get_or_create(channel_id)
                state.reseed(EPISODE_SEED_TAIL_MESSAGES)
                await self.conversation_state_manager.save(state)

            if distilled:
                logger.info(
                    f"Episodized channel {channel_id}: {distilled} episode(s)"
                    f"{' (session reseeded)' if tail_closed else ''}"
                )
            return distilled

    async def _bootstrap_watermark(self, channel_id: str) -> Optional[str]:
        """
        First encounter with a channel: skip fine episodization of deep history
        (REDESIGN: FTS5 + culture.md cover 'before my time'). Watermark lands on
        the last message older than EPISODE_BOOTSTRAP_DAYS.
        """
        cutoff = datetime.utcnow() - timedelta(days=EPISODE_BOOTSTRAP_DAYS)
        last_old = await self.message_memory.get_last_message_id_before(channel_id, cutoff)
        if last_old is not None:
            await self.message_memory.set_episode_watermark(channel_id, last_old)
            logger.info(
                f"Bootstrapped episode watermark for channel {channel_id} at {last_old} "
                f"(skipping history older than {EPISODE_BOOTSTRAP_DAYS}d)"
            )
        return last_old

    async def _distill_segment(self, channel_id: str, segment: list) -> None:
        """One Haiku call -> episode file + updated channel state file."""
        server_id = await self.message_memory.get_server_for_channel(channel_id)
        if not server_id:
            server_id = segment[0].guild_id

        state_path = self.memory_manager.get_channel_context_path(server_id, channel_id)
        current_state = await self.memory_manager.read(state_path)
        state_body, index_lines = split_channel_state(current_state)

        transcript = "\n".join(
            f"[{m.timestamp:%Y-%m-%d %H:%M}] {m.author_name}"
            f"{' (bot)' if m.is_bot else ''}: {m.content or '[no text]'}"
            f"{' [attachment]' if m.has_attachments else ''}"
            for m in segment
        )

        user_prompt = f"""<current_channel_state>
{state_body or "(no state file yet)"}
</current_channel_state>

<episode_transcript channel_id="{channel_id}" message_range="{segment[0].message_id}-{segment[-1].message_id}">
{transcript}
</episode_transcript>

Distill this episode and return the updated channel state per the schema."""

        response = await self.anthropic.beta.messages.create(
            model=EPISODE_DISTILL_MODEL,
            max_tokens=EPISODE_DISTILL_MAX_TOKENS,
            system=DISTILL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            output_config={"format": {"type": "json_schema", "schema": EPISODE_SCHEMA}},
        )
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)

        # Episode file - filename keyed by range-start ID (idempotent overwrite)
        slug = re.sub(r"[^a-z0-9-]", "", data["slug"].lower().replace(" ", "-"))[:40] or "episode"
        episodes_dir_virtual = self.memory_manager.get_episodes_dir_path(server_id, channel_id)
        episodes_dir = self.memory_manager.resolve_path(episodes_dir_virtual)
        episodes_dir.mkdir(parents=True, exist_ok=True)
        range_start = segment[0].message_id
        for stale in episodes_dir.glob(f"*_{range_start}_*.md"):
            stale.unlink()
        filename = f"{segment[-1].timestamp:%Y-%m-%d_%H%M}_{range_start}_{slug}.md"
        episode_path = f"{episodes_dir_virtual}/{filename}"
        await self.memory_manager.write(
            episode_path, render_episode_file(data, segment[0], segment[-1])
        )

        # Channel state file - code appends the index line (dedup by range key)
        range_key = f"msgs {segment[0].message_id}-{segment[-1].message_id}"
        index_lines = [l for l in index_lines if range_key not in l]
        index_lines.append(
            f"- {segment[-1].timestamp:%Y-%m-%d %H:%M} | {data['title']} | {range_key} | {data['index_hook']}"
        )
        await self.memory_manager.write(
            state_path, render_channel_state(channel_id, data, index_lines)
        )

        logger.info(
            f"Distilled episode '{data['title']}' for channel {channel_id} ({range_key})"
        )

    async def check_idle_channels(self) -> None:
        """Idle-boundary sweep over live channels (called from the periodic loop)."""
        for channel_id in list(self.conversation_state_manager._cache.keys()):
            try:
                latest = await self.message_memory.get_latest_message(channel_id)
                if latest is None:
                    continue
                idle = datetime.utcnow() - latest.timestamp
                if idle >= timedelta(hours=EPISODE_IDLE_GAP_HOURS):
                    await self.episodize_channel(channel_id)
            except Exception as e:
                logger.error(f"Idle episode check failed for channel {channel_id}: {e}",
                             exc_info=True)

    async def catch_up_all_channels(self) -> None:
        """Startup catch-up: episodize every channel's open span (offline-resistant)."""
        try:
            for server_id in await self.message_memory.get_active_servers():
                for channel_id in await self.message_memory.get_channels_in_server(server_id):
                    try:
                        await self.episodize_channel(channel_id)
                    except Exception as e:
                        logger.error(f"Catch-up episodization failed for {channel_id}: {e}",
                                     exc_info=True)
            logger.info("Episode catch-up pass complete")
        except Exception as e:
            logger.error(f"Episode catch-up pass failed: {e}", exc_info=True)
