"""
Vaults (v0.7.0) - the one mechanical isolation gate.

A vault is a channel or server whose content never leaves it: excluded from
outside search and attachment access, its memory files unreadable from
outside, memory writes from inside contained to its own tree (0.13.1). Inside a vault the bot is fully
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
        # Set post-construction when MessageMemory is available.
        self.thread_parent_resolver = None  # sync: channel_id -> parent_id or None
        self.threads_of = None              # sync: vault_channel_id -> [thread ids]
        # Set post-construction when UserCache is available (v0.9).
        self.dm_partner_resolver = None     # sync: dm channel_id -> user_id or None

    @property
    def active(self) -> bool:
        return bool(self.vaults)

    def _context_ids(self, server_id, channel_id) -> set:
        ids = {str(i) for i in (server_id, channel_id) if i}
        if channel_id and self.thread_parent_resolver:
            parent = self.thread_parent_resolver(str(channel_id))
            if parent:
                ids.add(str(parent))
        return ids

    def is_inside(self, server_id, channel_id) -> bool:
        """Is the current context inside ANY vault?"""
        return bool(self.vaults & self._context_ids(server_id, channel_id))

    def excluded_ids(self, server_id, channel_id) -> List[str]:
        """Vault ids the context is NOT inside - excluded from search/listing SQL.
        Expands vaulted channels with their thread ids."""
        base = self.vaults - self._context_ids(server_id, channel_id)
        expanded = set(base)
        if self.threads_of:
            for vid in base:
                expanded.update(str(t) for t in (self.threads_of(vid) or []))
        return sorted(expanded)

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

    def _check_dm_memory(self, path: str, command: str,
                         server_id, channel_id,
                         write_grant: Optional[str] = None) -> Optional[Tuple[bool, Optional[str]]]:
        """DM privacy rules (v0.9): every DM is an implicit vault scoped to
        its conversation. Returns a verdict, or None when DM rules have no
        opinion (server vault rules still apply)."""
        parts = path.split("/")
        is_dm_path = len(parts) >= 5 and parts[3] == "global" and parts[4] == "dms"
        in_dm = server_id in (None, "DM")

        if is_dm_path:
            if not in_dm:
                return False, "that's a private conversation's memory - it stays there"
            partner = (self.dm_partner_resolver(str(channel_id))
                       if self.dm_partner_resolver else None)
            if partner:
                own = parts[5:6] == [partner]
            else:
                own = parts[5:7] == ["_unresolved", str(channel_id)]
            if not own:
                return False, "that's a different private conversation's memory - it stays there"
            return True, None

        if in_dm and command in _WRITE_COMMANDS:
            # One-shot consent (/memory): exactly the caller's own global
            # profile - structural match, not a suffix (a crafted path like
            # servers/x/global/users/{uid}.md must NOT pass)
            if (write_grant and len(parts) == 6
                    and parts[3:5] == ["global", "users"]
                    and parts[5] == f"{write_grant}.md"):
                return True, None
            return False, (
                "from a DM, what you write down stays in this conversation's "
                "own memory (its global/dms/ folder)"
            )
        return None

    def check_memory_access(self, path: str, command: str,
                            server_id, channel_id, *,
                            write_grant: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """Gate one memory-tool call. Returns (allowed, reason_if_denied).
        write_grant (v0.9): one-shot /memory consent - the named user's own
        global profile becomes writable from their DM, nothing else."""
        # DM privacy is mechanical and holds even with no vaults configured
        dm_verdict = self._check_dm_memory(path, command, server_id, channel_id,
                                           write_grant=write_grant)
        if dm_verdict is not None:
            return dm_verdict

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
                    # Thread path: channels/{parent}/threads/{tid}
                    if "threads" in parts:
                        t_idx = parts.index("threads") + 1
                        if t_idx < len(parts):
                            tid = parts[t_idx].replace(".md", "").replace("_stats.json", "")
                            excluded = set(self.excluded_ids(server_id, channel_id))
                            if tid in excluded:
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

        if command in _WRITE_COMMANDS and not self._inside_own_vault_tree(
                parts, server_id, channel_id):
            return False, "that's outside this vault - notes made here stay here"

        return True, None

    def _inside_own_vault_tree(self, parts: List[str], server_id, channel_id) -> bool:
        """From inside a vault, writes stay in that vault's own tree; the
        innermost vault wins (a vaulted channel in a vaulted server keeps its
        notes in the channel's tree, out of the rest of the server's reach).
        True when the context is in no vault at all."""
        inside = self.vaults & self._context_ids(server_id, channel_id)
        if not inside:
            return True
        # parts: ['', 'memories', bot, 'servers', sid, ...rest]
        if len(parts) < 6 or parts[3] != "servers" or parts[4] != str(server_id):
            return False
        rest = parts[5:]
        if rest == ["followups.json"]:
            return True   # follow-ups are server-level by design (SECURITY.md residual)
        channel_vaults = inside - {str(server_id)}
        if not channel_vaults:
            return True   # a vaulted server: its whole tree
        if rest[0] != "channels" or len(rest) < 2:
            return False
        names = [n.replace(".md", "").replace("_stats.json", "") for n in rest[1:]]
        # channels/{C}[.md|_stats.json|/...], or a vaulted thread's
        # channels/{parent}/threads/{T}[.md|/...]
        return (names[0] in channel_vaults
                or (len(names) >= 3 and names[1] == "threads" and names[2] in channel_vaults))
