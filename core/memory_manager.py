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
        # Set post-construction by bot_manager after MessageMemory exists.
        # Sync callable: channel_id -> parent_id or None.
        self.thread_parent_resolver = None

        logger.info(f"MemoryManager initialized for bot '{bot_id}' at {self.base_path}")

    def get_global_user_profile_path(self, user_id: str) -> str:
        """One file per human, keyed by Discord user ID (v0.7.0)."""
        return f"/memories/{self.bot_id}/global/users/{user_id}.md"

    def get_global_users_dir(self) -> Path:
        return self.base_path / "global" / "users"

    def get_user_profile_path(self, server_id: str, user_id: str) -> str:
        """Legacy per-server profile path (pre-0.7) - migration + fallback shim only."""
        return f"/memories/{self.bot_id}/servers/{server_id}/users/{user_id}.md"

    def _thread_parent(self, channel_id: str):
        return self.thread_parent_resolver(str(channel_id)) if self.thread_parent_resolver else None

    def get_channel_context_path(self, server_id: str, channel_id: str) -> str:
        """Standard path for channel context memory file (threads nest under
        their parent: places are local, a thread is part of its parent place)."""
        parent = self._thread_parent(channel_id)
        if parent:
            return f"/memories/{self.bot_id}/servers/{server_id}/channels/{parent}/threads/{channel_id}.md"
        return f"/memories/{self.bot_id}/servers/{server_id}/channels/{channel_id}.md"

    def get_episodes_dir_path(self, server_id: str, channel_id: str) -> str:
        parent = self._thread_parent(channel_id)
        if parent:
            return f"/memories/{self.bot_id}/servers/{server_id}/channels/{parent}/threads/{channel_id}/episodes"
        return f"/memories/{self.bot_id}/servers/{server_id}/channels/{channel_id}/episodes"

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

    def resolve_path(self, path: str):
        """Filesystem path for a /memories/{bot_id}/... virtual path."""
        return self.base_path / path.replace(f"/memories/{self.bot_id}/", "")

    async def write_followups(self, server_id: str, data: dict):
        """
        System-level write for follow-up completion/cleanup.

        Used when the system completes follow-ups, not for Claude-initiated creation.
        Claude creates follow-ups via memory tool.
        """
        path = self.get_followups_path(server_id)
        file_path = self.resolve_path(path)

        file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Wrote followups to {path}")

        except Exception as e:
            logger.error(f"Error writing followups to {path}: {e}")

    def get_channel_stats_path(self, server_id: str, channel_id: str) -> str:
        """Standard path for channel engagement stats JSON"""
        parent = self._thread_parent(channel_id)
        if parent:
            return f"/memories/{self.bot_id}/servers/{server_id}/channels/{parent}/threads/{channel_id}_stats.json"
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

        # Laplace smoothing: stays near the 0.5 prior on few samples, so one
        # ignored message can't blacklist a channel forever (proactive never
        # firing again means the rate could never recover - a death spiral)
        success_rate = (successful + 1) / (total + 2)

        return {
            "success_rate": success_rate,
            "total_attempts": total,
            "successful_attempts": successful,
        }

    async def write_engagement_stats(self, server_id: str, channel_id: str, data: dict):
        """System-level write for engagement tracking"""
        path = self.get_channel_stats_path(server_id, channel_id)
        file_path = self.resolve_path(path)

        file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Wrote engagement stats to {path}")

        except Exception as e:
            logger.error(f"Error writing engagement stats to {path}: {e}")

    async def read(self, path: str) -> Optional[str]:
        """Read memory file, returning None if not found"""
        # Convert memory tool path to filesystem path
        file_path = self.resolve_path(path)

        if not file_path.exists():
            logger.debug(f"Memory file not found: {path}")
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            logger.debug(f"Read memory file: {path} ({len(content)} chars)")
            return content

        except Exception as e:
            logger.error(f"Error reading memory file {path}: {e}")
            return None

    async def write(self, path: str, content: str) -> None:
        """
        System-level write of a memory file (used by the episodizer).

        Claude writes via the memory tool; this is for framework-owned files.
        """
        file_path = self.resolve_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.debug(f"Wrote memory file {path}")

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
            context_parts.append("- User profiles (global, one per human):")
            for user_id in user_ids[:5]:
                context_parts.append(f"  - {self.get_global_user_profile_path(user_id)}")

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

    async def initialize_memory_structure(self, message_memory, user_cache, discord_guilds):
        """
        Initialize memory directory structure with skeleton files.

        Creates directory structure and basic markdown files for:
        - Server culture files
        - User profile files (with display names)
        - Channel context files (with channel names)

        Called on bot startup after backfill completes.
        """
        logger.info("Initializing memory structure...")

        # Get all unique server IDs from messages
        server_ids = await message_memory.get_active_servers()

        total_files_created = 0

        for server_id in server_ids:
            # Get guild object for server name
            guild = None
            for g in discord_guilds:
                if str(g.id) == server_id:
                    guild = g
                    break

            server_name = guild.name if guild else f"Server {server_id}"

            # Create server directory
            server_path = self.base_path / "servers" / server_id
            server_path.mkdir(parents=True, exist_ok=True)

            # Create server culture file if it doesn't exist
            culture_file = server_path / "culture.md"
            if not culture_file.exists():
                culture_content = f"# {server_name} Culture\n\n[WRITE ABOUT SERVER CULTURE HERE]\n"
                culture_file.write_text(culture_content, encoding="utf-8")
                total_files_created += 1
                logger.debug(f"Created culture file for {server_name}")

            # Get all users who have messaged in this server
            users = await message_memory.get_users_in_server(server_id)

            # Create global user profile skeletons
            users_path = self.get_global_users_dir()
            users_path.mkdir(parents=True, exist_ok=True)

            for user_id in users:
                user_file = users_path / f"{user_id}.md"
                known_entry = f"{server_name} ({server_id})"
                if user_file.exists():
                    content = user_file.read_text(encoding="utf-8")
                    lines = content.splitlines()
                    if len(lines) > 1 and lines[1].startswith("Known from:") and f"({server_id})" not in lines[1]:
                        lines[1] = lines[1] + f", {known_entry}"
                        user_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    continue
                user_data = await user_cache.get_user(user_id)
                display_name = user_data.display_name if user_data else "Unknown"
                username = user_data.username if user_data else user_id
                user_file.write_text(
                    f"# {display_name} ({username})\n"
                    f"Known from: {known_entry}\n\n"
                    f"## Profile\n",
                    encoding="utf-8",
                )
                total_files_created += 1

            logger.debug(f"Created {len(users)} user files for {server_name}")

        logger.info(f"Memory structure initialized: {total_files_created} new files created across {len(server_ids)} servers")
