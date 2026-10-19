"""Network approvals (spec 5.8): the workshop's one runtime approval.

`request_network_access` records a request and DMs every operator with
Allow / Deny / Details buttons. The DM's text is only the host and where
the request came from: the bot stores its own messages and the operator
DM's context replays them, so a model-written reason (which could quote a
vaulted channel or someone else's DM) is shown only as an ephemeral reply
to Details, never as a stored message. The first operator decision wins;
an Allow lands in that scope's allowlist; either way a persisted note
reaches the requesting channel and a note-driven turn follows. Requests
expire after 24 hours.
"""
import asyncio
import ipaddress
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import discord

from core.internal_constants import (
    WORKSHOP_APPROVAL_EXPIRY_HOURS,
    WORKSHOP_PENDING_APPROVALS_PER_SCOPE,
)
from core.workshop.notes import NOTE_HEAD, NOTE_TAIL

logger = logging.getLogger(__name__)

CUSTOM_ID = r"da-net:(?P<action>allow|deny|details):(?P<rid>[0-9a-f]{12})"
_HOSTNAME = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
REASON_CHARS = 300
EXPIRY_CHECK_SECONDS = 600


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def normalize_host(host: str) -> str:
    """A plain host name in its ASCII (punycode) form. Raises ValueError."""
    host = (host or "").strip().lower().rstrip(".")
    for prefix in ("https://", "http://"):
        if host.startswith(prefix):
            host = host[len(prefix):]
    if _is_ip(host.strip("[]")):
        raise ValueError("IP addresses can't be allowed, only host names")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError(f"{host!r} isn't a valid host name") from None
    if not _HOSTNAME.match(host):
        raise ValueError(f"{host!r} isn't a host name (no scheme, port, path or wildcard)")
    return host


def resolve_operators(app_info, configured: List[str]) -> List[str]:
    """workshop.operators if set; else the app's owner, or for a team-owned
    app the team's owner and its members with the ADMIN role only."""
    if configured:
        return [str(v) for v in configured]
    team = getattr(app_info, "team", None)
    if team is not None:
        ids = {str(team.owner_id)} if getattr(team, "owner_id", None) else set()
        for member in team.members:
            if getattr(member, "role", None) == discord.TeamMemberRole.admin:
                ids.add(str(member.id))
        return sorted(ids)
    return [str(app_info.owner.id)]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalBook:
    """Requests live in the workshop's state file (the bot is its one writer)."""

    def __init__(self, state):
        self.state = state

    @property
    def _records(self) -> Dict[str, dict]:
        return self.state.data["pending_approvals"]

    def get(self, rid: str) -> Optional[dict]:
        return self._records.get(rid)

    def pending(self, scope_key: str) -> List[dict]:
        return [r for r in self._records.values() if r["scope_key"] == scope_key and not r["decision"]]

    def create(self, *, scope_key: str, host: str, reason: str, server_id, channel_id) -> dict:
        """A new pending request, or the identical one already waiting
        (`duplicate` set). Raises ValueError when the scope has too many."""
        for r in self.pending(scope_key):
            if r["host"] == host:
                return {**r, "duplicate": True}
        if len(self.pending(scope_key)) >= WORKSHOP_PENDING_APPROVALS_PER_SCOPE:
            raise ValueError(f"{WORKSHOP_PENDING_APPROVALS_PER_SCOPE} requests from this workshop "
                             f"are already waiting for the operator")
        record = {"id": secrets.token_hex(6), "scope_key": scope_key, "host": host,
                  "reason": (reason or "").strip()[:REASON_CHARS],
                  "server_id": None if server_id in (None, "DM") else str(server_id),
                  "channel_id": str(channel_id), "created_at": _now().isoformat(),
                  "decision": None, "decided_by": None, "decided_at": None}
        self._records[record["id"]] = record
        self.state.save()
        return record

    def decide(self, rid: str, operator_id: str, allow: bool):
        """(record, None) for the first decision; (None, why) otherwise."""
        record = self._records.get(rid)
        if record is None:
            return None, "That request no longer exists."
        if record["decision"]:
            return None, f"Already {record['decision']}."
        if self._expired(record):
            return None, "That request expired."
        record.update(decision="allowed" if allow else "denied", decided_by=str(operator_id),
                      decided_at=_now().isoformat())
        if allow:
            self.state.approve_host(record["scope_key"], record["host"])
        self.state.save()
        return record, None

    def _expired(self, record: dict, now: Optional[datetime] = None) -> bool:
        created = datetime.fromisoformat(record["created_at"])
        return (now or _now()) - created >= timedelta(hours=WORKSHOP_APPROVAL_EXPIRY_HOURS)

    def expire(self, now: Optional[datetime] = None) -> List[dict]:
        """Mark undecided requests past 24 h expired; drop decided ones a week old."""
        now = now or _now()
        expired, changed = [], False
        for rid, record in list(self._records.items()):
            if not record["decision"] and self._expired(record, now):
                record.update(decision="expired", decided_at=now.isoformat())
                expired.append(record)
                changed = True
            elif record["decision"] and now - datetime.fromisoformat(record["decided_at"]) > timedelta(days=7):
                del self._records[rid]
                changed = True
        if changed:
            self.state.save()
        return expired


DECISION_NOTES = {
    "allowed": "The operator allowed {host} for your workshop; requests to it go through now.",
    "denied": "The operator declined network access to {host} for your workshop.",
    "expired": "Your request for network access to {host} expired without an answer.",
}


class Approvals:
    """The book wired to Discord: operator DMs, button clicks, notes, expiry."""

    def __init__(self, workshop, client, notes, request_turn: Callable[[str], None], config,
                 register_dm: Optional[Callable] = None):
        self.workshop = workshop
        self.client = client
        self.notes = notes
        self.request_turn = request_turn
        self.config = config
        self.register_dm = register_dm   # records the outbound DM so its memory routes right
        self.book = ApprovalBook(workshop.state)
        self.operators: List[str] = []
        self._expiry_task = None

    async def resolve_operators(self) -> None:
        info = await self.client.application_info()
        self.operators = resolve_operators(info, self.config.workshop.operators)
        logger.info(f"workshop operators: {self.operators}")

    def start(self) -> None:
        self._expiry_task = asyncio.ensure_future(self._expiry_loop())

    async def close(self) -> None:
        if self._expiry_task:
            self._expiry_task.cancel()

    def _label(self, record: dict) -> str:
        """Where a request came from - names only, never content."""
        channel = self.client.get_channel(int(record["channel_id"]))
        if record["server_id"] is None:
            partner = getattr(getattr(channel, "recipient", None), "display_name", None)
            return f"a DM with {partner}" if partner else "a DM"
        guild = self.client.get_guild(int(record["server_id"]))
        where = f"#{channel.name}" if channel is not None and getattr(channel, "name", None) else "a channel"
        return f"{where} in {guild.name if guild else 'a server'}"

    async def request(self, scope, host: str, reason: str, *, server_id, channel_id) -> str:
        """The tool's body: refuse what's invalid or moot, else ask."""
        try:
            host = normalize_host(host)
        except ValueError as e:
            return f"Error: {e}."
        allow, open_internet = self.workshop.allowed(scope.key)
        if open_internet or host in allow:
            return f"{host} is already reachable from this workshop - no need to ask."
        if not self.operators:
            return "There's no operator to ask right now, so this can't be approved."
        try:
            record = self.book.create(scope_key=scope.key, host=host, reason=reason,
                                      server_id=server_id, channel_id=channel_id)
        except ValueError as e:
            return f"Can't ask right now: {e}."
        if record.get("duplicate"):
            return f"A request for {host} is already waiting for the operator; you'll hear back here."
        sent = 0
        for operator_id in self.operators:
            try:
                user = await self.client.fetch_user(int(operator_id))
                dm = await user.create_dm()
                await dm.send(self.dm_text(record), view=approval_view(record["id"]))
                if self.register_dm:
                    await self.register_dm(dm)
                sent += 1
            except discord.HTTPException as e:
                logger.warning(f"approval DM to operator {operator_id} failed: {e}")
        if not sent:
            return "Couldn't reach the operator just now, so the request didn't go out."
        return f"Asked the operator about {host}; you'll hear back here when they decide."

    def dm_text(self, record: dict, decided: Optional[str] = None) -> str:
        lines = ["**Network request from your bot's workshop**",
                 f"Host: `{record['host']}`", f"From: {self._label(record)}"]
        if decided:
            lines.append(decided)
        else:
            lines.append("Allow that workshop to reach this host? (HTTPS only; it stays allowed "
                         "for that workshop.) Details shows the bot's reason, privately.")
        return "\n".join(lines)

    async def handle_click(self, interaction: discord.Interaction, action: str, rid: str) -> None:
        if str(interaction.user.id) not in self.operators:
            await interaction.response.send_message(
                "Only the workshop's operator can decide this.", ephemeral=True)
            return
        record = self.book.get(rid)
        if action == "details":
            text = (f"Reason given: {record['reason'] or '(none)'}" if record
                    else "That request no longer exists.")
            await interaction.response.send_message(text, ephemeral=True)
            return
        decided, why = self.book.decide(rid, str(interaction.user.id), allow=(action == "allow"))
        if decided is None:
            await interaction.response.send_message(why, ephemeral=True)
            return
        await interaction.response.edit_message(
            content=self.dm_text(decided, f"**{decided['decision'].capitalize()}** by "
                                          f"{interaction.user.display_name}."), view=None)
        await self._tell_requester(decided)

    async def _tell_requester(self, record: dict) -> None:
        note = NOTE_HEAD + DECISION_NOTES[record["decision"]].format(host=record["host"]) + NOTE_TAIL
        if await self.notes.write(record["channel_id"], note):
            self.request_turn(record["channel_id"])

    async def expire_due(self) -> None:
        for record in self.book.expire():
            await self._tell_requester(record)

    async def _expiry_loop(self) -> None:
        while True:
            await asyncio.sleep(EXPIRY_CHECK_SECONDS)
            try:
                await self.expire_due()
            except Exception:
                logger.exception("approval expiry pass failed")


class ApprovalButton(discord.ui.DynamicItem[discord.ui.Button], template=CUSTOM_ID):
    """Survives restarts: discord.py matches any click on a custom_id of this
    shape to this class, so no per-request registration is needed."""
    controller: Optional[Approvals] = None

    def __init__(self, action: str, rid: str):
        style = {"allow": discord.ButtonStyle.success, "deny": discord.ButtonStyle.danger,
                 "details": discord.ButtonStyle.secondary}[action]
        super().__init__(discord.ui.Button(label=action.capitalize(), style=style,
                                           custom_id=f"da-net:{action}:{rid}"))
        self.action = action
        self.rid = rid

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"], match["rid"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if ApprovalButton.controller is None:
            await interaction.response.send_message("The workshop isn't running.", ephemeral=True)
            return
        await ApprovalButton.controller.handle_click(interaction, self.action, self.rid)


def approval_view(rid: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in ("allow", "deny", "details"):
        view.add_item(ApprovalButton(action, rid))
    return view
