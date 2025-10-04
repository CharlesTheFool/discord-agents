"""
Context Builder - Smart Context Assembly for Claude API

Builds rich context from Discord messages with:
- @mention name resolution
- Reply chain threading
- Recent message history
- Memory system integration
"""

import discord
import logging
import re
from datetime import datetime
from typing import Optional, List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import BotConfig
    from .message_memory import MessageMemory
    from .memory_manager import MemoryManager

logger = logging.getLogger(__name__)


class ContextBuilder:
    """
    Builds context for Claude API calls with enhanced features.

    Features:
    - Resolves @mentions to display names
    - Threads reply chains for better context
    - Includes recent message history
    - Integrates memory system paths
    """

    def __init__(
        self,
        config: "BotConfig",
        message_memory: "MessageMemory",
        memory_manager: "MemoryManager",
    ):
        """
        Initialize context builder.

        Args:
            config: Bot configuration
            message_memory: Message storage
            memory_manager: Memory tool manager
        """
        self.config = config
        self.message_memory = message_memory
        self.memory_manager = memory_manager

        logger.info(f"ContextBuilder initialized for bot '{config.bot_id}'")

    async def build_context(self, message: discord.Message) -> dict:
        """
        Build context for Claude API call.

        Args:
            message: Discord message to build context for

        Returns:
            Dictionary with system_prompt, messages, and stats
        """
        # Track stats for logging
        stats = {
            "mentions_resolved": 0,
            "reply_chain_length": 0,
            "recent_messages": 0,
            "reactions_found": 0
        }
        # Get bot's Discord display name
        bot_display_name = "Assistant"
        if message.guild and message.guild.me:
            bot_display_name = message.guild.me.display_name

        # Get current time
        current_time = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

        # Get system prompt
        base_prompt = (
            self.config.personality.base_prompt
            if self.config.personality
            else "You are a helpful Discord bot assistant."
        )

        # Add identity, current time, and clarification about conversation history
        system_prompt = f"""You are {bot_display_name}.
Current time: {current_time}

{base_prompt}

IMPORTANT: In the conversation history below, messages marked "Assistant (you)" are YOUR OWN previous responses. Do not refer to them as if someone else said them. These are what you already said earlier in this conversation."""

        # Get recent messages
        channel_id = str(message.channel.id)
        recent_messages = await self.message_memory.get_recent(
            channel_id, limit=self.config.reactive.context_window
        )

        # Build messages array for Claude
        messages = []

        # Add recent message history as context
        if recent_messages:
            stats["recent_messages"] = len(recent_messages)
            history_parts = []
            history_parts.append("# Recent Conversation History")
            history_parts.append("")

            # Check if current message is a reply
            reply_chain = await self._get_reply_chain(message)
            if reply_chain:
                stats["reply_chain_length"] = len(reply_chain)
                history_parts.append("## Reply Chain (Oldest to Newest)")
                history_parts.append("")
                for msg in reply_chain:
                    resolved_content, resolved_count = await self._resolve_mentions(msg.content, message.guild)
                    stats["mentions_resolved"] += resolved_count
                    author_display = "Assistant (you)" if msg.author.bot else msg.author.display_name
                    timestamp_str = msg.created_at.strftime('%H:%M')
                    history_parts.append(f"[{timestamp_str}] **{author_display}**: {resolved_content}")
                history_parts.append("")
                history_parts.append("## Recent Messages")
                history_parts.append("")

            # Add recent messages
            for msg in recent_messages:
                # Clarify bot's own messages vs user messages
                if msg.is_bot:
                    author_display = "Assistant (you)"
                else:
                    author_display = msg.author_name

                # Resolve mentions in content
                resolved_content, resolved_count = await self._resolve_mentions(msg.content, message.guild)
                stats["mentions_resolved"] += resolved_count

                # Format timestamp from database (datetime object)
                timestamp_str = msg.timestamp.strftime('%H:%M')
                history_parts.append(f"[{timestamp_str}] **{author_display}**: {resolved_content}")

            history_parts.append("")

            # Add current message with resolved mentions and full context
            resolved_content, resolved_count = await self._resolve_mentions(message.content, message.guild)
            stats["mentions_resolved"] += resolved_count

            message_with_context, has_reactions = self._format_message_with_context(
                author=message.author.display_name,
                content=resolved_content,
                message=message
            )
            if has_reactions:
                stats["reactions_found"] += 1
            history_parts.append(message_with_context)

            # Add memory context paths
            if message.guild:
                server_id = str(message.guild.id)
                user_ids = [str(message.author.id)]

                # Add unique user IDs from recent messages
                for msg in recent_messages[-5:]:  # Last 5 messages
                    if msg.author_id not in user_ids:
                        user_ids.append(msg.author_id)

                memory_context = self.memory_manager.build_memory_context(
                    server_id, channel_id, user_ids
                )
                history_parts.append("")
                history_parts.append(memory_context)

            messages.append(
                {"role": "user", "content": "\n".join(history_parts)}
            )

        else:
            # No history, just current message with full context
            resolved_content, resolved_count = await self._resolve_mentions(message.content, message.guild)
            stats["mentions_resolved"] += resolved_count

            message_with_context, has_reactions = self._format_message_with_context(
                author=message.author.display_name,
                content=resolved_content,
                message=message
            )
            if has_reactions:
                stats["reactions_found"] += 1

            messages.append(
                {"role": "user", "content": message_with_context}
            )

        return {"system_prompt": system_prompt, "messages": messages, "stats": stats}

    async def _resolve_mentions(self, content: str, guild: Optional[discord.Guild]) -> tuple[str, int]:
        """
        Resolve @mentions in content to display names.

        Args:
            content: Message content with potential @mentions
            guild: Discord guild (server) for member lookup

        Returns:
            Tuple of (content with resolved mentions, count of mentions resolved)
        """
        if not guild:
            return content, 0

        # Pattern for user mentions: <@123456789> or <@!123456789>
        mention_pattern = re.compile(r'<@!?(\d+)>')
        mentions_found = mention_pattern.findall(content)

        if not mentions_found:
            return content, 0

        resolved_count = 0

        async def replace_mention(match):
            nonlocal resolved_count
            user_id = int(match.group(1))
            try:
                member = await guild.fetch_member(user_id)
                resolved_count += 1
                return f"@{member.display_name}"
            except (discord.NotFound, discord.HTTPException):
                # Keep original mention if user not found
                return match.group(0)

        # Replace all mentions
        resolved = content
        for match in mention_pattern.finditer(content):
            replacement = await replace_mention(match)
            resolved = resolved.replace(match.group(0), replacement, 1)

        return resolved, resolved_count

    async def _get_reply_chain(self, message: discord.Message) -> List[discord.Message]:
        """
        Follow reply chain backwards to build context.

        Args:
            message: Message that may be a reply

        Returns:
            List of messages in reply chain (oldest first), empty if not a reply
        """
        if not message.reference:
            return []

        chain = []
        current = message

        # Follow chain backwards (max 5 messages to prevent excessive context)
        for _ in range(5):
            if not current.reference:
                break

            try:
                # Fetch the message being replied to
                replied_to = await current.channel.fetch_message(current.reference.message_id)
                chain.append(replied_to)
                current = replied_to
            except (discord.NotFound, discord.HTTPException) as e:
                logger.debug(f"Could not fetch reply chain message: {e}")
                break

        # Reverse to get oldest-first order
        chain.reverse()

        logger.debug(f"Built reply chain with {len(chain)} messages")
        return chain

    def _format_message_with_context(
        self, author: str, content: str, message: discord.Message
    ) -> tuple[str, bool]:
        """
        Format message with additional context (timestamp, reactions, reply info).

        Args:
            author: Author display name
            content: Message content (already resolved)
            message: Discord message object

        Returns:
            Tuple of (formatted message string, has_reactions boolean)
        """
        # Add timestamp to message
        timestamp_str = message.created_at.strftime('%H:%M')
        parts = [f"[{timestamp_str}] **{author}**: {content}"]
        has_reactions = False

        # Add reaction information
        if message.reactions:
            reaction_strs = []
            for reaction in message.reactions:
                emoji_str = str(reaction.emoji)
                count = reaction.count
                reaction_strs.append(f"{emoji_str}×{count}")

            if reaction_strs:
                has_reactions = True
                parts.append(f"  *(Reactions: {', '.join(reaction_strs)})*")

        # Add reply information
        if message.reference and message.reference.resolved:
            replied_msg = message.reference.resolved
            if isinstance(replied_msg, discord.Message):
                replied_author = "Assistant (you)" if replied_msg.author.bot else replied_msg.author.display_name
                # Truncate replied content if too long
                replied_content = replied_msg.content[:100]
                if len(replied_msg.content) > 100:
                    replied_content += "..."
                parts.append(f"  *(Replying to {replied_author}: \"{replied_content}\")*")

        return "\n".join(parts), has_reactions
