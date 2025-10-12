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
import sys
import os

# Add tools directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

if TYPE_CHECKING:
    from .config import BotConfig
    from .message_memory import MessageMemory
    from .memory_manager import MemoryManager

from tools.image_processor import ImageProcessor

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
        self.image_processor = ImageProcessor()

        logger.info(f"ContextBuilder initialized for bot '{config.bot_id}'")

    async def build_context(self, message: discord.Message, exclude_message_ids: List[int] = None) -> dict:
        """
        Build context for Claude API call.

        Args:
            message: Discord message to build context for
            exclude_message_ids: Optional list of message IDs to exclude from context (for filtering in-flight messages)

        Returns:
            Dictionary with system_prompt, messages, and stats
        """
        # Track stats for logging
        stats = {
            "mentions_resolved": 0,
            "reply_chain_length": 0,
            "recent_messages": 0,
            "reactions_found": 0,
            "images_processed": 0
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

        # Check if followups are enabled
        followup_instructions = ""
        if self.config.agentic and self.config.agentic.followups.enabled:
            server_id = str(message.guild.id) if message.guild else None
            if server_id:
                followups_path = self.memory_manager.get_followups_path(server_id)
                # Get current user and channel IDs for the template
                current_user_id = str(message.author.id)
                current_user_name = message.author.display_name
                current_channel_id = str(message.channel.id)

                followup_instructions = f"""

# Follow-Up System

When people mention future events, use your judgment to decide if a follow-up would be helpful or engaging. Create follow-ups when checking in later would be natural and valuable.

To create a follow-up, use the memory tool to write to: {followups_path}

**Current Context:**
- Current user: {current_user_name} (ID: {current_user_id})
- Current channel ID: {current_channel_id}

Format (JSON):
{{
  "pending": [
    {{
      "id": "unique-id-{current_time.replace(' ', '-').replace(':', '')}",
      "user_id": "{current_user_id}",
      "user_name": "{current_user_name}",
      "channel_id": "{current_channel_id}",
      "event": "<brief description>",
      "context": "<relevant context>",
      "mentioned_date": "{current_time}",
      "follow_up_after": "<ISO 8601 datetime>",
      "priority": "low|medium|high"
    }}
  ],
  "completed": []
}}

NOTE: The user_id MUST be the numeric Discord user ID (like {current_user_id}), NOT the display name.

**When to create follow-ups:**

Use social intelligence to decide when a follow-up would be natural and valuable. Would a thoughtful friend check in about this later?

Good candidates:
- Personal events (appointments, interviews, exams, presentations)
- Group activities (game nights, watch parties, meetups)
- Anticipated releases or events multiple people care about
- Projects and deadlines
- Life changes (moves, trips, new jobs)

Skip follow-ups for:
- Vague mentions without clear timeframes
- Recurring/routine events
- Past events
- When user explicitly declines

**Timing:** Use judgment based on the event. The system checks periodically, so schedule follow-ups for when it would be natural to check in.
"""

        # Add identity, current time, and clarification about conversation history
        system_prompt = f"""You are {bot_display_name}.
Current time: {current_time}

{base_prompt}{followup_instructions}

IMPORTANT: In the conversation history below, messages marked "Assistant (you)" are YOUR OWN previous responses. Do not refer to them as if someone else said them. These are what you already said earlier in this conversation.

NOTE: Messages showing "[Forwarded message - content not accessible]" are forwards from other channels. You cannot see forwarded message content due to Discord API limitations.

CRITICAL: Do NOT narrate your thought process, explain your reasoning, or describe what you're about to do in your responses. Just respond naturally and directly. Your thinking is private - users only see your final response."""

        # Get recent messages (excluding current message to avoid duplication)
        channel_id = str(message.channel.id)
        all_recent = await self.message_memory.get_recent(
            channel_id,
            limit=self.config.reactive.context_window + 1,
            exclude_message_ids=exclude_message_ids
        )

        # Filter out current message (it was just saved to DB before this call)
        recent_messages = [msg for msg in all_recent if msg.message_id != str(message.id)]

        # Trim to configured limit after filtering
        recent_messages = recent_messages[:self.config.reactive.context_window]

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

            # Process images from current message
            images = await self.process_images(message)
            if images:
                stats["images_processed"] = len(images)
                # Content becomes array with text and images
                content_parts = [{"type": "text", "text": "\n".join(history_parts)}]
                content_parts.extend(images)
                messages.append(
                    {"role": "user", "content": content_parts}
                )
            else:
                # No images, use string content
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

            # Process images from current message
            images = await self.process_images(message)
            if images:
                stats["images_processed"] = len(images)
                # Content becomes array with text and images
                content_parts = [{"type": "text", "text": message_with_context}]
                content_parts.extend(images)
                messages.append(
                    {"role": "user", "content": content_parts}
                )
            else:
                # No images, use string content
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

    async def process_images(self, message: discord.Message) -> List[Dict]:
        """
        Process image attachments from message.

        Args:
            message: Discord message with potential attachments

        Returns:
            List of processed images in Claude API format
        """
        if not message.attachments:
            return []

        # Filter to only images
        image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
        image_attachments = [
            attachment for attachment in message.attachments
            if any(attachment.filename.lower().endswith(ext) for ext in image_extensions)
        ]

        if not image_attachments:
            return []

        # Limit to 5 images (Claude API limit)
        if len(image_attachments) > 5:
            logger.warning(f"Message has {len(image_attachments)} images, limiting to 5")
            image_attachments = image_attachments[:5]

        # Process each image
        processed_images = []
        for attachment in image_attachments:
            try:
                processed = await self.image_processor.process_attachment(attachment)
                if processed:
                    processed_images.append(processed)
                    logger.info(f"Processed image: {attachment.filename}")
            except Exception as e:
                logger.error(f"Failed to process image {attachment.filename}: {e}")

        return processed_images
