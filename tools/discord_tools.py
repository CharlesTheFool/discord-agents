"""
Discord Tools - Message Search and User/Channel Info

Provides Claude with tools to:
- Search message history with FTS5 full-text search
- Look up user information
- Get channel metadata
"""

import logging
from typing import List, Dict, Optional, TYPE_CHECKING
from datetime import datetime, timedelta

if TYPE_CHECKING:
    from core.message_memory import MessageMemory
    from core.user_cache import UserCache

logger = logging.getLogger(__name__)


class DiscordToolExecutor:
    """
    Executes Discord tool commands for Claude API.

    Provides:
    - Message history search with FTS5
    - User info lookup
    - Channel metadata
    """

    def __init__(self, message_memory: "MessageMemory", user_cache: "UserCache"):
        """
        Initialize Discord tool executor.

        Args:
            message_memory: Message storage with FTS5 search
            user_cache: User data cache
        """
        self.message_memory = message_memory
        self.user_cache = user_cache
        logger.info("DiscordToolExecutor initialized")

    async def execute(self, tool_input: dict) -> str:
        """
        Execute Discord tool command.

        Args:
            tool_input: Tool parameters from Claude API

        Returns:
            Execution result as string
        """
        command = tool_input.get("command", "")

        if command == "search_messages":
            return await self._search_messages(tool_input)
        elif command == "get_user_info":
            return await self._get_user_info(tool_input)
        elif command == "get_channel_info":
            return await self._get_channel_info(tool_input)
        else:
            return f"Unknown Discord tool command: {command}"

    async def _search_messages(self, params: dict) -> str:
        """
        Search message history using FTS5 full-text search.

        Args:
            params: {
                "query": str - search query (FTS5 syntax supported),
                "channel_id": str (optional) - limit to specific channel,
                "author_id": str (optional) - limit to specific author,
                "limit": int (optional) - max results (default 20)
            }

        Returns:
            Search results formatted as string
        """
        query = params.get("query", "")
        channel_id = params.get("channel_id")
        author_id = params.get("author_id")
        limit = params.get("limit", 20)

        if not query:
            return "Error: query parameter required"

        try:
            results = await self.message_memory.search_messages(
                query=query,
                channel_id=channel_id,
                author_id=author_id,
                limit=limit
            )

            if not results:
                return f"No messages found matching: {query}"

            # Format results
            lines = [f"Found {len(results)} message(s) matching '{query}':\n"]
            for msg in results:
                timestamp = msg.timestamp.strftime('%Y-%m-%d %H:%M')
                lines.append(f"[{timestamp}] {msg.author_name}: {msg.content}")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error searching messages: {e}", exc_info=True)
            return f"Error searching messages: {str(e)}"

    async def _get_user_info(self, params: dict) -> str:
        """
        Get user information from cache.

        Args:
            params: {
                "user_id": str - Discord user ID
            }

        Returns:
            User info formatted as string
        """
        user_id = params.get("user_id", "")
        if not user_id:
            return "Error: user_id parameter required"

        try:
            user_info = await self.user_cache.get_user(user_id)

            if not user_info:
                return f"User {user_id} not found in cache"

            lines = [
                f"User: {user_info.username} (ID: {user_info.user_id})",
                f"Display Name: {user_info.display_name}",
                f"Bot: {'Yes' if user_info.is_bot else 'No'}",
                f"Last Seen: {user_info.last_seen.strftime('%Y-%m-%d %H:%M')}",
                f"Message Count: {user_info.message_count}",
            ]

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error getting user info: {e}", exc_info=True)
            return f"Error getting user info: {str(e)}"

    async def _get_channel_info(self, params: dict) -> str:
        """
        Get channel metadata.

        Args:
            params: {
                "channel_id": str - Discord channel ID
            }

        Returns:
            Channel info formatted as string
        """
        channel_id = params.get("channel_id", "")
        if not channel_id:
            return "Error: channel_id parameter required"

        try:
            stats = await self.message_memory.get_channel_stats(channel_id)

            if stats["total_messages"] == 0:
                return f"No data found for channel {channel_id}"

            lines = [
                f"Channel ID: {channel_id}",
                f"Total Messages: {stats['total_messages']}",
                f"Unique Users: {stats['unique_users']}",
            ]

            if stats['first_message']:
                first = datetime.fromisoformat(stats['first_message'])
                lines.append(f"First Message: {first.strftime('%Y-%m-%d %H:%M')}")

            if stats['last_message']:
                last = datetime.fromisoformat(stats['last_message'])
                lines.append(f"Last Message: {last.strftime('%Y-%m-%d %H:%M')}")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error getting channel info: {e}", exc_info=True)
            return f"Error getting channel info: {str(e)}"


def get_discord_tools() -> list:
    """
    Get Discord tool definitions for Claude API.

    Returns:
        List of tool definitions
    """
    return [
        {
            "name": "discord_tools",
            "description": "Search Discord message history, get user/channel info. Use search_messages for full-text search, get_user_info for user details, get_channel_info for channel stats.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": ["search_messages", "get_user_info", "get_channel_info"],
                        "description": "Discord tool command to execute"
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query for search_messages (supports FTS5 syntax: 'AND', 'OR', 'NOT', phrase matching with quotes)"
                    },
                    "channel_id": {
                        "type": "string",
                        "description": "Discord channel ID (for search_messages or get_channel_info)"
                    },
                    "user_id": {
                        "type": "string",
                        "description": "Discord user ID (for search_messages or get_user_info)"
                    },
                    "author_id": {
                        "type": "string",
                        "description": "Filter search by author ID (for search_messages)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results to return (for search_messages, default 20, max recommended 50)"
                    }
                },
                "required": ["command"]
            }
        }
    ]
