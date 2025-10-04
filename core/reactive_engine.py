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
        conversation_logger: "ConversationLogger",
    ):
        """
        Initialize reactive engine.

        Args:
            config: Bot configuration
            rate_limiter: Rate limiter instance
            message_memory: Message storage
            memory_manager: Memory tool manager
            anthropic_api_key: Anthropic API key
            conversation_logger: Conversation logger
        """
        self.config = config
        self.rate_limiter = rate_limiter
        self.message_memory = message_memory
        self.memory_manager = memory_manager
        self.conversation_logger = conversation_logger

        # Initialize Anthropic client
        self.anthropic = AsyncAnthropic(api_key=anthropic_api_key)

        # Track background tasks for clean shutdown
        self._background_tasks = set()

        # Prevent concurrent responses (fixes multiple responses to single mention)
        self._response_semaphore = asyncio.Semaphore(1)

        logger.info(f"ReactiveEngine initialized for bot '{config.bot_id}'")

    async def handle_urgent(self, message: discord.Message):
        """
        Handle urgent message (@mention) immediately.

        Args:
            message: Discord message with @mention
        """
        channel_id = str(message.channel.id)

        # Log incoming message
        self.conversation_logger.log_user_message(
            author=message.author.display_name,
            channel=message.channel.name,
            content=message.content,
            is_mention=True
        )

        # Check rate limits
        can_respond, reason = self.rate_limiter.can_respond(channel_id)
        rate_limit_stats = self.rate_limiter.get_stats(channel_id)

        # Log decision
        self.conversation_logger.log_decision(
            should_respond=can_respond,
            reason="mention detected" if can_respond else reason,
            rate_limit_stats=rate_limit_stats
        )

        if not can_respond:
            logger.info(f"Cannot respond to @mention: {reason}")
            # Still send a brief message to acknowledge
            await message.channel.send(
                f"{message.author.mention} I'm currently rate-limited. Please try again in a few minutes."
            )
            self.conversation_logger.log_separator()
            return

        # Build context
        context = await self._build_context(message)

        # Call Claude API
        logger.info(f"Calling Claude API for @mention from {message.author.name}")

        try:
            # Acquire semaphore to prevent concurrent API calls
            async with self._response_semaphore:
                # Show typing indicator
                async with message.channel.typing():
                    # Small delay for more natural feel
                    await asyncio.sleep(1.5)

                    # Build API request parameters
                    api_params = {
                        "model": self.config.api.model,
                        "max_tokens": self.config.api.max_tokens,
                        "system": context["system_prompt"],
                        "messages": context["messages"]
                    }

                    # Add extended thinking if enabled
                    if self.config.api.extended_thinking.enabled:
                        api_params["thinking"] = {
                            "type": "enabled",
                            "budget_tokens": self.config.api.extended_thinking.budget_tokens
                        }

                    # Call Claude API
                    response = await self.anthropic.messages.create(**api_params)

                # Extract thinking and text response
                thinking_text = ""
                response_text = ""

                for block in response.content:
                    if block.type == "thinking":
                        thinking_text = block.thinking
                    elif block.type == "text":
                        response_text += block.text

                if not response_text:
                    response_text = "I'm not sure how to respond to that."

            # Send response (outside semaphore to allow concurrent API calls while sending)
            try:
                sent_message = await message.channel.send(response_text)
            except discord.HTTPException as e:
                logger.error(f"Failed to send response to Discord: {e}")
                self.conversation_logger.log_error(f"Discord send failed: {str(e)}")
                self.conversation_logger.log_separator()
                return

            # Log thinking trace (if present) and bot response
            if thinking_text:
                self.conversation_logger.log_thinking(thinking_text, len(thinking_text))
            self.conversation_logger.log_bot_response(response_text, len(response_text))

            # Record response and start engagement tracking
            self.rate_limiter.record_response(channel_id)

            # Log engagement tracking start
            self.conversation_logger.log_engagement_tracking(started=True)
            self.conversation_logger.log_separator()

            # Create and track background task
            task = asyncio.create_task(
                self._track_engagement(
                    sent_message.id,
                    message.channel,
                    delay=self.config.rate_limiting.engagement_tracking_delay,
                )
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

            logger.info(
                f"Response sent to {message.author.name} ({len(response_text)} chars)"
            )

        except Exception as e:
            logger.error(f"Error calling Claude API: {e}", exc_info=True)
            self.conversation_logger.log_error(f"Claude API error: {str(e)}")
            self.conversation_logger.log_separator()
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
        base_prompt = (
            self.config.personality.base_prompt
            if self.config.personality
            else "You are a helpful Discord bot assistant."
        )

        # Add clarification about conversation history
        system_prompt = f"""{base_prompt}

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
            history_parts = []
            history_parts.append("# Recent Conversation History")
            history_parts.append("")

            for msg in recent_messages:
                # Clarify bot's own messages vs user messages
                if msg.is_bot:
                    author_display = "Assistant (you)"
                else:
                    author_display = msg.author_name
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
            method = "reactions" if has_reactions else "replies"
            self.conversation_logger.log_engagement_result(engaged=True, method=method)
            logger.debug(f"Message {message_id}: ENGAGED ({method})")
        else:
            self.rate_limiter.record_ignored(channel_id)
            self.conversation_logger.log_engagement_result(engaged=False)
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

    async def shutdown(self):
        """Cancel all background tasks for clean shutdown"""
        logger.info(f"Cancelling {len(self._background_tasks)} background tasks...")
        for task in self._background_tasks:
            task.cancel()
        # Wait briefly for cancellations to complete
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        logger.info("ReactiveEngine shutdown complete")
