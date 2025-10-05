"""
Memory Manager - Memory Tool Wrapper

Provides path helpers and read-only access to memory files.
Bot writes to memories via Claude's memory tool, not directly.
"""

import json
import logging
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Abstraction over Anthropic's memory tool.

    Provides:
    - Standardized path helpers
    - Read-only access to memory files
    - Context building from memories

    Important: This manager does NOT write memories.
    Bot writes via memory tool calls in Claude API responses.
    """

    def __init__(self, bot_id: str, memory_base_path: Path):
        """
        Initialize memory manager for specific bot.

        Args:
            bot_id: Bot identifier (e.g., "alpha")
            memory_base_path: Base path for memories (e.g., "./memories")
        """
        self.bot_id = bot_id
        self.base_path = memory_base_path / bot_id
        self.base_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"MemoryManager initialized for bot '{bot_id}' at {self.base_path}")

    def get_user_profile_path(self, server_id: str, user_id: str) -> str:
        """
        Get standard path for user profile.

        Args:
            server_id: Discord server/guild ID
            user_id: Discord user ID

        Returns:
            Memory tool path (e.g., "/memories/alpha/servers/123/users/456.md")
        """
        return f"/memories/{self.bot_id}/servers/{server_id}/users/{user_id}.md"

    def get_channel_context_path(self, server_id: str, channel_id: str) -> str:
        """
        Get standard path for channel context.

        Args:
            server_id: Discord server/guild ID
            channel_id: Discord channel ID

        Returns:
            Memory tool path (e.g., "/memories/alpha/servers/123/channels/456.md")
        """
        return f"/memories/{self.bot_id}/servers/{server_id}/channels/{channel_id}.md"

    def get_server_culture_path(self, server_id: str) -> str:
        """
        Get standard path for server culture/overview.

        Args:
            server_id: Discord server/guild ID

        Returns:
            Memory tool path (e.g., "/memories/alpha/servers/123/culture.md")
        """
        return f"/memories/{self.bot_id}/servers/{server_id}/culture.md"

    def get_followups_path(self, server_id: str) -> str:
        """
        Get path for follow-ups JSON.

        Args:
            server_id: Discord server/guild ID

        Returns:
            Memory tool path (e.g., "/memories/alpha/servers/123/followups.json")
        """
        return f"/memories/{self.bot_id}/servers/{server_id}/followups.json"

    async def get_followups(self, server_id: str) -> Optional[dict]:
        """
        Get followups data for server.

        Args:
            server_id: Discord server/guild ID

        Returns:
            Followups dict with 'pending' and 'completed' lists, or None
        """
        path = self.get_followups_path(server_id)
        data = await self.read_json(path)

        if not data:
            # Return empty structure
            return {"pending": [], "completed": []}

        return data

    async def write_followups(self, server_id: str, data: dict):
        """
        Write followups data to file (system-level operation).

        This is used by the system when completing or cleaning up followups,
        not for Claude-initiated creation (which uses memory tool).

        Args:
            server_id: Discord server/guild ID
            data: Followups dict with 'pending' and 'completed' lists
        """
        path = self.get_followups_path(server_id)

        # Convert memory tool path to filesystem path
        relative_path = path.replace(f"/memories/{self.bot_id}/", "")
        file_path = self.base_path / relative_path

        # Ensure directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Wrote followups to {path}")

        except Exception as e:
            logger.error(f"Error writing followups to {path}: {e}")

    def get_channel_stats_path(self, server_id: str, channel_id: str) -> str:
        """
        Get path for channel engagement stats JSON.

        Args:
            server_id: Discord server ID
            channel_id: Discord channel ID

        Returns:
            Memory tool path (e.g., "/memories/alpha/servers/123/channels/456_stats.json")
        """
        return f"/memories/{self.bot_id}/servers/{server_id}/channels/{channel_id}_stats.json"

    async def get_engagement_stats(self, server_id: str, channel_id: str) -> dict:
        """
        Get engagement stats for channel from stats file.

        Args:
            server_id: Discord server ID
            channel_id: Discord channel ID

        Returns:
            Stats dict with success_rate, total_attempts, successful_attempts
        """
        path = self.get_channel_stats_path(server_id, channel_id)
        data = await self.read_json(path)

        if not data:
            # No data, return defaults
            return {
                "success_rate": 0.5,
                "total_attempts": 0,
                "successful_attempts": 0,
            }

        total = data.get("total_attempts", 0)
        successful = data.get("successful_attempts", 0)

        # Calculate success rate (default to 0.5 if no attempts yet)
        if total == 0:
            success_rate = 0.5
        else:
            success_rate = successful / total

        return {
            "success_rate": success_rate,
            "total_attempts": total,
            "successful_attempts": successful,
        }

    async def write_engagement_stats(self, server_id: str, channel_id: str, data: dict):
        """
        Write engagement stats to file (system-level operation).

        Args:
            server_id: Discord server ID
            channel_id: Discord channel ID
            data: Stats dict with total_attempts and successful_attempts
        """
        path = self.get_channel_stats_path(server_id, channel_id)

        # Convert memory tool path to filesystem path
        relative_path = path.replace(f"/memories/{self.bot_id}/", "")
        file_path = self.base_path / relative_path

        # Ensure directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Wrote engagement stats to {path}")

        except Exception as e:
            logger.error(f"Error writing engagement stats to {path}: {e}")

    async def read(self, path: str) -> Optional[str]:
        """
        Read memory file.

        Args:
            path: Memory tool path (e.g., "/memories/alpha/servers/123/users/456.md")

        Returns:
            File contents, or None if not found
        """
        # Convert memory tool path to actual filesystem path
        # Remove leading "/memories/{bot_id}/" prefix
        relative_path = path.replace(f"/memories/{self.bot_id}/", "")
        file_path = self.base_path / relative_path

        if not file_path.exists():
            logger.debug(f"Memory file not found: {path}")
            return None

        try:
            with open(file_path, "r") as f:
                content = f.read()
            logger.debug(f"Read memory file: {path} ({len(content)} chars)")
            return content

        except Exception as e:
            logger.error(f"Error reading memory file {path}: {e}")
            return None

    async def read_json(self, path: str) -> Optional[dict]:
        """
        Read and parse JSON memory file.

        Args:
            path: Memory tool path to JSON file

        Returns:
            Parsed JSON dict, or None if not found/invalid
        """
        content = await self.read(path)
        if not content:
            return None

        try:
            data = json.loads(content)
            return data

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in memory file {path}: {e}")
            return None

    def build_memory_context(
        self, server_id: str, channel_id: str, user_ids: List[str]
    ) -> str:
        """
        Build context string from memories for Claude API.

        This assembles relevant memory files into a context block
        that can be included in the Claude API call.

        Args:
            server_id: Discord server/guild ID
            channel_id: Discord channel ID
            user_ids: List of user IDs to include profiles for

        Returns:
            Formatted context string with memory information
        """
        # Note: In Phase 1, we'll keep this simple.
        # Just return the paths that Claude should check.
        # Claude will use memory tool to read them.

        context_parts = []

        context_parts.append("# Available Memories")
        context_parts.append("")
        context_parts.append("You have access to the following memory files:")
        context_parts.append("")

        # Server culture
        culture_path = self.get_server_culture_path(server_id)
        context_parts.append(f"- Server culture: {culture_path}")

        # Channel context
        channel_path = self.get_channel_context_path(server_id, channel_id)
        context_parts.append(f"- Channel context: {channel_path}")

        # User profiles
        if user_ids:
            context_parts.append("- User profiles:")
            for user_id in user_ids[:5]:  # Limit to 5 most recent
                user_path = self.get_user_profile_path(server_id, user_id)
                context_parts.append(f"  - {user_path}")

        context_parts.append("")
        context_parts.append(
            "Use the memory tool to read these files if needed for context."
        )

        return "\n".join(context_parts)

    def validate_path(self, path: str) -> bool:
        """
        Validate that path is within allowed memory directory.
        Prevents directory traversal attacks.

        Args:
            path: Memory tool path to validate

        Returns:
            True if safe, False if potential attack
        """
        # Memory tool paths should start with /memories/{bot_id}/
        expected_prefix = f"/memories/{self.bot_id}/"
        if not path.startswith(expected_prefix):
            logger.warning(f"Invalid memory path (wrong prefix): {path}")
            return False

        # Convert to filesystem path and check
        relative_path = path.replace(expected_prefix, "")
        try:
            file_path = (self.base_path / relative_path).resolve()
            base_resolved = self.base_path.resolve()

            # Check if file_path is within base_path
            file_path.relative_to(base_resolved)
            return True

        except ValueError:
            logger.warning(f"Invalid memory path (traversal attempt): {path}")
            return False
