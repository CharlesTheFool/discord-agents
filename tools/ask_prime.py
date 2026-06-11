"""
ask_prime (v0.9) - a particular asks its Prime to act in another server.

Order is law: mechanical gates (DM check, target resolution, vault gates,
daily caps) run BEFORE any model call; the Prime's judgment is one bounded
structured-output call; approval enqueues a coordination send on the
agentic engine and optionally registers a standing watch.
"""

import logging
from datetime import date
from typing import Optional

from core.internal_constants import (
    PRIME_ASKS_PER_CHANNEL_PER_DAY,
    PRIME_ASKS_PER_SERVER_PER_DAY,
    PRIME_JUDGMENT_PROMPT,
)

logger = logging.getLogger(__name__)

ASK_PRIME_TOOL = {
    "name": "ask_prime",
    "description": (
        "Ask your Prime - the you above all servers - to pose a question or "
        "make an announcement in another server you inhabit. The Prime can "
        "refuse. Optionally keep a watch so the answer gets brought back "
        "here. Use sparingly; this interrupts another room."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "description": "What to say or ask over there, and why it matters here",
            },
            "target_server": {
                "type": "string",
                "description": "Target server name or id",
            },
            "target_channel": {
                "type": "string",
                "description": "Optional channel name or id in the target server",
            },
            "watch_for_response": {
                "type": "boolean",
                "description": "Keep a watch and relay the answer back here (default false)",
            },
        },
        "required": ["request", "target_server"],
    },
}

JUDGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "approve": {"type": "boolean"},
        "message": {
            "type": "string",
            "description": "If approved: what to carry over, plainly phrased",
        },
        "reason": {
            "type": "string",
            "description": "If refused: why, in a sentence the asking side can relay",
        },
    },
    "required": ["approve", "message", "reason"],
    "additionalProperties": False,
}


class AskPrimeExecutor:
    """Executes ask_prime calls; constructed in on_ready (needs the client)."""

    def __init__(self, anthropic, model: str, vault_ids, watch_manager,
                 agentic_engine, discord_client, message_memory=None):
        self.anthropic = anthropic
        self.model = model
        self.vaults = {str(v) for v in (vault_ids or [])}
        self.watch_manager = watch_manager
        self.agentic_engine = agentic_engine
        self.discord_client = discord_client
        self.message_memory = message_memory
        # Daily ask budgets: per origin channel and per target server
        self._asks_by_origin_channel = {}
        self._asks_by_target_server = {}
        self._counts_date = date.today()

    def _resolve_target(self, target_server: str, target_channel: Optional[str]):
        """(server, channel) discord objects for a name-or-id pair, or None."""
        needle = (target_server or "").strip()
        guild = None
        for g in self.discord_client.guilds:
            if str(g.id) == needle or g.name.casefold() == needle.casefold():
                guild = g
                break
        if guild is None:
            return None
        if target_channel:
            c_needle = target_channel.strip().lstrip("#")
            for c in guild.text_channels:
                if str(c.id) == c_needle or c.name.casefold() == c_needle.casefold():
                    return guild, c
            return None
        # No channel named: the server's first text channel carries announcements
        return (guild, guild.text_channels[0]) if guild.text_channels else None

    def _over_caps(self, origin_channel_id: str, target_server_id: str) -> bool:
        today = date.today()
        if today != self._counts_date:
            self._asks_by_origin_channel = {}
            self._asks_by_target_server = {}
            self._counts_date = today
        return (
            self._asks_by_origin_channel.get(origin_channel_id, 0)
            >= PRIME_ASKS_PER_CHANNEL_PER_DAY
            or self._asks_by_target_server.get(target_server_id, 0)
            >= PRIME_ASKS_PER_SERVER_PER_DAY
        )

    def _count_ask(self, origin_channel_id: str, target_server_id: str) -> None:
        self._asks_by_origin_channel[origin_channel_id] = (
            self._asks_by_origin_channel.get(origin_channel_id, 0) + 1)
        self._asks_by_target_server[target_server_id] = (
            self._asks_by_target_server.get(target_server_id, 0) + 1)

    async def execute(self, tool_input: dict,
                      current_server_id: Optional[str] = None,
                      current_channel_id: Optional[str] = None) -> str:
        request = (tool_input.get("request") or "").strip()
        if not request:
            return "Error: nothing to ask - the request was empty."

        # 1. Mechanical: no Prime-asking from DMs (you're already there)
        if current_server_id is None:
            return ("ask_prime isn't available in DMs - this conversation "
                    "already is the Prime.")

        # 2. Resolve the target
        resolved = self._resolve_target(
            tool_input.get("target_server", ""), tool_input.get("target_channel"))
        if resolved is None:
            return ("Couldn't find that server or channel among the places "
                    "you live - check the name and try again.")
        guild, channel = resolved

        if str(guild.id) == str(current_server_id):
            return ("That target is this server - just say it here yourself, "
                    "no Prime needed.")

        # 3. Mechanical vault gates: nothing crosses a vault boundary, either way
        if {str(guild.id), str(channel.id)} & self.vaults or \
                {str(current_server_id), str(current_channel_id)} & self.vaults:
            return "Can't do that one, sorry - that's a door that stays closed."

        # 4. Daily caps, before any model spend
        if self._over_caps(str(current_channel_id), str(guild.id)):
            return ("The Prime's done enough carrying for today - try again "
                    "tomorrow if it still matters.")

        # 5. The Prime's judgment: one bounded call, fail-closed
        origin_guild = self.discord_client.get_guild(int(current_server_id))
        origin_name = origin_guild.name if origin_guild else "another server"
        try:
            response = await self.anthropic.beta.messages.create(
                model=self.model,
                max_tokens=600,
                system=PRIME_JUDGMENT_PROMPT.format(
                    server_list=", ".join(g.name for g in self.discord_client.guilds)),
                messages=[{
                    "role": "user",
                    "content": (
                        f"From {origin_name}, your presence asks: {request}\n"
                        f"Target: #{channel.name} in {guild.name}."
                    ),
                }],
                output_config={"format": {"type": "json_schema", "schema": JUDGMENT_SCHEMA}},
            )
            import json as _json
            verdict = _json.loads(
                next(b.text for b in response.content if b.type == "text"))
        except Exception as e:
            logger.error(f"Prime judgment call failed: {e}", exc_info=True)
            return ("The Prime didn't come back with an answer - treat that "
                    "as a no for now.")

        if not verdict["approve"]:
            reason = verdict["reason"].strip() or "no reason given"
            logger.info(f"Prime refused ask from {current_channel_id}: {reason}")
            return f"The Prime says no: {reason}"

        # 6. Approved: count, enqueue the send, maybe register the watch
        self._count_ask(str(current_channel_id), str(guild.id))

        from core.proactive_action import ProactiveAction
        action = ProactiveAction(
            type="coordination",
            priority="high",
            server_id=str(guild.id),
            channel_id=str(channel.id),
            message=verdict["message"].strip() or request,
            context=f"asked from {origin_name}",
            delivery_method="immediate",
        )
        self.agentic_engine.enqueue_coordination(action)

        watch_note = ""
        if tool_input.get("watch_for_response"):
            watch = self.watch_manager.register(
                question=request,
                target_server_id=str(guild.id),
                target_channel_id=str(channel.id),
                origin_server_id=str(current_server_id),
                origin_channel_id=str(current_channel_id),
            )
            watch_note = (" A watch is set - the answer comes back here when it lands."
                          if watch else
                          " Couldn't set a watch though - too many already standing.")

        if self.message_memory:
            try:
                await self.message_memory.add_event(
                    "relay", current_server_id, current_channel_id,
                    {
                        "triggers": [], "thinking": "", "tool_calls": [],
                        "response": None,
                        "request": request,
                        "provenance": (f"ask_prime → #{channel.name} in {guild.name}"
                                       + (" · watch standing" if watch_note.startswith(" A watch") else "")),
                    },
                )
            except Exception:
                logger.exception("relay event emit failed")

        logger.info(f"Prime approved ask: {current_channel_id} → {guild.name}#{channel.name}")
        return (f"The Prime approved it - it'll be posted in #{channel.name} "
                f"({guild.name}) shortly.{watch_note}")
