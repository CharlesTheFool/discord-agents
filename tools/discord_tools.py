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
    Routes commands to appropriate handlers.
    """

    def __init__(self, message_memory: "MessageMemory", user_cache: "UserCache"):
        self.message_memory = message_memory
        self.user_cache = user_cache
        logger.info("DiscordToolExecutor initialized")

    async def execute(self, tool_input: dict) -> str:
        """Route tool command to appropriate handler"""
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
        Search message history using FTS5 full-text search.

        Returns matching messages with IDs for follow-up exploration.
        Designed for keyword discovery, not context browsing.
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
        View messages with flexible exploration modes.

        Four modes for different browsing patterns:
        - "recent": Latest messages (current conversation)
        - "around": Context surrounding a message (post-search exploration)
        - "first": Oldest messages (channel history/purpose)
        - "range": Timestamp window (temporal exploration)
        """
        mode = params.get("mode", "recent")
        channel_id = params.get("channel_id")
        limit = min(params.get("limit", 30), 100)  # Cap at 100 to avoid overwhelming output

        if not channel_id:
            return "Error: channel_id parameter required"

        try:
            messages = []

            if mode == "recent":
                messages = await self.message_memory.get_recent(
                    channel_id=channel_id,
                    limit=limit
                )
                header = f"Recent {len(messages)} message(s) from channel {channel_id}:\n"

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

                messages = context["before"] + [context["match"]] + context["after"]
                header = f"Messages around {message_id} (±{before_count}/{after_count}):\n"

            elif mode == "first":
                messages = await self.message_memory.get_first_messages(
                    channel_id=channel_id,
                    limit=limit
                )
                header = f"First {len(messages)} message(s) from channel {channel_id}:\n"

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

                # Apply end_time filter if provided
                if end_time_str:
                    end_time = datetime.fromisoformat(end_time_str)
                    messages = [msg for msg in messages if msg.timestamp <= end_time]

                messages = messages[:limit]
                header = f"Messages from {start_time_str} to {end_time_str or 'now'} ({len(messages)} found):\n"

            else:
                return f"Error: Unknown mode '{mode}'. Valid modes: recent, around, first, range"

            if not messages:
                return f"No messages found for mode '{mode}' in channel {channel_id}"

            lines = [header]

            for msg in messages:
                timestamp = msg.timestamp.strftime('%Y-%m-%d %H:%M')
                lines.append(f"[{timestamp}] {msg.author_name}: {msg.content}")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error viewing messages: {e}", exc_info=True)
            return f"Error viewing messages: {str(e)}"

    async def _get_user_info(self, params: dict) -> str:
        """Get user information from cache"""
        user_id = params.get("user_id", "")
        if not user_id:
            return "Error: user_id parameter required"

        try:
            user_info = await self.user_cache.get_user(user_id)

            if not user_info:
                return f"User {user_id} not found in cache"

            # Query actual message count from history (not just session count)
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
        """Get channel metadata from message history"""
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
    Generate Discord tool definitions for Claude API.

    Designed for agentic exploration: search for keywords, then view context around results.
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
