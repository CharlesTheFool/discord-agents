"""
Discord Tools - Message Search and User/Channel Info

Provides Claude with tools to:
- Search message history with FTS5 full-text search
- Look up user information
- Get channel metadata
"""

import logging
from typing import List, Dict, Optional, TYPE_CHECKING

from core.internal_constants import format_size
from datetime import datetime, timedelta

if TYPE_CHECKING:
    from core.message_memory import MessageMemory
    from core.user_cache import UserCache
    from core.vaults import VaultEnforcer
    from core.conversation_state_manager import ConversationStateManager

logger = logging.getLogger(__name__)


class DiscordToolExecutor:
    """
    Executes Discord tool commands for Claude API.
    Routes commands to appropriate handlers.
    """

    def __init__(
        self,
        message_memory: "MessageMemory",
        user_cache: "UserCache",
        vaults: Optional["VaultEnforcer"] = None,
        attachment_manager: Optional["UnifiedAttachmentManager"] = None,
        conversation_state_manager: Optional["ConversationStateManager"] = None,
        discord_client=None,
    ):
        self.message_memory = message_memory
        self.user_cache = user_cache
        self.vaults = vaults
        self.attachment_manager = attachment_manager
        self.conversation_state_manager = conversation_state_manager
        self.discord_client = discord_client
        logger.info("DiscordToolExecutor initialized")

    async def execute(
        self,
        tool_input: dict,
        current_server_id: Optional[str] = None,
        current_channel_id: Optional[str] = None
    ) -> str:
        """
        Route tool command to appropriate handler.

        Args:
            tool_input: Tool parameters
            current_server_id: Current server context for isolation
            current_channel_id: Current channel context for isolation
        """
        command = tool_input.get("command", "")

        if command == "search_messages":
            return await self._search_messages(tool_input, current_server_id, current_channel_id)
        elif command == "view_messages":
            return await self._view_messages(tool_input, current_server_id, current_channel_id)
        elif command == "get_user_info":
            return await self._get_user_info(tool_input)
        elif command == "get_channel_info":
            return await self._get_channel_info(tool_input)
        elif command == "get_attachment":
            return await self._get_attachment(tool_input, current_server_id, current_channel_id)
        elif command == "list_attachments":
            return await self._list_attachments(tool_input, current_server_id, current_channel_id)
        else:
            return f"Unknown Discord tool command: {command}"

    def _server_label(self, guild_id: str) -> str:
        client = self.discord_client
        if client:
            guild = client.get_guild(int(guild_id)) if str(guild_id).isdigit() else None
            if guild:
                return guild.name
        return f"server {guild_id}"

    async def _search_messages(self, params: dict, current_server_id, current_channel_id) -> str:
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
            scope = params.get("scope") or "server"
            guild_id = None
            if scope == "channel":
                channel_id = current_channel_id or channel_id
            elif scope == "server" and current_server_id:
                guild_id = current_server_id
            # scope == "global" (or DM context with no server): no constraint beyond vaults

            exclude_ids = self.vaults.excluded_ids(current_server_id, current_channel_id) if self.vaults else []
            results = await self.message_memory.search_messages(
                query=query,
                channel_id=channel_id,
                author_id=author_id,
                guild_id=guild_id,
                limit=limit,
                exclude_ids=exclude_ids or None,
            )

            if not results:
                return f"No messages found matching: {query}"

            lines = [f"Found {len(results)} message(s) matching '{query}':\n"]

            for msg in results:
                timestamp = msg.timestamp.strftime('%Y-%m-%d %H:%M')
                message_line = f"[{timestamp}] {msg.author_name} (msg_id: {msg.message_id}, channel: {msg.channel_id}): {msg.content}"

                # Query attachments for this message
                if self.attachment_manager:
                    try:
                        async with self.attachment_manager.attachment_db.db.execute(
                            "SELECT attachment_id, filename FROM attachments WHERE message_id = ?",
                            (str(msg.message_id),)
                        ) as cursor:
                            attachments = await cursor.fetchall()

                        if attachments:
                            attachment_strs = [f"{row['filename']} (ID: {row['attachment_id']})" for row in attachments]
                            message_line += f" [Attachments: {', '.join(attachment_strs)}]"
                    except Exception as e:
                        logger.debug(f"Failed to query attachments for message {msg.message_id}: {e}")

                if current_server_id and msg.guild_id and msg.guild_id != current_server_id:
                    message_line = f"[from {self._server_label(msg.guild_id)}] " + message_line

                lines.append(message_line)

            lines.append("\nTip: Use view_messages with mode='around' and the message_id to see conversation context.")
            lines.append("Tip: Use get_attachment with attachment_id to retrieve and process specific attachments.")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error searching messages: {e}", exc_info=True)
            return f"Error searching messages: {str(e)}"

    async def _view_messages(self, params: dict, current_server_id, current_channel_id) -> str:
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

        if self.vaults and self.vaults.active:
            owner = await self.message_memory.get_server_for_channel(channel_id)
            if self.vaults.blocks_content(owner, channel_id,
                                          current_server_id, current_channel_id):
                return "Error: Access denied - that channel is vaulted; its messages stay inside it."

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
                message_line = f"[{timestamp}] {msg.author_name}: {msg.content}"

                # Query attachments for this message
                if self.attachment_manager:
                    try:
                        async with self.attachment_manager.attachment_db.db.execute(
                            "SELECT attachment_id, filename FROM attachments WHERE message_id = ?",
                            (str(msg.message_id),)
                        ) as cursor:
                            attachments = await cursor.fetchall()

                        if attachments:
                            attachment_strs = [f"{row['filename']} (ID: {row['attachment_id']})" for row in attachments]
                            message_line += f" [Attachments: {', '.join(attachment_strs)}]"
                    except Exception as e:
                        logger.debug(f"Failed to query attachments for message {msg.message_id}: {e}")

                lines.append(message_line)

            # Add helpful tips
            if any("[Attachments:" in line for line in lines):
                lines.append("\nTip: Use get_attachment with attachment_id to retrieve and process specific attachments.")

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

    async def _get_attachment(self, params: dict, current_server_id=None, current_channel_id=None):
        """
        Retrieve and process a specific attachment by ID.

        Returns structured data with both text description and file content
        for integration into conversation context.

        Returns:
            Dict with:
            - text: Human-readable description
            - file_data: File content for API (optional)
            - metadata: Source information
            OR string with error message
        """
        attachment_id = params.get("attachment_id", "")
        if not attachment_id:
            return "Error: attachment_id parameter required"

        if not self.attachment_manager:
            return "Error: Attachment processing not available (attachment_manager not initialized)"

        try:
            # Query database for full metadata
            async with self.attachment_manager.attachment_db.db.execute(
                "SELECT filename, attachment_type, size_bytes, message_id, channel_id, server_id, uploaded_at, local_path FROM attachments WHERE attachment_id = ?",
                (attachment_id,)
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                return f"Error: Attachment {attachment_id} not found in database"

            if self.vaults and self.vaults.blocks_content(
                    row["server_id"], row["channel_id"],
                    current_server_id, current_channel_id):
                return "Error: That attachment lives in a vaulted space - it can only be opened from inside it."

            filename = row["filename"]
            attachment_type = row["attachment_type"]
            size_bytes = row["size_bytes"]
            source_message_id = row["message_id"]
            source_channel_id = row["channel_id"]
            server_id = row["server_id"]
            uploaded_at = row["uploaded_at"]

            size_str = format_size(size_bytes)

            # Retrieve attachment from storage
            attachment_data = await self.attachment_manager.get_attachment_for_processing(attachment_id)

            if not attachment_data:
                return f"Error: Attachment {attachment_id} not found or no longer available in storage"

            # Build text description with provenance
            if source_channel_id == "repository":
                lines = [
                    f"✓ Retrieved: {filename} ({size_str})",
                    f"📍 Source: bot repository — {row['local_path']}",
                    f"📎 Type: {attachment_type}"
                ]
            else:
                lines = [
                    f"✓ Retrieved: {filename} ({size_str})",
                    f"📍 Source: Message {source_message_id} in channel {source_channel_id}",
                    f"📅 Uploaded: {uploaded_at}",
                    f"📎 Type: {attachment_type}"
                ]

            text_description = "\n".join(lines)

            # Return structured data for reactive engine integration
            return {
                "_structured": True,  # Marker for reactive engine
                "text": text_description,
                "file_data": attachment_data,
                "metadata": {
                    "attachment_id": attachment_id,
                    "filename": filename,
                    "attachment_type": attachment_type,
                    "size_bytes": size_bytes,
                    "source_message_id": source_message_id,
                    "source_channel_id": source_channel_id,
                    "server_id": server_id
                }
            }

        except Exception as e:
            logger.error(f"Error retrieving attachment: {e}", exc_info=True)
            return f"Error retrieving attachment: {str(e)}"

    async def _list_attachments(self, params: dict, current_server_id=None, current_channel_id=None) -> str:
        """
        Phase 6.2: List all attachments with optional filtering.

        Design: Discovery layer for rolled-out files. Shows all attachments stored
        in database, with optional filters for keyword, file_type, and in_context_only.

        Returns formatted list with filename, size, type, and message_id for context
        via view_messages tool.
        """
        if not self.attachment_manager:
            return "Error: Attachment processing not available (attachment_manager not initialized)"

        # Extract filter parameters
        scope = params.get("scope") or "server"
        keyword = params.get("keyword", "").lower()
        file_type = params.get("file_type", "").lower()
        in_context_only = params.get("in_context_only", False)
        channel_id = params.get("channel_id")

        try:
            # Build SQL query with filters
            query = "SELECT attachment_id, filename, attachment_type, size_bytes, message_id, channel_id, server_id FROM attachments WHERE 1=1"
            query_params = []

            # Scope: server (default) constrains to current server; global removes constraint
            if scope == "server" and current_server_id:
                query += " AND (server_id = ? OR channel_id = ?)"
                query_params.extend([current_server_id, current_channel_id or ""])

            if channel_id:
                query += " AND channel_id = ?"
                query_params.append(channel_id)

            if keyword:
                query += " AND (LOWER(filename) LIKE ? OR LOWER(attachment_type) LIKE ?)"
                query_params.extend([f"%{keyword}%", f"%{keyword}%"])

            if file_type:
                query += " AND LOWER(filename) LIKE ?"
                query_params.append(f"%.{file_type}")

            exclude_ids = self.vaults.excluded_ids(current_server_id, current_channel_id) if self.vaults else []
            if exclude_ids:
                ph = ",".join("?" for _ in exclude_ids)
                query += f" AND channel_id NOT IN ({ph}) AND (server_id IS NULL OR server_id NOT IN ({ph}))"
                query_params.extend(exclude_ids)
                query_params.extend(exclude_ids)

            # Global scope: group by server_id for labeled output
            if scope == "global":
                query += " ORDER BY server_id, uploaded_at DESC"
            else:
                query += " ORDER BY uploaded_at DESC"

            # Execute query
            async with self.attachment_manager.attachment_db.db.execute(query, query_params) as cursor:
                rows = await cursor.fetchall()

            if not rows:
                return "No attachments found matching the specified filters."

            # Phase 6.2: Filter by in_context_only if requested
            # Design: If in_context_only is True, only show files that are currently in conversation state
            if in_context_only and self.conversation_state_manager and channel_id:
                # Get conversation state for this channel
                state = await self.conversation_state_manager.get_or_create(channel_id)

                # Get set of attachment_ids currently in context (from message annotations)
                in_context_ids = set()
                for msg in state.messages:
                    in_context_ids.update(msg.get("attachment_ids", []))

                # Filter rows to only those in context
                rows = [row for row in rows if row["attachment_id"] in in_context_ids]

                if not rows:
                    return "No attachments currently in conversation context. Use list_attachments without in_context_only to see all uploaded files."

            # Format results
            lines = [f"Found {len(rows)} attachment(s):\n"]

            current_group_server = None
            for row in rows:
                attachment_id = row["attachment_id"]
                filename = row["filename"]
                attachment_type = row["attachment_type"]
                size_bytes = row["size_bytes"]
                message_id = row["message_id"]
                row_server = row["server_id"]

                size_str = format_size(size_bytes)

                # Global scope: emit server group header on server_id change
                if scope == "global" and row_server != current_group_server:
                    current_group_server = row_server
                    lines.append(f"\n[server {self._server_label(row_server) if row_server else 'unknown'}]")

                # Format line with provenance for context lookup
                if row["channel_id"] == "repository":
                    line = f"- {filename} ({size_str}, type: {attachment_type}, ID: {attachment_id}) [repository]"
                else:
                    line = f"- {filename} ({size_str}, type: {attachment_type}, ID: {attachment_id}, from message: {message_id})"
                lines.append(line)

            # Add tips about re-accessing and viewing context
            lines.append("\n📎 To retrieve and process any of these files, use: get_attachment with the attachment_id")
            lines.append("🔍 To see the message context, use: view_messages with mode='around' and the message_id")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error listing attachments: {e}", exc_info=True)
            return f"Error listing attachments: {str(e)}"


def get_discord_tools() -> list:
    """
    Generate Discord tool definitions for Claude API.

    Designed for agentic exploration: search for keywords, then view context around results.
    """
    return [
        {
            "name": "discord_tools",
            "description": "Agentic Discord exploration: search_messages for keyword discovery (returns message IDs), view_messages for flexible browsing (recent/around/first/range modes), list_attachments for file discovery (shows all uploaded files with filters), get_attachment to retrieve specific files, get_user_info for user details, get_channel_info for stats. WORKFLOW: Use search to find keywords → use view with mode='around' and message_id to explore context. Use list_attachments to discover files → use get_attachment to retrieve them. search_messages and list_attachments accept scope; search defaults to this server - pass scope='global' to reach everything you're in.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": ["search_messages", "view_messages", "get_user_info", "get_channel_info", "get_attachment", "list_attachments"],
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
                    },
                    "attachment_id": {
                        "type": "string",
                        "description": "[get_attachment] Attachment ID from search/view results to retrieve and process"
                    },
                    "keyword": {
                        "type": "string",
                        "description": "[list_attachments] Filter by filename or attachment type (case-insensitive substring match)"
                    },
                    "file_type": {
                        "type": "string",
                        "description": "[list_attachments] Filter by file extension (e.g., 'pdf', 'docx', 'xlsx', 'png')"
                    },
                    "in_context_only": {
                        "type": "boolean",
                        "description": "[list_attachments] Show only attachments currently in conversation state (not rolled out). Default: false (show all attachments)"
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["channel", "server", "global"],
                        "description": "[search_messages] reach: channel | server (default) | global. [list_attachments] server (default) | global."
                    }
                },
                "required": ["command"]
            }
        }
    ]
