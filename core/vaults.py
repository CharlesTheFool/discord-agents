"""
Vaults (v0.7.0) - the one mechanical isolation gate.

A vault is a channel or server whose content never leaves it: excluded from
outside search and attachment access, its memory files unreadable from
outside, writes from inside contained. Inside a vault the bot is fully
itself. Everything coarser is Discord's job; everything finer is the
discretion-norms prompt.
"""

import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Memory tool commands that write
_WRITE_COMMANDS = {"create", "str_replace", "insert", "delete", "rename"}


class VaultEnforcer:
    """Pure vault logic; callers thread current_server_id/current_channel_id."""

    def __init__(self, vault_ids: Optional[List[str]] = None):
        self.vaults = {str(v) for v in (vault_ids or []) if str(v).strip()}
        if self.vaults:
            logger.info(f"Vaults active: {sorted(self.vaults)}")

    @property
    def active(self) -> bool:
        return bool(self.vaults)

    def _context_ids(self, server_id, channel_id) -> set:
        return {str(i) for i in (server_id, channel_id) if i}

    def is_inside(self, server_id, channel_id) -> bool:
        """Is the current context inside ANY vault?"""
        return bool(self.vaults & self._context_ids(server_id, channel_id))

    def excluded_ids(self, server_id, channel_id) -> List[str]:
        """Vault ids the context is NOT inside - excluded from search/listing SQL."""
        return sorted(self.vaults - self._context_ids(server_id, channel_id))

    def blocks_content(self, content_server_id, content_channel_id,
                       server_id, channel_id) -> bool:
        """Must content from (content_server, content_channel) stay away from this context?"""
        if not self.vaults:
            return False
        excluded = set(self.excluded_ids(server_id, channel_id))
        content = {str(i) for i in (content_server_id, content_channel_id) if i}
        return bool(excluded & content)

    def blocks_repository_save(self, server_id, channel_id) -> bool:
        """Saving from a vaulted CHANNEL into the server-visible repo would leak.
        A vaulted SERVER's repo is inside the vault - fine."""
        return str(channel_id or "") in self.vaults

    def check_memory_access(self, path: str, command: str,
                            server_id, channel_id) -> Tuple[bool, Optional[str]]:
        """Gate one memory-tool call. Returns (allowed, reason_if_denied)."""
        if not self.vaults:
            return True, None

        parts = path.split("/")
        # /memories/{bot}/servers/{sid}/... -> ['', 'memories', bot, 'servers', sid, ...]
        if len(parts) >= 5 and parts[3] == "servers":
            sid = parts[4]
            if sid in self.vaults and sid not in self._context_ids(server_id, channel_id):
                return False, (
                    "that path belongs to a vaulted server - it can only be "
                    "touched from inside it"
                )
            if "channels" in parts:
                idx = parts.index("channels") + 1
                if idx < len(parts):
                    cid = parts[idx].replace(".md", "").replace("_stats.json", "")
                    if cid in self.vaults and cid not in self._context_ids(server_id, channel_id):
                        return False, (
                            "that path belongs to a vaulted channel - it can "
                            "only be touched from inside it"
                        )

        # Global profile writes from inside a vault would leak onto other servers
        if (command in _WRITE_COMMANDS
                and len(parts) >= 5 and parts[3] == "global" and parts[4] == "users"
                and self.is_inside(server_id, channel_id)):
            return False, (
                "global profile writes are off while you're in a vaulted space - "
                "keep person-notes in this server's channel notes instead"
            )

        return True, None
