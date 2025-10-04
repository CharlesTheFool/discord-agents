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
from pathlib import Path

if TYPE_CHECKING:
    from .config import BotConfig
    from .rate_limiter import RateLimiter
    from .message_memory import MessageMemory
    from .memory_manager import MemoryManager
    from .conversation_logger import ConversationLogger

from .memory_tool_executor import MemoryToolExecutor
from .context_builder import ContextBuilder

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

        # Initialize memory tool executor
        self.memory_tool_executor = MemoryToolExecutor(
            memory_base_path=Path("memories"),
            bot_id=config.bot_id
        )

        # Initialize context builder
        self.context_builder = ContextBuilder(
            config=config,
            message_memory=message_memory,
            memory_manager=memory_manager
        )

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
        context = await self.context_builder.build_context(message)

        # Log context building stats
        if context.get("stats"):
            self.conversation_logger.log_context_building(**context["stats"])

        # Log cache status
        self.conversation_logger.log_cache_status(enabled=self.config.api.context_editing.enabled)

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
                        "tools": [{"type": "memory_20250818", "name": "memory"}],
                        "extra_headers": {"anthropic-beta": "context-management-2025-06-27,prompt-caching-2024-07-31"}
                    }

                    # Add system prompt with cache control if context editing enabled
                    if self.config.api.context_editing.enabled:
                        api_params["system"] = [
                            {
                                "type": "text",
                                "text": context["system_prompt"],
                                "cache_control": {"type": "ephemeral"}
                            }
                        ]
                    else:
                        api_params["system"] = context["system_prompt"]

                    # Add messages
                    api_params["messages"] = context["messages"]

                    # Add extended thinking if enabled
                    if self.config.api.extended_thinking.enabled:
                        api_params["thinking"] = {
                            "type": "enabled",
                            "budget_tokens": self.config.api.extended_thinking.budget_tokens
                        }

                    # Tool use loop - continue until end_turn
                    thinking_text = ""
                    response_text = ""
                    loop_iteration = 0

                    while True:
                        loop_iteration += 1

                        # Call Claude API
                        response = await self.anthropic.messages.create(**api_params)

                        # Log tool use loop iteration
                        self.conversation_logger.log_tool_use_loop(loop_iteration, response.stop_reason)

                        # Extract thinking if present
                        for block in response.content:
                            if block.type == "thinking":
                                thinking_text += block.thinking

                        # Check stop reason
                        if response.stop_reason == "tool_use":
                            # Execute tool calls
                            tool_results = []

                            for block in response.content:
                                if block.type == "tool_use":
                                    command = block.input.get('command', 'unknown')
                                    path = block.input.get('path', 'unknown')
                                    logger.debug(f"Executing memory tool: {command} {path}")

                                    # Execute memory command
                                    result = self.memory_tool_executor.execute(block.input)

                                    # Log memory tool operation
                                    self.conversation_logger.log_memory_tool(command, path, result)

                                    tool_results.append({
                                        "type": "tool_result",
                                        "tool_use_id": block.id,
                                        "content": result
                                    })

                            # Add assistant message with tool use to conversation
                            api_params["messages"].append({
                                "role": "assistant",
                                "content": response.content
                            })

                            # Add tool results as user message
                            api_params["messages"].append({
                                "role": "user",
                                "content": tool_results
                            })

                            # Continue loop for next API call
                            continue

                        elif response.stop_reason == "end_turn":
                            # Extract final text response
                            for block in response.content:
                                if block.type == "text":
                                    response_text += block.text

                            if not response_text:
                                response_text = "I'm not sure how to respond to that."

                            # Exit loop
                            break

                        else:
                            # Unexpected stop reason
                            logger.warning(f"Unexpected stop_reason: {response.stop_reason}")
                            response_text = "I'm not sure how to respond to that."
                            break

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
                    original_author_id=message.author.id,
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

    async def _track_engagement(
        self, message_id: int, channel: discord.TextChannel, original_author_id: int, delay: int
    ):
        """
        Track engagement on bot message.

        Waits for delay, then checks if message got reactions or replies.

        Args:
            message_id: Discord message ID to track
            channel: Channel where message was sent
            original_author_id: User ID who originally triggered the bot
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
        reaction_details = []
        total_reaction_count = 0

        if has_reactions:
            for reaction in message.reactions:
                emoji_str = str(reaction.emoji)
                count = reaction.count
                total_reaction_count += count
                reaction_details.append(f"{emoji_str}×{count}")

        # Check for replies or any messages from original user
        has_replies = await self._check_for_replies(message, channel, original_author_id)

        # Record result
        engaged = has_reactions or has_replies

        if engaged:
            self.rate_limiter.record_engagement(channel_id)

            # Build detailed method string
            if has_reactions and has_replies:
                method = f"reactions ({', '.join(reaction_details)}) + replies"
            elif has_reactions:
                method = f"reactions ({', '.join(reaction_details)})"
            else:
                method = "replies"

            self.conversation_logger.log_engagement_result(engaged=True, method=method)
            logger.debug(f"Message {message_id}: ENGAGED - {method}")
        else:
            self.rate_limiter.record_ignored(channel_id)
            self.conversation_logger.log_engagement_result(engaged=False)
            logger.debug(f"Message {message_id}: IGNORED (no reactions or replies)")

    async def _check_for_replies(
        self, message: discord.Message, channel: discord.TextChannel, original_author_id: int
    ) -> bool:
        """
        Check if user engaged after bot's message.

        Detects engagement via:
        - Formal Discord replies to bot's message
        - ANY message from original user in channel (loose engagement)

        Args:
            message: Bot's message to check engagement for
            channel: Channel to search
            original_author_id: User ID who originally triggered the bot

        Returns:
            True if engagement detected (reply or any message from user)
        """
        try:
            # Get messages after bot's message
            recent = []
            async for msg in channel.history(after=message.created_at, limit=10):
                recent.append(msg)

            # Check for engagement
            for msg in recent:
                # Formal reply to bot's message
                if msg.reference and msg.reference.message_id == message.id:
                    return True

                # Any message from original user (loose engagement)
                if msg.author.id == original_author_id:
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
