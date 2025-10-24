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
from collections import deque
import sys
import os

# Add tools directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

if TYPE_CHECKING:
    from .config import BotConfig
    from .rate_limiter import RateLimiter
    from .message_memory import MessageMemory
    from .memory_manager import MemoryManager
    from .conversation_logger import ConversationLogger

from .memory_tool_executor import MemoryToolExecutor
from .context_builder import ContextBuilder
from .mcp_manager import MCPManager
from .skills_manager import SkillsManager
from .multimedia_processor import MultimediaProcessor
from .data_isolation import DataIsolationEnforcer
from tools.web_search import WebSearchManager, get_web_search_tools
from tools.discord_tools import DiscordToolExecutor, get_discord_tools

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

        # Initialize web search manager if enabled
        self.web_search_manager = None
        if config.api.web_search.enabled:
            stats_file = Path("persistence") / f"{config.bot_id}_web_search_stats.json"
            self.web_search_manager = WebSearchManager(
                stats_file=stats_file,
                max_daily=config.api.web_search.max_daily
            )
            logger.info(f"Web search enabled (max_daily={config.api.web_search.max_daily})")

        # Initialize Discord tool executor (Phase 4)
        # Get user_cache from discord_client once it's set in on_ready
        self.discord_tool_executor = None
        self._discord_tools_enabled = True  # Always enabled for Phase 4

        # Initialize v0.5.0 managers
        # MCP Manager
        self.mcp_manager = None
        if config.mcp.enabled:
            self.mcp_manager = MCPManager(config_path=Path(config.mcp.config_file))
            logger.info("MCP manager initialized")

        # Skills Manager
        self.skills_manager = None
        if config.skills.enabled:
            self.skills_manager = SkillsManager(
                skills_dir=Path(config.skills.skills_dir),
                cache_file=Path(config.skills.cache_file),
                anthropic_api_key=anthropic_api_key
            )
            logger.info("Skills manager initialized")

        # Multimedia Processor
        self.multimedia_processor = None
        if config.multimedia.enabled:
            self.multimedia_processor = MultimediaProcessor(
                anthropic_client=self.anthropic,
                max_file_size_mb=config.multimedia.max_file_size_mb
            )
            logger.info("Multimedia processor initialized")

        # Data Isolation Enforcer
        self.data_isolation = DataIsolationEnforcer(config.data_isolation)
        logger.info(f"Data isolation enforcer initialized (enabled: {config.data_isolation.enabled})")

        # Track background tasks for clean shutdown
        self._background_tasks = set()

        # Prevent concurrent responses (fixes multiple responses to single mention)
        self._response_semaphore = asyncio.Semaphore(1)

        # Track messages that have been responded to (prevent race condition)
        # Using deque with maxlen to automatically discard old entries
        self._responded_messages = deque(maxlen=1000)

        # Pending channels for periodic check (Phase 3)
        self.pending_channels = set()
        self._periodic_task = None
        self._running = False
        self.discord_client = None  # Set by DiscordClient on_ready

        logger.info(f"ReactiveEngine initialized for bot '{config.bot_id}'")

    async def async_initialize(self):
        """
        Async initialization for managers that require I/O.
        Should be called after bot is ready.
        """
        # Initialize MCP Manager (discover tools from servers)
        if self.mcp_manager:
            try:
                await self.mcp_manager.initialize()
                logger.info("MCP manager initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize MCP manager: {e}", exc_info=True)
                self.mcp_manager = None  # Disable on failure

        # Initialize Skills Manager (scan and upload skills)
        if self.skills_manager:
            try:
                await self.skills_manager.initialize()
                logger.info("Skills manager initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Skills manager: {e}", exc_info=True)
                self.skills_manager = None  # Disable on failure

    async def handle_urgent(self, message: discord.Message):
        """
        Handle urgent message (@mention) immediately.

        Args:
            message: Discord message with @mention
        """
        # Check if already responded/processing this message (prevent race condition)
        if message.id in self._responded_messages:
            logger.debug(f"Already responded to @mention {message.id}, skipping")
            return

        # Mark as being processed immediately (before building context)
        self._responded_messages.append(message.id)

        channel_id = str(message.channel.id)

        # Log incoming message
        self.conversation_logger.log_user_message(
            author=message.author.display_name,
            channel=message.channel.name,
            content=message.content,
            is_mention=True
        )

        # Process multimedia attachments (v0.5.0)
        multimedia_files = []
        if self.multimedia_processor and message.attachments:
            for attachment in message.attachments:
                try:
                    file_info = await self.multimedia_processor.process_attachment(attachment)
                    if file_info:
                        multimedia_files.append(file_info)
                        logger.info(f"Processed multimedia file: {attachment.filename}")
                except Exception as e:
                    logger.error(f"Failed to process attachment {attachment.filename}: {e}")

        # @mentions ALWAYS bypass rate limits (especially ignore threshold)
        # Get stats for logging but don't check limits
        rate_limit_stats = self.rate_limiter.get_stats(channel_id)

        # Log decision
        self.conversation_logger.log_decision(
            should_respond=True,
            reason="mention detected (bypasses rate limits)",
            rate_limit_stats=rate_limit_stats
        )

        # Call Claude API
        logger.info(f"Calling Claude API for @mention from {message.author.name}")

        try:
            # Acquire semaphore FIRST to prevent race condition
            # Context is built inside semaphore so each message gets isolated context
            async with self._response_semaphore:
                # Build context (inside semaphore to prevent overlapping contexts)
                # Exclude messages that are currently being processed to prevent seeing other pending @mentions
                context = await self.context_builder.build_context(
                    message,
                    exclude_message_ids=list(self._responded_messages)
                )

                # Log context building stats
                if context.get("stats"):
                    self.conversation_logger.log_context_building(**context["stats"])

                # Log cache status
                self.conversation_logger.log_cache_status(enabled=self.config.api.context_editing.enabled)
                # Show typing indicator
                async with message.channel.typing():
                    # Small delay for more natural feel
                    await asyncio.sleep(1.5)

                    # Build API request parameters
                    tools = [{"type": "memory_20250818", "name": "memory"}]

                    # Add Discord tools if enabled (Phase 4)
                    if self.discord_tool_executor:
                        tools.extend(get_discord_tools())
                        logger.debug("Discord tools added to API request")
                    else:
                        logger.warning("Discord tool executor is None - tools NOT added!")

                    # Add web search tools if enabled and quota available
                    beta_headers = ["context-management-2025-06-27", "prompt-caching-2024-07-31"]
                    if self.web_search_manager:
                        can_search, reason = self.web_search_manager.can_search()
                        if can_search:
                            max_uses = self.config.api.web_search.max_per_request
                            citations_enabled = self.config.api.web_search.citations_enabled
                            tools.extend(get_web_search_tools(max_uses=max_uses, citations_enabled=citations_enabled))
                            beta_headers.append("web-fetch-2025-09-10")
                            logger.debug(f"Web search tools added to API request (max_uses={max_uses}, citations={citations_enabled})")
                        else:
                            logger.debug(f"Web search disabled for this request: {reason}")

                    # Add MCP tools if enabled (v0.5.0)
                    if self.mcp_manager:
                        mcp_tools = self.mcp_manager.get_tools_for_api()
                        if mcp_tools:
                            tools.extend(mcp_tools)
                            logger.debug(f"Added {len(mcp_tools)} MCP tools to API request")

                    # Add code execution tool if multimedia or skills enabled (v0.5.0)
                    code_execution_needed = self.multimedia_processor or self.skills_manager
                    if code_execution_needed:
                        tools.append({
                            "type": "code_execution_20250825",
                            "name": "code_execution"
                        })
                        beta_headers.append("code-execution-2025-08-25")
                        logger.debug("Code execution tool added to API request")

                        # Add files API beta if multimedia enabled
                        if self.multimedia_processor:
                            beta_headers.append("files-api-2025-04-14")

                        # Add skills beta if skills enabled
                        if self.skills_manager:
                            beta_headers.append("skills-2025-10-02")

                    api_params = {
                        "model": self.config.api.model,
                        "max_tokens": self.config.api.max_tokens,
                        "tools": tools,
                        "extra_headers": {"anthropic-beta": ",".join(beta_headers)}
                    }

                    # Add skills container if skills enabled (v0.5.0)
                    if self.skills_manager:
                        skills_list = self.skills_manager.get_all_skills_for_api() if self.config.skills.include_anthropic_skills else self.skills_manager.get_skills_for_api()
                        if skills_list:
                            api_params["container"] = {"skills": skills_list}
                            logger.debug(f"Added {len(skills_list)} skills to container")

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
                    # Include multimedia files in message content if present (v0.5.0)
                    messages = context["messages"].copy()
                    if multimedia_files:
                        # Add multimedia files to the last user message
                        if messages and messages[-1]["role"] == "user":
                            # Convert content to list format if it's a string
                            if isinstance(messages[-1]["content"], str):
                                messages[-1]["content"] = [
                                    {"type": "text", "text": messages[-1]["content"]}
                                ]

                            # Add file uploads
                            for file_info in multimedia_files:
                                messages[-1]["content"].append({
                                    "type": "container_upload",
                                    "file_id": file_info["file_id"]
                                })
                                logger.debug(f"Added multimedia file to message: {file_info['filename']}")

                                # Add processing instructions as a text block
                                instructions = self.multimedia_processor.get_processing_instructions(
                                    file_info["file_type"],
                                    file_info["filename"]
                                )
                                messages[-1]["content"].append({
                                    "type": "text",
                                    "text": instructions
                                })

                    api_params["messages"] = messages

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

                        # Track server tool usage (web_search, web_fetch)
                        # Server tools appear as server_tool_use blocks in response.content
                        if self.web_search_manager:
                            # Log all response blocks for debugging
                            logger.info(f"Response has {len(response.content)} content blocks")
                            for i, block in enumerate(response.content):
                                logger.info(f"  Block {i}: type={block.type} (type class: {type(block.type).__name__}, repr: {repr(block.type)})")

                                # Log text blocks
                                if hasattr(block, 'text'):
                                    preview = block.text[:300] if len(block.text) > 300 else block.text
                                    logger.info(f"    Text preview: {preview}")

                                # Log web_fetch_tool_result content
                                # Structure: block.content.content.source.data contains the fetched text
                                if block.type == "web_fetch_tool_result":
                                    if hasattr(block, 'content') and hasattr(block.content, 'content'):
                                        if hasattr(block.content.content, 'source') and hasattr(block.content.content.source, 'data'):
                                            fetch_text = block.content.content.source.data[:1000]
                                            logger.info(f"    WEB_FETCH CONTENT (first 1000 chars): {fetch_text}")
                                        else:
                                            logger.warning(f"    web_fetch_tool_result has unexpected structure")

                                if hasattr(block, 'type') and block.type == "server_tool_use":
                                    if hasattr(block, 'name') and block.name in ["web_search", "web_fetch"]:
                                        self.web_search_manager.record_search()
                                        logger.info(f"Server tool used: {block.name}")

                                        # Log server tool input/output for debugging
                                        logger.info(f"Server tool {block.name} input: {block.input}")
                                        logger.info(f"Server tool {block.name} id: {block.id}")

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
                                    # Handle memory tool
                                    if block.name == "memory":
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

                                    # Handle Discord tools (Phase 4)
                                    elif block.name == "discord_tools":
                                        if self.discord_tool_executor:
                                            command = block.input.get('command', 'unknown')
                                            logger.debug(f"Executing Discord tool: {command}")

                                            # Execute Discord tool
                                            result = await self.discord_tool_executor.execute(block.input)

                                            tool_results.append({
                                                "type": "tool_result",
                                                "tool_use_id": block.id,
                                                "content": result
                                            })

                                    # Handle MCP tools (v0.5.0)
                                    elif "_" in block.name and self.mcp_manager:
                                        # MCP tools are prefixed with server name (e.g., "github_get_commits")
                                        logger.debug(f"Executing MCP tool: {block.name} with input: {block.input}")
                                        try:
                                            result = await self.mcp_manager.execute_tool(
                                                tool_name=block.name,
                                                arguments=block.input
                                            )

                                            tool_results.append({
                                                "type": "tool_result",
                                                "tool_use_id": block.id,
                                                "content": str(result)
                                            })
                                        except Exception as e:
                                            logger.error(f"MCP tool execution failed: {e}", exc_info=True)
                                            tool_results.append({
                                                "type": "tool_result",
                                                "tool_use_id": block.id,
                                                "content": f"Error executing MCP tool: {str(e)}",
                                                "is_error": True
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
                            # Extract final text response and citations
                            citations_list = []
                            for block in response.content:
                                if block.type == "text":
                                    response_text += block.text

                                    # Extract citations if present
                                    if hasattr(block, 'citations') and block.citations:
                                        for citation in block.citations:
                                            url = getattr(citation, 'url', None)
                                            title = getattr(citation, 'title', None)
                                            if url and title:
                                                citations_list.append(f"[{title}]({url})")

                            # Append citations to response
                            if citations_list:
                                response_text += "\n\n**Sources:**\n" + "\n".join(f"- {cite}" for cite in citations_list)

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
            self.conversation_logger.log_engagement_tracking(
                started=True,
                delay_seconds=self.config.rate_limiting.engagement_tracking_delay
            )
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

            # Don't respond if we already responded to this message (prevents race condition)
            if latest_message.id in self._responded_messages:
                logger.debug(f"Already responded to message {latest_message.id} in {channel_id}, skipping")
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
            tools = [{"type": "memory_20250818", "name": "memory"}]

            # Add Discord tools if enabled (Phase 4)
            if self.discord_tool_executor:
                tools.extend(get_discord_tools())
                logger.debug("Discord tools added to periodic check")
            else:
                logger.warning("Discord tool executor is None - tools NOT added to periodic check!")

            # Add web search tools if enabled and quota available
            beta_headers = ["context-management-2025-06-27", "prompt-caching-2024-07-31"]
            if self.web_search_manager:
                can_search, reason = self.web_search_manager.can_search()
                if can_search:
                    max_uses = self.config.api.web_search.max_per_request
                    citations_enabled = self.config.api.web_search.citations_enabled
                    tools.extend(get_web_search_tools(max_uses=max_uses, citations_enabled=citations_enabled))
                    beta_headers.append("web-fetch-2025-09-10")
                    logger.debug(f"Web search tools added to periodic check (max_uses={max_uses}, citations={citations_enabled})")
                else:
                    logger.debug(f"Web search disabled for periodic check: {reason}")

            api_params = {
                "model": self.config.api.model,
                "max_tokens": self.config.api.max_tokens,
                "system": system_prompt,
                "messages": context["messages"],
                "tools": tools,
                "extra_headers": {"anthropic-beta": ",".join(beta_headers)}
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

                    # Track server tool usage (web_search, web_fetch)
                    # Server tools appear as server_tool_use blocks in response.content
                    if self.web_search_manager:
                        # Log all response blocks for debugging
                        logger.info(f"Response has {len(response.content)} content blocks (periodic)")
                        for i, block in enumerate(response.content):
                            logger.info(f"  Block {i}: type={block.type} (type class: {type(block.type).__name__}, repr: {repr(block.type)})")

                            # Log text blocks
                            if hasattr(block, 'text'):
                                preview = block.text[:300] if len(block.text) > 300 else block.text
                                logger.info(f"    Text preview: {preview}")

                            # Log web_fetch_tool_result content
                            # Structure: block.content.content.source.data contains the fetched text
                            if block.type == "web_fetch_tool_result":
                                if hasattr(block, 'content') and hasattr(block.content, 'content'):
                                    if hasattr(block.content.content, 'source') and hasattr(block.content.content.source, 'data'):
                                        fetch_text = block.content.content.source.data[:1000]
                                        logger.info(f"    WEB_FETCH CONTENT (first 1000 chars): {fetch_text}")
                                    else:
                                        logger.warning(f"    web_fetch_tool_result has unexpected structure")

                            if hasattr(block, 'type') and block.type == "server_tool_use":
                                if hasattr(block, 'name') and block.name in ["web_search", "web_fetch"]:
                                    self.web_search_manager.record_search()
                                    logger.info(f"Server tool used (periodic): {block.name}")

                                    # Log server tool input/output for debugging
                                    logger.info(f"Server tool {block.name} input (periodic): {block.input}")
                                    logger.info(f"Server tool {block.name} id (periodic): {block.id}")

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
                                # Handle memory tool
                                if content_block.name == "memory":
                                    result = self.memory_tool_executor.execute(content_block.input)
                                    tool_results.append({
                                        "type": "tool_result",
                                        "tool_use_id": content_block.id,
                                        "content": result
                                    })

                                # Handle Discord tools (Phase 4)
                                elif content_block.name == "discord_tools":
                                    if self.discord_tool_executor:
                                        result = await self.discord_tool_executor.execute(content_block.input)
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
                        # Extract final text response and citations
                        citations_list = []
                        for block in response.content:
                            if block.type == "text":
                                response_text += block.text

                                # Extract citations if present
                                if hasattr(block, 'citations') and block.citations:
                                    for citation in block.citations:
                                        url = getattr(citation, 'url', None)
                                        title = getattr(citation, 'title', None)
                                        if url and title:
                                            citations_list.append(f"[{title}]({url})")

                        # Append citations to response
                        if citations_list:
                            response_text += "\n\n**Sources:**\n" + "\n".join(f"- {cite}" for cite in citations_list)

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
                    self.conversation_logger.log_engagement_tracking(
                        started=True,
                        delay_seconds=self.config.rate_limiting.engagement_tracking_delay
                    )
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

                    # Mark message as responded to prevent duplicate responses
                    self._responded_messages.append(message.id)

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
