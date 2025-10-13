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
        elif command == "view_messages":
            return await self._view_messages(tool_input)
        elif command == "get_user_info":
            return await self._get_user_info(tool_input)
        elif command == "get_channel_info":
            return await self._get_channel_info(tool_input)
        else:
            return f"Unknown Discord tool command: {command}"

    async def _search_messages(self, params: dict) -> str:
        """
        Search message history using FTS5 full-text search (pure discovery).

        Returns only matching messages with their IDs for follow-up exploration.
        Use view_messages to get context around search results.

        Args:
            params: {
                "query": str - search query (FTS5 syntax supported),
                "channel_id": str (optional) - limit to specific channel,
                "author_id": str (optional) - limit to specific author,
                "limit": int (optional) - max results (default 20)
            }

        Returns:
            Search results formatted as string (message_id, timestamp, author, content)
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

            # Format results - lightweight discovery format
            lines = [f"Found {len(results)} message(s) matching '{query}':\n"]

            for msg in results:
                timestamp = msg.timestamp.strftime('%Y-%m-%d %H:%M')
                lines.append(
                    f"[{timestamp}] {msg.author_name} (msg_id: {msg.message_id}, channel: {msg.channel_id}): {msg.content}"
                )

            lines.append("\nTip: Use view_messages with mode='around' and the message_id to see conversation context.")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error searching messages: {e}", exc_info=True)
            return f"Error searching messages: {str(e)}"

    async def _view_messages(self, params: dict) -> str:
        """
        View messages with flexible exploration modes (agentic browsing).

        Four modes:
        - "recent": View latest N messages (for "what's being discussed?")
        - "around": View messages surrounding a message_id (for context after search)
        - "first": View oldest N messages (for channel history/purpose)
        - "range": View messages in timestamp range (for temporal exploration)

        Args:
            params: {
                "mode": str - viewing mode ("recent", "around", "first", "range"),
                "channel_id": str - Discord channel ID (required),
                "limit": int (optional) - max messages (default 30, max 100),
                # Mode-specific params:
                "message_id": str - for "around" mode,
                "before": int - messages before target (default 5, for "around"),
                "after": int - messages after target (default 5, for "around"),
                "start_time": str - ISO timestamp (for "range"),
                "end_time": str - ISO timestamp (for "range")
            }

        Returns:
            Formatted messages with timestamps and authors
        """
        mode = params.get("mode", "recent")
        channel_id = params.get("channel_id")
        limit = min(params.get("limit", 30), 100)  # Max 100 messages

        if not channel_id:
            return "Error: channel_id parameter required"

        try:
            messages = []

            # Mode 1: Recent messages (current conversation)
            if mode == "recent":
                messages = await self.message_memory.get_recent(
                    channel_id=channel_id,
                    limit=limit
                )
                header = f"Recent {len(messages)} message(s) from channel {channel_id}:\n"

            # Mode 2: Around a specific message (context after search)
            elif mode == "around":
                message_id = params.get("message_id")
                if not message_id:
                    return "Error: message_id required for 'around' mode"

                before_count = params.get("before", 5)
                after_count = params.get("after", 5)

                context = await self.message_memory.get_message_context(
                    message_id=message_id,
                    channel_id=channel_id,
                    before=before_count,
                    after=after_count
                )

                if not context["match"]:
                    return f"Error: Message {message_id} not found in channel {channel_id}"

                # Combine before + match + after
                messages = context["before"] + [context["match"]] + context["after"]
                header = f"Messages around {message_id} (±{before_count}/{after_count}):\n"

            # Mode 3: First messages (channel history)
            elif mode == "first":
                messages = await self.message_memory.get_first_messages(
                    channel_id=channel_id,
                    limit=limit
                )
                header = f"First {len(messages)} message(s) from channel {channel_id}:\n"

            # Mode 4: Range (timestamp window)
            elif mode == "range":
                start_time_str = params.get("start_time")
                end_time_str = params.get("end_time")

                if not start_time_str:
                    return "Error: start_time required for 'range' mode (ISO format)"

                start_time = datetime.fromisoformat(start_time_str)
                messages = await self.message_memory.get_since(
                    channel_id=channel_id,
                    since=start_time
                )

                # Filter by end_time if provided
                if end_time_str:
                    end_time = datetime.fromisoformat(end_time_str)
                    messages = [msg for msg in messages if msg.timestamp <= end_time]

                # Apply limit
                messages = messages[:limit]
                header = f"Messages from {start_time_str} to {end_time_str or 'now'} ({len(messages)} found):\n"

            else:
                return f"Error: Unknown mode '{mode}'. Valid modes: recent, around, first, range"

            if not messages:
                return f"No messages found for mode '{mode}' in channel {channel_id}"

            # Format output
            lines = [header]

            for msg in messages:
                timestamp = msg.timestamp.strftime('%Y-%m-%d %H:%M')
                lines.append(f"[{timestamp}] {msg.author_name}: {msg.content}")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error viewing messages: {e}", exc_info=True)
            return f"Error viewing messages: {str(e)}"

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

            # Get actual total message count from message history (not just session count)
            total_messages = await self.message_memory.get_user_message_count(user_id)

            lines = [
                f"User: {user_info.username} (ID: {user_info.user_id})",
                f"Display Name: {user_info.display_name}",
                f"Bot: {'Yes' if user_info.is_bot else 'No'}",
                f"Last Seen: {user_info.last_seen.strftime('%Y-%m-%d %H:%M')}",
                f"Messages Tracked: {total_messages} (since bot started)",
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
            "description": "Agentic Discord exploration: search_messages for keyword discovery (returns message IDs), view_messages for flexible browsing (recent/around/first/range modes), get_user_info for user details, get_channel_info for stats. WORKFLOW: Use search to find keywords → use view with mode='around' and message_id to explore context. Or use view directly with mode='recent' to see current conversation.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": ["search_messages", "view_messages", "get_user_info", "get_channel_info"],
                        "description": "Discord tool command to execute"
                    },
                    "query": {
                        "type": "string",
                        "description": "[search_messages] Search query (supports FTS5: 'AND', 'OR', 'NOT', quotes for phrases)"
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["recent", "around", "first", "range"],
                        "description": "[view_messages] Viewing mode: 'recent' (latest messages), 'around' (context around message_id), 'first' (oldest messages), 'range' (timestamp window)"
                    },
                    "channel_id": {
                        "type": "string",
                        "description": "[search_messages/view_messages/get_channel_info] Discord channel ID. For search: omit to search ALL channels (recommended). For view: required."
                    },
                    "message_id": {
                        "type": "string",
                        "description": "[view_messages mode='around'] Target message ID to view context around"
                    },
                    "before": {
                        "type": "integer",
                        "description": "[view_messages mode='around'] Messages before target (default 5)"
                    },
                    "after": {
                        "type": "integer",
                        "description": "[view_messages mode='around'] Messages after target (default 5)"
                    },
                    "start_time": {
                        "type": "string",
                        "description": "[view_messages mode='range'] Start timestamp (ISO format: YYYY-MM-DDTHH:MM:SS)"
                    },
                    "end_time": {
                        "type": "string",
                        "description": "[view_messages mode='range'] End timestamp (ISO format, optional)"
                    },
                    "user_id": {
                        "type": "string",
                        "description": "[get_user_info] Discord user ID"
                    },
                    "author_id": {
                        "type": "string",
                        "description": "[search_messages] Filter search by author ID"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "[search_messages/view_messages] Max results (search: default 20, view: default 30, max 100)"
                    }
                },
                "required": ["command"]
            }
        }
    ]
