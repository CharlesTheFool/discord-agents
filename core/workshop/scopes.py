"""Workshop scopes: which sandbox a conversation works in, and whether it may.

A scope is the unit nothing is shared across: its own sandbox, volume, shell
state and proxy credential. Resolution lives next to the vault rules it
mirrors (`VaultEnforcer.resolve_scope`); this module holds the value type and
the setting lookup.
"""
import re
from dataclasses import dataclass
from typing import Optional

KINDS = ("server", "vault", "dm", "dm-channel")
_NAME_UNSAFE = re.compile(r"[^a-z0-9-]")


@dataclass(frozen=True)
class Scope:
    kind: str   # server | vault | dm | dm-channel
    id: str     # server id, vaulted channel/thread id, DM partner id, or DM channel id

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"

    def sandbox_name(self, install_id: str, bot_id: str) -> str:
        """Runtime-safe and unique per install: MSB_HOME is per OS user, so a
        dev checkout and the installed app on one machine share it."""
        return _NAME_UNSAFE.sub("-", f"{install_id}-{bot_id}-{self.kind}-{self.id}".lower())


def setting_for(scope: Optional[Scope], config, *, server_id=None,
                channel_id=None, parent_id=None) -> str:
    """`container` or `off` for a turn in this place. The most specific
    configured id wins: the channel, its thread parent, the scope, the
    server; then the default. No scope (a thread whose parent is unknown)
    is always off."""
    workshop = config.workshop
    if scope is None or not workshop.enabled:
        return "off"
    for place in (channel_id, parent_id, scope.id, server_id):
        if place is not None and str(place) in workshop.scopes:
            return workshop.scopes[str(place)]
    return workshop.default_setting
