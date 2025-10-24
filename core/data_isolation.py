"""
Data Isolation Enforcement

Provides granular access controls for memory, message search, and Discord tools.
Defaults to permissive mode - users opt-in to restrictions.
"""

import logging
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import DataIsolationConfig

logger = logging.getLogger(__name__)


class DataIsolationEnforcer:
    """
    Enforce data isolation rules based on configuration.

    Responsibilities:
    - Validate memory access based on scope rules
    - Restrict message search to appropriate channels/servers
    - Control Discord tool access across boundaries
    - Provide transparency about access scope
    """

    def __init__(self, config: 'DataIsolationConfig'):
        """
        Initialize Data Isolation Enforcer.

        Args:
            config: Data isolation configuration
        """
        self.config = config

        if config.enabled:
            logger.info(f"Data isolation enabled (mode: {config.default_mode})")
        else:
            logger.info("Data isolation disabled (permissive mode)")

    def validate_memory_access(
        self,
        requested_path: str,
        current_server_id: str,
        current_channel_id: str
    ) -> tuple[bool, Optional[str]]:
        """
        Validate if memory file access is allowed.

        Args:
            requested_path: Memory file path being requested
            current_server_id: Current server/guild ID
            current_channel_id: Current channel ID

        Returns:
            Tuple of (is_allowed, reason_if_denied)
        """
        if not self.config.enabled:
            return True, None

        # Parse requested path to extract server_id
        # Format: /memories/{bot_id}/servers/{server_id}/...
        path_parts = requested_path.split('/')

        if len(path_parts) < 5 or path_parts[3] != 'servers':
            # Invalid path or not server-scoped
            return True, None

        requested_server_id = path_parts[4]

        # Check memory scope
        if self.config.memory_scope == "global":
            # Global scope - all access allowed
            return True, None

        elif self.config.memory_scope == "server":
            # Server scope - only same server
            if requested_server_id != current_server_id:
                return False, f"Memory access restricted to current server only"
            return True, None

        elif self.config.memory_scope == "channel":
            # Channel scope - only same channel
            # Check if path is channel-specific
            if 'channels' in path_parts:
                channel_idx = path_parts.index('channels') + 1
                if channel_idx < len(path_parts):
                    requested_channel_id = path_parts[channel_idx].replace('.md', '').replace('_stats.json', '')
                    if requested_channel_id != current_channel_id:
                        return False, f"Memory access restricted to current channel only"

            # Also check server
            if requested_server_id != current_server_id:
                return False, f"Memory access restricted to current server only"

            return True, None

        # Default: allow
        return True, None

    def get_search_scope(
        self,
        current_server_id: str,
        current_channel_id: str
    ) -> Dict[str, Optional[str]]:
        """
        Get search scope restrictions for message queries.

        Args:
            current_server_id: Current server/guild ID
            current_channel_id: Current channel ID

        Returns:
            Dictionary with 'server_id' and 'channel_id' constraints (None = unrestricted)
        """
        if not self.config.enabled:
            return {"server_id": None, "channel_id": None}

        scope = {}

        if self.config.search_scope == "global":
            scope["server_id"] = None
            scope["channel_id"] = None

        elif self.config.search_scope == "server":
            scope["server_id"] = current_server_id
            scope["channel_id"] = None

        elif self.config.search_scope == "channel":
            scope["server_id"] = current_server_id
            scope["channel_id"] = current_channel_id

        else:
            # Default: global
            scope["server_id"] = None
            scope["channel_id"] = None

        return scope

    def validate_discord_tool_access(
        self,
        target_channel_id: str,
        target_server_id: str,
        current_server_id: str,
        current_channel_id: str
    ) -> tuple[bool, Optional[str]]:
        """
        Validate if Discord tool can access the target channel/server.

        Args:
            target_channel_id: Channel being accessed
            target_server_id: Server of target channel
            current_server_id: Current server/guild ID
            current_channel_id: Current channel ID

        Returns:
            Tuple of (is_allowed, reason_if_denied)
        """
        if not self.config.enabled:
            return True, None

        if self.config.discord_tools_scope == "global":
            return True, None

        elif self.config.discord_tools_scope == "server":
            if target_server_id != current_server_id:
                return False, "Discord tools restricted to current server only"
            return True, None

        elif self.config.discord_tools_scope == "channel":
            if target_channel_id != current_channel_id:
                return False, "Discord tools restricted to current channel only"
            return True, None

        # Default: allow
        return True, None

    def get_transparency_message(self) -> str:
        """
        Generate a transparency message explaining current access scope.

        This should be included in the system prompt so Claude understands
        what data it can access.

        Returns:
            Formatted message explaining access scope
        """
        if not self.config.enabled:
            return """
# Data Access Scope
You have unrestricted access to:
- All memory files across all servers and channels
- Message history from all servers and channels
- Discord tools can query any channel

This bot operates in permissive mode with no isolation restrictions.
"""

        lines = ["# Data Access Scope"]

        # Memory scope
        if self.config.memory_scope == "global":
            lines.append("- Memory files: Unrestricted (all servers and channels)")
        elif self.config.memory_scope == "server":
            lines.append("- Memory files: Server-scoped (current server only)")
        elif self.config.memory_scope == "channel":
            lines.append("- Memory files: Channel-scoped (current channel only)")

        # Search scope
        if self.config.search_scope == "global":
            lines.append("- Message search: Unrestricted (all servers and channels)")
        elif self.config.search_scope == "server":
            lines.append("- Message search: Server-scoped (current server only)")
        elif self.config.search_scope == "channel":
            lines.append("- Message search: Channel-scoped (current channel only)")

        # Discord tools scope
        if self.config.discord_tools_scope == "global":
            lines.append("- Discord tools: Unrestricted (can query any channel)")
        elif self.config.discord_tools_scope == "server":
            lines.append("- Discord tools: Server-scoped (current server only)")
        elif self.config.discord_tools_scope == "channel":
            lines.append("- Discord tools: Channel-scoped (current channel only)")

        lines.append("\nYou should respect these boundaries when using tools and accessing data.")

        return "\n".join(lines)
