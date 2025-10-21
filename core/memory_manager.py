"""
Memory Manager - Wrapper for Anthropic's Memory Tool

Provides standardized paths and read access to memory files.
Bot writes via Claude's memory tool, not this manager.
"""

import json
import logging
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Path helpers and read access for Anthropic's memory tool.

    Memory writes happen via Claude API memory tool calls, not this class.
    This manager provides standardized paths and system-level reads.
    """

    def __init__(self, bot_id: str, memory_base_path: Path):
        self.bot_id = bot_id
        self.base_path = memory_base_path / bot_id
        self.base_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"MemoryManager initialized for bot '{bot_id}' at {self.base_path}")

    def get_user_profile_path(self, server_id: str, user_id: str) -> str:
        """Standard path for user profile memory file"""
        return f"/memories/{self.bot_id}/servers/{server_id}/users/{user_id}.md"

    def get_channel_context_path(self, server_id: str, channel_id: str) -> str:
        """Standard path for channel context memory file"""
        return f"/memories/{self.bot_id}/servers/{server_id}/channels/{channel_id}.md"

    def get_server_culture_path(self, server_id: str) -> str:
        """Standard path for server culture/overview memory file"""
        return f"/memories/{self.bot_id}/servers/{server_id}/culture.md"

    def get_followups_path(self, server_id: str) -> str:
        """Standard path for follow-ups JSON"""
        return f"/memories/{self.bot_id}/servers/{server_id}/followups.json"

    async def get_followups(self, server_id: str) -> Optional[dict]:
        """Get follow-ups for server, returning empty structure if none exist"""
        path = self.get_followups_path(server_id)
        data = await self.read_json(path)

        if not data:
            return {"pending": [], "completed": []}

        return data

    async def write_followups(self, server_id: str, data: dict):
        """
        System-level write for follow-up completion/cleanup.

        Used when the system completes follow-ups, not for Claude-initiated creation.
        Claude creates follow-ups via memory tool.
        """
        path = self.get_followups_path(server_id)
        relative_path = path.replace(f"/memories/{self.bot_id}/", "")
        file_path = self.base_path / relative_path

        file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Wrote followups to {path}")

        except Exception as e:
            logger.error(f"Error writing followups to {path}: {e}")

    def get_channel_stats_path(self, server_id: str, channel_id: str) -> str:
        """Standard path for channel engagement stats JSON"""
        return f"/memories/{self.bot_id}/servers/{server_id}/channels/{channel_id}_stats.json"

    async def get_engagement_stats(self, server_id: str, channel_id: str) -> dict:
        """Get engagement stats for channel, calculating success rate"""
        path = self.get_channel_stats_path(server_id, channel_id)
        data = await self.read_json(path)

        if not data:
            return {
                "success_rate": 0.5,
                "total_attempts": 0,
                "successful_attempts": 0,
            }

        total = data.get("total_attempts", 0)
        successful = data.get("successful_attempts", 0)

        # Default to 0.5 success rate if no attempts yet
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
        """System-level write for engagement tracking"""
        path = self.get_channel_stats_path(server_id, channel_id)
        relative_path = path.replace(f"/memories/{self.bot_id}/", "")
        file_path = self.base_path / relative_path

        file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Wrote engagement stats to {path}")

        except Exception as e:
            logger.error(f"Error writing engagement stats to {path}: {e}")

    async def read(self, path: str) -> Optional[str]:
        """Read memory file, returning None if not found"""
        # Convert memory tool path to filesystem path
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
        """Read and parse JSON memory file, returning None if not found/invalid"""
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
        Build context block listing available memory files for Claude to read.

        Claude uses memory tool to actually read these files.
        This just provides paths in a formatted context block.
        """
        context_parts = []

        context_parts.append("# Available Memories")
        context_parts.append("")
        context_parts.append("You have access to the following memory files:")
        context_parts.append("")

        culture_path = self.get_server_culture_path(server_id)
        context_parts.append(f"- Server culture: {culture_path}")

        channel_path = self.get_channel_context_path(server_id, channel_id)
        context_parts.append(f"- Channel context: {channel_path}")

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
        Validate memory path to prevent directory traversal attacks.

        Ensures path stays within /memories/{bot_id}/ boundary.
        """
        expected_prefix = f"/memories/{self.bot_id}/"
        if not path.startswith(expected_prefix):
            logger.warning(f"Invalid memory path (wrong prefix): {path}")
            return False

        # Convert to filesystem path and check for traversal
        relative_path = path.replace(expected_prefix, "")
        try:
            file_path = (self.base_path / relative_path).resolve()
            base_resolved = self.base_path.resolve()

            # Ensure resolved path is within base_path
            file_path.relative_to(base_resolved)
            return True

        except ValueError:
            logger.warning(f"Invalid memory path (traversal attempt): {path}")
            return False
