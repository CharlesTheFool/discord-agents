"""
Reactive Engine - Message Handling

Handles incoming messages and generates responses.
Phase 1: Basic @mention handling with Claude API.
"""

import discord
import asyncio
import logging
from anthropic import Anthropic, AsyncAnthropic
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import BotConfig
    from .rate_limiter import RateLimiter
    from .message_memory import MessageMemory
    from .memory_manager import MemoryManager

logger = logging.getLogger(__name__)


class ReactiveEngine:
    """
    Reactive message handling engine.

    Phase 1 capabilities:
    - Handle @mentions immediately
    - Build basic context (system prompt + recent messages)
    - Call Claude API with memory tool
    - Send response with typing indicator
    - Track engagement
    """

    def __init__(
        self,
        config: "BotConfig",
        rate_limiter: "RateLimiter",
        message_memory: "MessageMemory",
        memory_manager: "MemoryManager",
        anthropic_api_key: str,
    ):
        """
        Initialize reactive engine.

        Args:
            config: Bot configuration
            rate_limiter: Rate limiter instance
            message_memory: Message storage
            memory_manager: Memory tool manager
            anthropic_api_key: Anthropic API key
        """
        self.config = config
        self.rate_limiter = rate_limiter
        self.message_memory = message_memory
        self.memory_manager = memory_manager

        # Initialize Anthropic client
        self.anthropic = AsyncAnthropic(api_key=anthropic_api_key)

        logger.info(f"ReactiveEngine initialized for bot '{config.bot_id}'")

    async def handle_urgent(self, message: discord.Message):
        """
        Handle urgent message (@mention) immediately.

        Args:
            message: Discord message with @mention
        """
        channel_id = str(message.channel.id)

        # Check rate limits
        can_respond, reason = self.rate_limiter.can_respond(channel_id)

        if not can_respond:
            logger.info(f"Cannot respond to @mention: {reason}")
            # Still send a brief message to acknowledge
            await message.channel.send(
                f"{message.author.mention} I'm currently rate-limited. Please try again in a few minutes."
            )
            return

        # Build context
        context = await self._build_context(message)

        # Call Claude API
        logger.info(f"Calling Claude API for @mention from {message.author.name}")

        try:
            # Show typing indicator
            async with message.channel.typing():
                # Small delay for more natural feel
                await asyncio.sleep(1.5)

                # Call Claude API
                response = await self.anthropic.messages.create(
                    model=self.config.api.model,
                    max_tokens=self.config.api.max_tokens,
                    temperature=self.config.api.temperature,
                    betas=["context-management-2025-06-27"],  # Enable context editing
                    tools=[{"type": "memory"}],  # Enable memory tool
                    system=context["system_prompt"],
                    messages=context["messages"],
                )

            # Extract text response
            response_text = ""
            for block in response.content:
                if block.type == "text":
                    response_text += block.text

            if not response_text:
                response_text = "I'm not sure how to respond to that."

            # Send response
            sent_message = await message.channel.send(response_text)

            # Record response and start engagement tracking
            self.rate_limiter.record_response(channel_id)

            asyncio.create_task(
                self._track_engagement(
                    sent_message.id,
                    message.channel,
                    delay=self.config.rate_limiting.engagement_tracking_delay,
                )
            )

            logger.info(
                f"Response sent to {message.author.name} ({len(response_text)} chars)"
            )

        except Exception as e:
            logger.error(f"Error calling Claude API: {e}", exc_info=True)
            await message.channel.send(
                f"{message.author.mention} Sorry, I encountered an error processing your request."
            )

    async def _build_context(self, message: discord.Message) -> dict:
        """
        Build context for Claude API call.

        Phase 1: Simple context with system prompt and recent messages.

        Args:
            message: Discord message to build context for

        Returns:
            Dictionary with system_prompt and messages
        """
        # Get system prompt
        system_prompt = (
            self.config.personality.base_prompt
            if self.config.personality
            else "You are a helpful Discord bot assistant."
        )

        # Get recent messages
        channel_id = str(message.channel.id)
        recent_messages = await self.message_memory.get_recent(
            channel_id, limit=self.config.reactive.context_window
        )

        # Build messages array for Claude
        messages = []

        # Add recent message history as context
        if recent_messages:
            history_parts = []
            history_parts.append("# Recent Conversation History")
            history_parts.append("")

            for msg in recent_messages:
                author_display = f"{msg.author_name} {'(bot)' if msg.is_bot else ''}"
                history_parts.append(f"**{author_display}**: {msg.content}")

            history_parts.append("")
            history_parts.append(f"**{message.author.display_name}**: {message.content}")

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
            # No history, just current message
            messages.append(
                {"role": "user", "content": f"{message.author.display_name}: {message.content}"}
            )

        return {"system_prompt": system_prompt, "messages": messages}

    async def _track_engagement(
        self, message_id: int, channel: discord.TextChannel, delay: int
    ):
        """
        Track engagement on bot message.

        Waits for delay, then checks if message got reactions or replies.

        Args:
            message_id: Discord message ID to track
            channel: Channel where message was sent
            delay: Seconds to wait before checking
        """
        await asyncio.sleep(delay)

        channel_id = str(channel.id)

        try:
            # Fetch fresh message to see current state
            message = await channel.fetch_message(message_id)

        except discord.NotFound:
            # Message was deleted
            logger.debug(f"Message {message_id} was deleted")
            return

        except discord.HTTPException as e:
            logger.error(f"Error fetching message {message_id}: {e}")
            return

        # Check for reactions
        has_reactions = len(message.reactions) > 0

        # Check for replies
        has_replies = await self._check_for_replies(message, channel)

        # Record result
        engaged = has_reactions or has_replies

        if engaged:
            self.rate_limiter.record_engagement(channel_id)
            logger.debug(
                f"Message {message_id}: ENGAGED "
                f"({'reactions' if has_reactions else 'replies'})"
            )
        else:
            self.rate_limiter.record_ignored(channel_id)
            logger.debug(f"Message {message_id}: IGNORED")

    async def _check_for_replies(
        self, message: discord.Message, channel: discord.TextChannel
    ) -> bool:
        """
        Check if any recent messages reply to this message.

        Args:
            message: Message to check replies for
            channel: Channel to search

        Returns:
            True if any replies found
        """
        try:
            # Get messages after this one
            recent = []
            async for msg in channel.history(after=message.created_at, limit=10):
                recent.append(msg)

            # Check if any reference this message
            for msg in recent:
                if msg.reference and msg.reference.message_id == message.id:
                    return True

            return False

        except discord.HTTPException:
            return False
