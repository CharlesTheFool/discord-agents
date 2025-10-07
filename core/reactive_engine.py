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

        # Pending channels for periodic check (Phase 3)
        self.pending_channels = set()
        self._periodic_task = None
        self._running = False
        self.discord_client = None  # Set by DiscordClient on_ready

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

                    # Add context management if enabled
                    if self.config.api.context_editing.enabled:
                        api_params["context_management"] = {
                            "edits": [
                                {
                                    "type": "clear_tool_uses_20250919",
                                    "trigger": {
                                        "type": "input_tokens",
                                        "value": self.config.api.context_editing.trigger_tokens
                                    },
                                    "keep": {
                                        "type": "tool_uses",
                                        "value": self.config.api.context_editing.keep_tool_uses
                                    },
                                    "exclude_tools": self.config.api.context_editing.exclude_tools,
                                }
                            ]
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

                        # Call Claude API (use beta endpoint if context management enabled)
                        if self.config.api.context_editing.enabled:
                            response = await self.anthropic.beta.messages.create(**api_params)
                        else:
                            response = await self.anthropic.messages.create(**api_params)

                        # Log tool use loop iteration
                        self.conversation_logger.log_tool_use_loop(loop_iteration, response.stop_reason)

                        # Log current input token count (first iteration only)
                        if loop_iteration == 1 and hasattr(response, 'usage'):
                            input_tokens = response.usage.input_tokens
                            trigger_threshold = self.config.api.context_editing.trigger_tokens
                            logger.info(f"Input tokens: {input_tokens:,} / {trigger_threshold:,} ({input_tokens/trigger_threshold*100:.1f}%)")

                        # Log context management stats if present (first iteration only)
                        if loop_iteration == 1 and hasattr(response, 'context_management') and response.context_management:
                            cm = response.context_management
                            # Sum up all cleared tool uses and tokens from applied_edits
                            total_cleared_tool_uses = 0
                            total_cleared_tokens = 0
                            if cm.applied_edits:
                                for edit in cm.applied_edits:
                                    total_cleared_tool_uses += getattr(edit, 'cleared_tool_uses', 0)
                                    total_cleared_tokens += getattr(edit, 'cleared_input_tokens', 0)

                            # Calculate original tokens (current + cleared)
                            current_tokens = response.usage.input_tokens if hasattr(response, 'usage') else 0
                            original_tokens = current_tokens + total_cleared_tokens

                            if total_cleared_tool_uses > 0:  # Only log if something was actually cleared
                                self.conversation_logger.log_context_management(
                                    tool_uses_cleared=total_cleared_tool_uses,
                                    tokens_cleared=total_cleared_tokens,
                                    original_tokens=original_tokens
                                )

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
            # Split message if it exceeds Discord's limit
            from .discord_client import split_message
            message_chunks = split_message(response_text)

            sent_message = None
            for i, chunk in enumerate(message_chunks):
                try:
                    # First chunk: reply to triggering message
                    # Subsequent chunks: standalone messages
                    if i == 0:
                        sent_message = await message.channel.send(chunk, reference=message)
                    else:
                        sent_message = await message.channel.send(chunk)
                except discord.HTTPException as e:
                    # If reply fails (e.g., message deleted), try without reference
                    if i == 0:
                        try:
                            logger.warning(f"Failed to send reply, trying standalone: {e}")
                            sent_message = await message.channel.send(chunk)
                        except discord.HTTPException as e2:
                            logger.error(f"Failed to send response to Discord: {e2}")
                            self.conversation_logger.log_error(f"Discord send failed: {str(e2)}")
                            self.conversation_logger.log_separator()
                            return
                    else:
                        logger.error(f"Failed to send message chunk {i+1}/{len(message_chunks)}: {e}")
                        self.conversation_logger.log_error(f"Discord send failed (chunk {i+1}): {str(e)}")
                        # Continue trying to send remaining chunks

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
        self._running = False

        # Cancel periodic task if running
        if self._periodic_task:
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass

        for task in self._background_tasks:
            task.cancel()
        # Wait briefly for cancellations to complete
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        logger.info("ReactiveEngine shutdown complete")

    # ========== PERIODIC SCANNING (Phase 3) ==========

    def start_periodic_check(self):
        """Start the periodic conversation scanning loop"""
        if not self.config.reactive.enabled:
            logger.info("Reactive engine disabled, not starting periodic check")
            return

        self._running = True
        self._periodic_task = asyncio.create_task(self._periodic_check_loop())
        logger.info(f"Periodic check started (interval: {self.config.reactive.check_interval_seconds}s)")

    async def _periodic_check_loop(self):
        """
        Periodic check loop - scans pending channels for response opportunities.

        Runs every check_interval_seconds (default 30s).
        """
        try:
            while self._running:
                await asyncio.sleep(self.config.reactive.check_interval_seconds)

                if not self.pending_channels:
                    continue

                # Get copy of pending channels to process
                channels_to_check = self.pending_channels.copy()
                self.pending_channels.clear()

                for channel_id in channels_to_check:
                    try:
                        await self._check_channel_for_response(channel_id)
                    except Exception as e:
                        logger.error(f"Error checking channel {channel_id}: {e}", exc_info=True)

        except asyncio.CancelledError:
            logger.info("Periodic check loop cancelled")
        except Exception as e:
            logger.error(f"Fatal error in periodic check loop: {e}", exc_info=True)

    async def _check_channel_for_response(self, channel_id: str):
        """
        Check if bot should respond to recent messages in channel.

        Args:
            channel_id: Discord channel ID to check
        """
        if not self.discord_client:
            logger.warning("Discord client not set, cannot perform periodic check")
            return

        # Check rate limits first
        can_respond, reason = self.rate_limiter.can_respond(channel_id)
        if not can_respond:
            logger.debug(f"Channel {channel_id} rate limited: {reason}")
            return

        # Get Discord channel object
        channel = self.discord_client.get_channel(int(channel_id))
        if not channel:
            logger.warning(f"Channel {channel_id} not found")
            return

        # Get recent messages from this channel
        recent_messages = await self.message_memory.get_recent(channel_id, limit=5)
        if not recent_messages:
            return

        # Get the most recent message for mock discord.Message creation
        latest_stored = recent_messages[-1]

        # Create a minimal mock message object for context building
        # This is a workaround since we don't have the actual discord.Message object
        try:
            # Fetch the actual latest message from Discord
            latest_message = None
            async for msg in channel.history(limit=1):
                latest_message = msg
                break

            if not latest_message:
                return

            # Don't respond if latest message is from the bot
            if latest_message.author == self.discord_client.user:
                logger.debug(f"Latest message in {channel_id} is from bot, skipping")
                return

            # Build context using the actual Discord message
            context = await self.context_builder.build_context(latest_message)

            # Calculate conversation momentum
            momentum = await self._calculate_conversation_momentum(channel_id)
            context["conversation_momentum"] = momentum

            # Call Claude API with response decision focus
            await self._call_claude_for_response_decision(latest_message, context)

        except Exception as e:
            logger.error(f"Error in periodic check for channel {channel_id}: {e}", exc_info=True)

    async def _calculate_conversation_momentum(self, channel_id: str) -> str:
        """
        Calculate conversation momentum based on message frequency.

        Args:
            channel_id: Discord channel ID

        Returns:
            "hot", "warm", or "cold" based on message frequency
        """
        try:
            # Get Discord channel
            channel = self.discord_client.get_channel(int(channel_id))
            if not channel:
                logger.debug(f"Channel {channel_id} not found, defaulting to cold")
                return "cold"

            # Fetch recent messages (last 20)
            messages = []
            async for msg in channel.history(limit=20):
                messages.append(msg)

            if len(messages) < 2:
                return "cold"

            # Calculate average gap between messages in minutes
            gaps = []
            for i in range(len(messages) - 1):
                time_diff = messages[i].created_at - messages[i + 1].created_at
                gap_minutes = time_diff.total_seconds() / 60
                gaps.append(gap_minutes)

            avg_gap = sum(gaps) / len(gaps)

            # Classify momentum
            if avg_gap < 15:
                return "hot"
            elif avg_gap < 60:
                return "warm"
            else:
                return "cold"

        except Exception as e:
            logger.error(f"Error calculating momentum for {channel_id}: {e}", exc_info=True)
            return "cold"

    async def _call_claude_for_response_decision(self, message: discord.Message, context):
        """
        Call Claude API to decide if bot should respond and generate response if yes.

        Args:
            message: Discord message object (latest in channel)
            context: Built context from ContextBuilder
        """
        channel_id = str(message.channel.id)

        # Log periodic check decision attempt
        self.conversation_logger.log_user_message(
            author="[PERIODIC CHECK]",
            channel=message.channel.name,
            content=f"Scanning conversation (momentum: {context['conversation_momentum'].upper()})",
            is_mention=False
        )

        # Log context building stats
        if context.get("stats"):
            self.conversation_logger.log_context_building(**context["stats"])

        # Prevent concurrent responses
        async with self._response_semaphore:
            # Build system prompt with response decision criteria
            system_prompt = self._build_response_decision_prompt(context)

            # Prepare API parameters
            api_params = {
                "model": self.config.api.model,
                "max_tokens": self.config.api.max_tokens,
                "system": system_prompt,
                "messages": context["messages"],
                "tools": [{"type": "memory_20250818", "name": "memory"}],
                "extra_headers": {"anthropic-beta": "context-management-2025-06-27,prompt-caching-2024-07-31"}
            }

            # Add extended thinking if enabled
            if self.config.api.extended_thinking.enabled:
                api_params["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": self.config.api.extended_thinking.budget_tokens
                }

            # Add context management if enabled
            if self.config.api.context_editing.enabled:
                api_params["context_management"] = {
                    "edits": [
                        {
                            "type": "clear_tool_uses_20250919",
                            "trigger": {
                                "type": "input_tokens",
                                "value": self.config.api.context_editing.trigger_tokens
                            },
                            "keep": {
                                "type": "tool_uses",
                                "value": self.config.api.context_editing.keep_tool_uses
                            },
                            "exclude_tools": self.config.api.context_editing.exclude_tools,
                        }
                    ]
                }

            # Call Claude API
            try:
                # Initialize response tracking
                thinking_text = ""
                response_text = ""
                loop_iteration = 0

                while True:
                    loop_iteration += 1

                    # Call API
                    if self.config.api.context_editing.enabled:
                        response = await self.anthropic.beta.messages.create(**api_params)
                    else:
                        response = await self.anthropic.messages.create(**api_params)

                    # Log current input token count (first iteration only)
                    if loop_iteration == 1 and hasattr(response, 'usage'):
                        input_tokens = response.usage.input_tokens
                        trigger_threshold = self.config.api.context_editing.trigger_tokens
                        logger.info(f"Input tokens: {input_tokens:,} / {trigger_threshold:,} ({input_tokens/trigger_threshold*100:.1f}%)")

                    # Extract thinking if present
                    for block in response.content:
                        if block.type == "thinking":
                            thinking_text += block.thinking

                    # Check stop reason
                    if response.stop_reason == "tool_use":
                        # Execute tool calls
                        tool_results = []
                        for content_block in response.content:
                            if content_block.type == "tool_use":
                                result = self.memory_tool_executor.execute(content_block.input)
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": content_block.id,
                                    "content": result
                                })

                        # Continue conversation with tool results
                        api_params["messages"].append({"role": "assistant", "content": response.content})
                        api_params["messages"].append({"role": "user", "content": tool_results})

                        # Continue loop for next API call
                        if loop_iteration >= 10:
                            logger.warning(f"Tool use loop exceeded 10 iterations in periodic check")
                            break
                        continue

                    elif response.stop_reason == "end_turn":
                        # Extract final text response
                        for block in response.content:
                            if block.type == "text":
                                response_text += block.text

                        # Exit loop
                        break

                    else:
                        # Unexpected stop reason
                        logger.warning(f"Unexpected stop_reason in periodic check: {response.stop_reason}")
                        break

                # If Claude decided to respond, send it
                if response_text.strip():
                    # Log thinking trace (if present) and bot response to conversation log
                    if thinking_text:
                        self.conversation_logger.log_thinking(thinking_text, len(thinking_text))
                    self.conversation_logger.log_bot_response(response_text, len(response_text))

                    # Send response as standalone (not a reply)
                    # Split message if it exceeds Discord's limit
                    from .discord_client import split_message
                    message_chunks = split_message(response_text)

                    sent_message = None
                    for i, chunk in enumerate(message_chunks):
                        try:
                            sent_message = await message.channel.send(chunk)
                        except discord.HTTPException as e:
                            logger.error(f"Failed to send periodic response chunk {i+1}/{len(message_chunks)}: {e}")
                            self.conversation_logger.log_error(f"Discord send failed: {str(e)}")
                            return

                    # Record response and track engagement
                    self.rate_limiter.record_response(channel_id)

                    # Log engagement tracking start
                    self.conversation_logger.log_engagement_tracking(started=True)
                    self.conversation_logger.log_separator()

                    # Track engagement in background
                    task = asyncio.create_task(
                        self._track_engagement(
                            message_id=sent_message.id,
                            channel=message.channel,
                            original_author_id=message.author.id,
                            delay=30
                        )
                    )
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)

                    logger.info(f"Periodic response sent in channel {channel_id} ({len(response_text)} chars)")
                else:
                    logger.debug(f"Claude decided not to respond in channel {channel_id}")
                    # Log thinking trace if present, then log decision to stay silent
                    if thinking_text:
                        self.conversation_logger.log_thinking(thinking_text, len(thinking_text))
                    self.conversation_logger.log_bot_response("[No response - staying silent]", 0)
                    self.conversation_logger.log_separator()

            except Exception as e:
                logger.error(f"Error calling Claude for periodic check: {e}", exc_info=True)
                self.conversation_logger.log_error(f"Periodic check error: {str(e)}")
                self.conversation_logger.log_separator()

    def _build_response_decision_prompt(self, context) -> list:
        """
        Build system prompt for response decision in periodic checks.

        Includes criteria for when to respond based on conversation momentum.

        Args:
            context: Context package from ContextBuilder

        Returns:
            System prompt list for Claude API
        """
        bot_name = self.discord_client.user.display_name if self.discord_client else "Assistant"

        # Get response rates from config
        cold_rate = int(self.config.personality.cold_conversation_rate * 100)
        warm_rate = int(self.config.personality.warm_conversation_rate * 100)
        hot_rate = int(self.config.personality.hot_conversation_rate * 100)

        decision_criteria = f"""

# Response Decision Criteria (Periodic Check)

You are monitoring an ongoing Discord conversation. Decide whether to participate based on:

**Direct Triggers** (Always consider responding):
- Your name "{bot_name}" mentioned (even without @)
- Question you can answer
- Topic within your expertise
- Someone needs help you can provide
- Conversation about technical topics you understand

**Conversation Momentum** (Response probability):
- COLD (idle/slow): {cold_rate}% base chance - only if very valuable contribution
- WARM (steady): {warm_rate}% base chance - if relevant and helpful
- HOT (active): {hot_rate}% base chance - participate naturally if appropriate

**DON'T Respond If**:
- Nothing meaningful to add
- Conversation doesn't need your input
- Would interrupt natural flow
- Just agreeing without adding value
- Making conversation about yourself

**Current Conversation Momentum**: {context["conversation_momentum"].upper()}

**RESPONSE FORMAT:**
- If you decide to respond: Output ONLY your message to the channel (no meta-commentary, no explanation of your decision)
- If you decide NOT to respond: Output ABSOLUTELY NOTHING (not even an explanation - complete silence)

DO NOT explain your reasoning for responding or not responding. DO NOT output meta-commentary about the conversation. Either respond naturally or output nothing.
"""

        system_prompt = [
            {
                "type": "text",
                "text": context["system_prompt"] + decision_criteria,
                "cache_control": {"type": "ephemeral"} if self.config.api.context_editing.enabled else None
            }
        ]

        # Remove None cache_control if not using context editing
        if not self.config.api.context_editing.enabled:
            system_prompt[0].pop("cache_control", None)

        return system_prompt

    def add_pending_channel(self, channel_id: str):
        """
        Add channel to pending list for periodic check.

        Args:
            channel_id: Discord channel ID
        """
        self.pending_channels.add(channel_id)
