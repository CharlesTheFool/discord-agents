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
from .unified_attachment_manager import UnifiedAttachmentManager
from .data_isolation import DataIsolationEnforcer
from .conversation_state_manager import ConversationStateManager
from .internal_constants import TOOL_STUB_KEEP_TURNS
from tools.web_search import get_web_search_tools
from tools.discord_tools import DiscordToolExecutor, get_discord_tools
from tools.skills_tool import get_skill_request_tool, SkillRequestExecutor

logger = logging.getLogger(__name__)


def total_input_tokens(usage) -> int:
    """
    Full context size from response.usage. With prompt caching, input_tokens
    counts only uncached tokens - cached reads/writes live in separate fields.
    """
    return (
        usage.input_tokens
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    )


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

        # Cache internal config values (v0.6.0 - simplified config)
        self._rate_limiting_config = config.get_rate_limiting_config()

        # Initialize data isolation enforcer first (v0.5.0)
        # data_isolation is now a string mode, not an object - use helper method to expand
        data_isolation_config = config.get_data_isolation_config()
        self.data_isolation = DataIsolationEnforcer(data_isolation_config)
        logger.info(f"Data isolation enforcer initialized (mode: {config.data_isolation})")

        # Initialize memory tool executor
        self.memory_tool_executor = MemoryToolExecutor(
            memory_base_path=Path("memories"),
            bot_id=config.bot_id,
            data_isolation=self.data_isolation
        )

        # Initialize context builder
        self.context_builder = ContextBuilder(
            config=config,
            message_memory=message_memory,
            memory_manager=memory_manager,
            data_isolation=self.data_isolation
        )

        # Web search: all-or-nothing (no rate limiting)
        self.web_search_enabled = config.api.web_search.enabled
        if self.web_search_enabled:
            logger.info("Web search enabled (unlimited)")

        # Initialize Discord tool executor (Phase 4)
        # Get user_cache from discord_client once it's set in on_ready
        self.discord_tool_executor = None
        self._discord_tools_enabled = True  # Always enabled for Phase 4

        # Initialize v0.5.0 managers
        # MCP Manager
        self.mcp_manager = None
        if config.mcp.enabled:
            mcp_config = config.get_mcp_config()
            self.mcp_manager = MCPManager(config_path=Path(mcp_config["config_file"]))
            logger.info("MCP manager initialized")

        # Skills Manager (always enabled - skills require code_execution tool)
        skills_config = config.get_skills_config()
        self.skills_manager = SkillsManager(
            skills_dir=Path(skills_config["skills_dir"]),
            cache_file=Path(skills_config["cache_file"]),
            anthropic_api_key=anthropic_api_key
        )
        logger.info(f"Skills manager initialized with directory: {skills_config['skills_dir']}")

        # Pass skills_manager to ContextBuilder for skills catalog in system prompt
        self.context_builder.skills_manager = self.skills_manager

        # Skill Request Executor (v0.5.0 Progressive Disclosure)
        self.skill_request_executor = SkillRequestExecutor(
            skills_manager=self.skills_manager,
            max_skills=self.skills_manager.MAX_SKILLS_PER_REQUEST
        )
        logger.info("Skill request executor initialized")

        # Unified Attachment Manager (replaces multimedia_processor)
        self.attachment_manager = None  # Initialized in async_initialize

        # Conversation State Manager (v0.5.0 - dual-cap context management)
        self.conversation_state_manager = None  # Initialized in async_initialize

        # Episode Manager (v0.6.0 episodic sessions)
        self.episode_manager = None  # Initialized in async_initialize

        # Track background tasks for clean shutdown
        self._background_tasks = set()

        # Prevent concurrent responses (fixes multiple responses to single mention)
        self._response_semaphore = asyncio.Semaphore(1)

        # Track messages that have been responded to (prevent race condition)
        # Using deque with maxlen to automatically discard old entries
        self._responded_messages = deque(maxlen=1000)

        # Pending channels for periodic check (Phase 3)
        # Bug #7 fix: Track individual messages, not just channels
        # List of (channel_id, message_id) tuples to prevent message loss
        self.pending_messages = []
        self._periodic_task = None
        self._running = False
        self.discord_client = None  # Set by DiscordClient on_ready

        logger.info(f"ReactiveEngine initialized for bot '{config.bot_id}'")

    async def async_initialize(self):
        """
        Async initialization for managers that require I/O.
        Should be called after bot is ready.
        """
        # Initialize Conversation State Manager (v0.5.0 - dual-cap context management)
        try:
            db_path = Path("persistence") / f"{self.config.bot_id}_conversation_states.db"
            self.conversation_state_manager = ConversationStateManager(
                db_path=db_path,
                bot_id=self.config.bot_id,
                max_messages=self.config.api.context_messages
            )
            await self.conversation_state_manager.initialize()
            logger.info(
                f"ConversationStateManager initialized "
                f"(max_messages={self.config.api.context_messages})"
            )
        except Exception as e:
            logger.error(f"Failed to initialize ConversationStateManager: {e}", exc_info=True)
            self.conversation_state_manager = None  # Disable on failure

        # Episode manager (v0.6.0 episodic sessions)
        self.episode_manager = None
        if self.conversation_state_manager:
            from core.episode_manager import EpisodeManager
            self.episode_manager = EpisodeManager(
                message_memory=self.message_memory,
                memory_manager=self.memory_manager,
                conversation_state_manager=self.conversation_state_manager,
                anthropic_client=self.anthropic,
            )

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

        # Initialize Unified Attachment Manager (replaces multimedia_processor)
        if self.config.attachments.enabled:
            try:
                self.attachment_manager = UnifiedAttachmentManager(
                    config=self.config,
                    anthropic_client=self.anthropic,
                    message_memory=self.message_memory
                )
                await self.attachment_manager.initialize()
                logger.info("UnifiedAttachmentManager initialized successfully")

                # Pass attachment_manager to ContextBuilder
                self.context_builder.attachment_manager = self.attachment_manager
                logger.debug("ContextBuilder updated with attachment_manager")
            except Exception as e:
                logger.error(f"Failed to initialize UnifiedAttachmentManager: {e}", exc_info=True)
                self.attachment_manager = None  # Disable on failure

    async def _initialize_conversation_from_db(
        self,
        channel_id: str,
        conversation_state,
        limit: int = None
    ) -> None:
        """
        Initialize conversation state from database with full attachment data.

        Used by both urgent and periodic paths to ensure consistent state after restart.
        Loads recent messages with attachments, building proper content blocks.

        Fixes Disconnect #1: Both paths now use the same initialization logic.
        """
        if limit is None:
            limit = self.config.api.context_messages

        logger.info("Conversation state empty, initializing from recent DB messages")
        recent_messages = await self.message_memory.get_recent(
            channel_id,
            limit=limit
        )

        # Add recent messages to state WITH attachments
        for db_msg in recent_messages:
            role = "assistant" if db_msg.is_bot else "user"

            # Query attachments for this message if it has any
            attachment_ids = []
            content_blocks = []

            if db_msg.has_attachments and self.attachment_manager:
                try:
                    # Query attachments table for this message (Phase 2.2: added local_path for expiration checking)
                    async with self.attachment_manager.attachment_db.db.execute(
                        "SELECT attachment_id, filename, local_path FROM attachments WHERE message_id = ?",
                        (str(db_msg.message_id),)
                    ) as cursor:
                        rows = await cursor.fetchall()

                    # Retrieve each attachment via retroactive processing
                    for row in rows:
                        attachment_id, filename, local_path = row

                        try:
                            file_data = await self.attachment_manager.get_attachment_for_processing(attachment_id)

                            if file_data:
                                if file_data["method"] == "base64":
                                    # Add text indicator for visibility
                                    content_blocks.append({
                                        "type": "text",
                                        "text": f"\n[Image: {filename}]"
                                    })
                                    # Only add image blocks to user messages (API restriction)
                                    if role == "user":
                                        attachment_ids.append(attachment_id)
                                        content_blocks.append({
                                            "type": "image",
                                            "source": {
                                                "type": "base64",
                                                "media_type": file_data["media_type"],
                                                "data": file_data["data"]
                                            }
                                        })
                                        logger.debug(f"Loaded image {filename} from DB")
                                    else:
                                        logger.debug(f"Skipped image block for assistant message: {filename}")

                                elif file_data["method"] == "file_id":
                                    file_id = file_data["data"]

                                    # Add document block for reading (if eligible)
                                    if file_data.get("use_as_document_block", True):
                                        attachment_ids.append(attachment_id)
                                        # Add text indicator for visibility
                                        content_blocks.append({
                                            "type": "text",
                                            "text": f"\n[Document: {filename}]"
                                        })
                                        # Add document block (Files API format)
                                        content_blocks.append({
                                            "type": "document",
                                            "source": {
                                                "type": "file",
                                                "file_id": file_id
                                            }
                                        })
                                        logger.debug(f"Loaded document {filename} from DB")

                                    # Add container_upload block (matches periodic path behavior)
                                    else:
                                        # Phase 2.2: Check expiration and re-upload if needed
                                        if local_path:
                                            fresh_file_id = await self.attachment_manager._handle_container_upload_expiration(
                                                attachment_id=attachment_id,
                                                file_id=file_id,
                                                local_path=local_path,
                                                filename=filename
                                            )
                                            if fresh_file_id:
                                                file_id = fresh_file_id
                                            else:
                                                logger.error(f"Failed to verify/re-upload container_upload {filename}, skipping")
                                                continue  # Skip this attachment

                                        attachment_ids.append(attachment_id)
                                        content_blocks.append({
                                            "type": "container_upload",
                                            "file_id": file_id
                                        })
                                        logger.debug(f"Loaded container_upload file {filename} from DB")

                        except Exception as e:
                            logger.warning(f"Failed to load attachment {attachment_id} ({filename}): {e}")
                            content_blocks.append({
                                "type": "text",
                                "text": f"\n[Attachment: {filename} - no longer available]"
                            })

                except Exception as e:
                    logger.error(f"Failed to query attachments for message {db_msg.message_id}: {e}")

            # Build final content (text + attachments)
            if content_blocks:
                if db_msg.content:
                    content_blocks.insert(0, {"type": "text", "text": db_msg.content})
                content = content_blocks
            else:
                content = db_msg.content

            # Add to conversation state with attachment tracking
            conversation_state.add_message(
                role,
                content,
                attachment_ids=attachment_ids if attachment_ids else None
            )

        logger.info(f"Initialized conversation state with {len(recent_messages)} messages from DB")

    async def _generate_uploaded_files_manifest(self, conversation_state) -> str:
        """
        Attachment index for the system prompt (v0.6.0 Phase 4).

        A slim index of recent channel attachments - IDs plus one-liners -
        rather than a mirror of pinned content blocks. Survives session
        reseeds: anything "not in context" is retrievable via get_attachment.
        """
        if not (self.attachment_manager and self.attachment_manager.attachment_db):
            return ""

        try:
            async with self.attachment_manager.attachment_db.db.execute(
                """
                SELECT attachment_id, filename, size_bytes, attachment_type
                FROM attachments WHERE channel_id = ?
                ORDER BY CAST(attachment_id AS INTEGER) DESC LIMIT 15
                """,
                (conversation_state.channel_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        except Exception as e:
            logger.warning(f"Failed to query attachment index: {e}")
            return ""

        if not rows:
            return ""

        in_context_ids = set()
        for msg in conversation_state.messages:
            in_context_ids.update(msg.get("attachment_ids", []))

        def _size(n: int) -> str:
            if n < 1024:
                return f"{n} B"
            if n < 1024 ** 2:
                return f"{n / 1024:.1f} KB"
            return f"{n / 1024 ** 2:.1f} MB"

        lines = [
            "<attachments_index>",
            "Recent attachments in this channel (newest first):",
        ]
        for att_id, filename, size_bytes, att_type in rows:
            marker = "in context" if att_id in in_context_ids else "not in context"
            lines.append(f"- {att_id} | {filename} | {_size(size_bytes or 0)} | {att_type} | {marker}")
        lines += [
            "",
            "Retrieve any 'not in context' file with the discord tool: get_attachment + attachment_id.",
            "Spreadsheets and large files are processed via code execution; container files are",
            "mounted at the INPUT_DIR env var and are EPHEMERAL - read them in the same turn.",
            "</attachments_index>",
        ]
        return "\n".join(lines)

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

        # Load or create conversation state for this channel (v0.5.0)
        conversation_state = None
        if self.conversation_state_manager:
            try:
                conversation_state = await self.conversation_state_manager.get_or_create(channel_id)
                logger.debug(f"Loaded conversation state: {conversation_state}")

                # If state is empty, initialize with recent DB messages (Disconnect #1 fix)
                if len(conversation_state.messages) == 0:
                    await self._initialize_conversation_from_db(channel_id, conversation_state)

            except Exception as e:
                logger.error(f"Failed to load conversation state for channel {channel_id}: {e}", exc_info=True)

        # Log incoming message
        self.conversation_logger.log_user_message(
            author=message.author.display_name,
            channel=message.channel.name,
            content=message.content,
            is_mention=True
        )

        # Process attachments (v0.5.0 - using UnifiedAttachmentManager)
        # Bug #4 fix: Skip attachment processing for bot messages (treat bots as users, but don't track their attachments)
        # Bug #5 fix: Allow bot attachments when allow_bot_interactions is enabled (for testing)
        processed_attachments = []
        should_process_attachments = (
            self.attachment_manager and
            message.attachments and
            (not message.author.bot or self.config.discord.allow_bot_interactions)
        )
        if should_process_attachments:
            for attachment in message.attachments:
                try:
                    result = await self.attachment_manager.process_attachment(
                        attachment=attachment,
                        message=message,
                        is_realtime=True
                    )
                    if result and result.get("for_api"):
                        processed_attachments.append(result)
                        logger.info(f"Processed attachment: {attachment.filename}")
                except Exception as e:
                    logger.error(f"Failed to process attachment {attachment.filename}: {e}", exc_info=True)

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
                # Build context (system prompt only - messages come from conversation state)
                # Exclude messages that are currently being processed to prevent seeing other pending @mentions
                context = await self.context_builder.build_context(
                    message,
                    exclude_message_ids=list(self._responded_messages)
                )

                # Initialize tools for API call
                tools = [{"type": "memory_20250818", "name": "memory"}]

                # Add current user message to conversation state BEFORE API call (v0.5.0)
                if conversation_state:
                    try:
                        # Extract attachment IDs if attachments were processed
                        attachment_ids = [att["attachment_id"] for att in processed_attachments] if processed_attachments else None

                        # Build user message content (same format as API)
                        user_content = message.content
                        if processed_attachments:
                            # Convert to content blocks if attachments present
                            # Bug #24 fix: Use default text if message content is empty to prevent API rejection
                            text_content = message.content if message.content.strip() else "[Attachment]"
                            user_content = [{"type": "text", "text": text_content}]
                            for att in processed_attachments:
                                api_data = att["for_api"]
                                logger.info(f"Processing attachment {att.get('filename')}: api_data={api_data}")
                                if api_data["method"] == "file_id":
                                    file_id = api_data["data"]

                                    # Add document block for reading (if eligible)
                                    if api_data.get("use_as_document_block", True):
                                        user_content.append({
                                            "type": "document",
                                            "source": {
                                                "type": "file",
                                                "file_id": file_id
                                            }
                                        })
                                        logger.info(f"Added document block for {att['filename']} (file_id: {file_id})")
                                    else:
                                        # Code execution files: container_upload block
                                        # Bug #25 fix: Use container_upload instead of text mention
                                        user_content.append({
                                            "type": "container_upload",
                                            "file_id": file_id
                                        })
                                        logger.info(f"Added container_upload block for {att['filename']} (file_id: {file_id})")
                                elif api_data["method"] == "base64":
                                    user_content.append({
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": api_data["media_type"],
                                            "data": api_data["data"]
                                        }
                                    })

                        conversation_state.add_message("user", user_content, attachment_ids)
                        logger.debug(f"Added user message to conversation state (attachments: {len(attachment_ids) if attachment_ids else 0})")

                        # Enforce message cap immediately
                        removed_count = conversation_state.enforce_message_cap()
                        if removed_count > 0:
                            logger.info(f"Message cap enforced: removed {removed_count} oldest messages")

                    except Exception as e:
                        logger.error(f"Failed to add user message to conversation state: {e}", exc_info=True)

                # Log context building stats
                if context.get("stats"):
                    self.conversation_logger.log_context_building(**context["stats"])

                # Show typing indicator
                async with message.channel.typing():
                    # Small delay for more natural feel
                    await asyncio.sleep(1.5)

                    # Build API request parameters
                    # (tools already initialized earlier for context nuke check)

                    # Add Discord tools if enabled (Phase 4)
                    if self.discord_tool_executor:
                        tools.extend(get_discord_tools())
                        logger.debug("Discord tools added to API request")
                    else:
                        logger.warning("Discord tool executor is None - tools NOT added!")

                    # Add web search tools if enabled (all-or-nothing, no rate limits)
                    beta_headers = []
                    if self.web_search_enabled:
                        web_search_config = self.config.get_web_search_config()
                        tools.extend(get_web_search_tools(citations_enabled=web_search_config["citations_enabled"]))
                        logger.debug("Web search tools added to API request (unlimited)")

                    # Add MCP tools if enabled (v0.5.0)
                    if self.mcp_manager:
                        mcp_tools = self.mcp_manager.get_tools_for_api()
                        if mcp_tools:
                            tools.extend(mcp_tools)
                            logger.debug(f"Added {len(mcp_tools)} MCP tools to API request")

                    # Add files API beta if attachments enabled (Bug #8 fix: match periodic check behavior)
                    # Always add when attachment_manager exists, since conversation state may contain file_id references
                    if self.attachment_manager:
                        beta_headers.append("files-api-2025-04-14")
                        logger.debug("Files API beta header added")

                    # Add code execution tool and skills (Bug #14 fix: skills REQUIRE code_execution)
                    # Skills load files into /skills/ directory which is accessed via code_execution tool
                    if self.skills_manager:
                        tools.append({
                            "type": "code_execution_20260120",
                            "name": "code_execution"
                        })
                        # Add request_skill tool for progressive disclosure (v0.5.0)
                        tools.append(get_skill_request_tool())
                        beta_headers.append("skills-2025-10-02")
                        logger.debug("Code execution tool, request_skill tool, and skills beta header added")

                    # Bug #14 fix: Track container.id across tool loop iterations
                    # Per Anthropic docs, multi-turn conversations must reuse container.id
                    container_id = None

                    api_params = {
                        "model": self.config.api.model,
                        "max_tokens": self.config.api.max_tokens,
                        "tools": tools,
                        "betas": beta_headers  # SDK's beta endpoint uses 'betas' parameter, not extra_headers
                    }

                    # Add skills container with progressive disclosure (v0.5.0)
                    # Use active_skills from conversation_state, or defaults if empty
                    if self.skills_manager:
                        # Get active skills from conversation state (progressive disclosure)
                        active_skill_names = []
                        if conversation_state:
                            active_skill_names = conversation_state.get_active_skills()

                        # Use defaults if no skills selected yet
                        if not active_skill_names:
                            default_skills = self.config.skills.default_skills
                            if default_skills:
                                active_skill_names = default_skills
                            else:
                                # Fallback to pdf (most common document skill)
                                active_skill_names = ["pdf"]
                            # Initialize conversation state with defaults
                            if conversation_state:
                                conversation_state.set_active_skills(active_skill_names, self.skills_manager.MAX_SKILLS_PER_REQUEST)

                        # Select skills by name using progressive disclosure
                        skills_list = self.skills_manager.select_skills(active_skill_names)
                        if skills_list:
                            container_config = {"skills": skills_list}
                            if container_id:  # Reuse from previous iteration
                                container_config["id"] = container_id
                            api_params["container"] = container_config
                            logger.debug(f"Container: id={container_id}, skills={active_skill_names}")

                    # Build system prompt
                    system_prompt_text = context["system_prompt"]

                    # Add skills catalog for progressive disclosure (v0.5.0)
                    if self.skills_manager and conversation_state:
                        skills_prompt = self.context_builder.build_skills_prompt(
                            conversation_state.get_active_skills()
                        )
                        if skills_prompt:
                            system_prompt_text += "\n\n" + skills_prompt

                    # Attachment index (v0.6.0 Phase 4)
                    if conversation_state and self.attachment_manager:
                        try:
                            attachments_index = await self._generate_uploaded_files_manifest(conversation_state)
                            if attachments_index:
                                system_prompt_text += "\n\n" + attachments_index
                        except Exception as e:
                            logger.error(f"Failed to generate attachment index: {e}", exc_info=True)

                    # Add system prompt with cache control (prompt caching)
                    api_params["system"] = [
                        {
                            "type": "text",
                            "text": system_prompt_text,
                            "cache_control": {"type": "ephemeral"}
                        }
                    ]

                    # Get messages from conversation state if available, otherwise use context (v0.5.0)
                    if conversation_state:
                        messages = conversation_state.get_messages_for_api()
                        logger.debug(f"Using {len(messages)} messages from conversation state")
                    else:
                        # Fallback to context builder (legacy behavior)
                        messages = context["messages"].copy()
                        logger.warning("Conversation state not available, falling back to context builder")

                    api_params["messages"] = messages

                    # Add adaptive thinking and effort if configured
                    if self.config.api.thinking.enabled:
                        api_params["thinking"] = {"type": "adaptive"}
                    if self.config.api.effort:
                        api_params["output_config"] = {"effort": self.config.api.effort}

                    # Tool use loop - continue until end_turn
                    thinking_text = ""
                    thinking_block = None  # Store full thinking block for persistence
                    response_text = ""
                    loop_iteration = 0

                    while True:
                        loop_iteration += 1

                        # Orphan detection removed - was causing false positives
                        # API will reject actual orphaned tool_results with clear error message

                        # Call Claude API (beta endpoint carries files/skills betas)
                        logger.debug(f"API params: model={api_params.get('model')}, betas={api_params.get('betas')}")
                        logger.debug(f"  Tools: {[t.get('name') or t.get('type') for t in api_params.get('tools', [])]}")
                        if 'container' in api_params:
                            logger.debug(f"  Container/Skills: {api_params.get('container')}")
                        response = await self.anthropic.beta.messages.create(**api_params)

                        # Bug #14 fix: Capture container.id from response for subsequent iterations
                        if hasattr(response, 'container') and response.container and hasattr(response.container, 'id'):
                            container_id = response.container.id
                            logger.debug(f"Captured container.id: {container_id}")

                        # Log tool use loop iteration
                        self.conversation_logger.log_tool_use_loop(loop_iteration, response.stop_reason)

                        # Log server tool usage (web_search, web_fetch)
                        # Server tools appear as server_tool_use blocks in response.content
                        if self.web_search_enabled:
                            logger.debug(f"Response has {len(response.content)} content blocks")
                            for i, block in enumerate(response.content):
                                logger.debug(f"  Block {i}: type={block.type}")

                                if hasattr(block, 'text'):
                                    preview = block.text[:200] if len(block.text) > 200 else block.text
                                    logger.debug(f"    Text preview: {preview}")

                                if block.type == "container_upload":
                                    file_id = getattr(block, 'file_id', 'unknown')
                                    filename = getattr(block, 'filename', 'unknown')
                                    logger.debug(f"    container_upload: file_id={file_id}, filename={filename}")

                                if block.type == "web_fetch_tool_result":
                                    if hasattr(block, 'content') and hasattr(block.content, 'content'):
                                        if hasattr(block.content.content, 'source') and hasattr(block.content.content.source, 'data'):
                                            fetch_text = block.content.content.source.data[:500]
                                            logger.debug(f"    web_fetch content: {fetch_text}")

                                if hasattr(block, 'type') and block.type == "server_tool_use":
                                    if hasattr(block, 'name'):
                                        logger.info(f"Server tool used: {block.name}")
                                        logger.debug(f"  Server tool {block.name} input: {block.input}")

                        # Log input token usage (first iteration only) - from response.usage, no count_tokens calls
                        if loop_iteration == 1 and hasattr(response, 'usage'):
                            logger.info(f"Input tokens: {total_input_tokens(response.usage):,} "
                                        f"(uncached: {response.usage.input_tokens:,})")

                        # Extract thinking if present (store full block for persistence)
                        for block in response.content:
                            if block.type == "thinking":
                                thinking_text += block.thinking
                                thinking_block = block  # Store full block with signature

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

                                        # Execute memory command with context for data isolation
                                        result = self.memory_tool_executor.execute(
                                            block.input,
                                            current_server_id=str(message.guild.id) if message.guild else None,
                                            current_channel_id=str(message.channel.id)
                                        )

                                        # Log memory tool operation
                                        self.conversation_logger.log_memory_tool(command, path, result)

                                        tool_results.append({
                                            "type": "tool_result",
                                            "tool_use_id": block.id,
                                            "content": result
                                        })

                                    # Handle request_skill tool (v0.5.0 Progressive Disclosure)
                                    elif block.name == "request_skill":
                                        skill_name = block.input.get('skill_name', 'unknown')
                                        logger.info(f"Executing request_skill tool: {skill_name}")

                                        if conversation_state:
                                            result = self.skill_request_executor.execute(
                                                block.input,
                                                conversation_state
                                            )
                                            # Save updated state with new skills
                                            await self.conversation_state_manager.save(conversation_state)
                                            logger.info(f"Skill request processed: {result}")
                                        else:
                                            result = "Error: Conversation state not available"

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

                                            # Execute Discord tool with context for data isolation
                                            result = await self.discord_tool_executor.execute(
                                                block.input,
                                                current_server_id=str(message.guild.id) if message.guild else None,
                                                current_channel_id=str(message.channel.id)
                                            )

                                            # Check if result is structured data from get_attachment
                                            if isinstance(result, dict) and result.get("_structured"):
                                                # Build content blocks array with text + file
                                                content_blocks = [
                                                    {"type": "text", "text": result["text"]}
                                                ]

                                                # Add file content block
                                                file_data = result["file_data"]
                                                if file_data.get("method") == "base64":
                                                    # Image: ephemeral processing
                                                    content_blocks.append({
                                                        "type": "image",
                                                        "source": {
                                                            "type": "base64",
                                                            "media_type": file_data["media_type"],
                                                            "data": file_data["data"]
                                                        }
                                                    })
                                                elif file_data.get("method") == "file_id":
                                                    # Phase 3: Detect whether to use document block or container_upload
                                                    # Design: document blocks for PDFs/plaintext (Claude reads directly)
                                                    #         container_upload for other file types (accessed via code execution tool)
                                                    use_as_document = file_data.get("use_as_document_block", True)

                                                    if use_as_document:
                                                        # Document: add to rolling context (PDFs, TXT, CSV, etc.)
                                                        content_blocks.append({
                                                            "type": "document",
                                                            "source": {
                                                                "type": "file",
                                                                "file_id": file_data["data"]
                                                            }
                                                        })
                                                    else:
                                                        # Container upload: add to rolling context (any non-document-eligible files)
                                                        # These files are accessed via code_execution tool, not read directly
                                                        content_blocks.append({
                                                            "type": "container_upload",
                                                            "file_id": file_data["data"]
                                                        })

                                                    # Add to conversation state for persistence
                                                    att_id = result["metadata"]["attachment_id"]
                                                    conversation_state.add_message(
                                                        "user",
                                                        content_blocks,
                                                        [att_id]
                                                    )
                                                    logger.info(f"Added fetched attachment to conversation state: {result['metadata']['filename']}")

                                                tool_results.append({
                                                    "type": "tool_result",
                                                    "tool_use_id": block.id,
                                                    "content": content_blocks
                                                })
                                            else:
                                                # Regular text result
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

                            # Persist tool use and results to conversation state (v0.5.0 Phase 1)
                            if conversation_state:
                                try:
                                    # Convert ContentBlock objects to dicts for serialization
                                    assistant_content_dicts = []
                                    for block in response.content:
                                        if hasattr(block, 'type'):
                                            if block.type == "text":
                                                assistant_content_dicts.append({"type": "text", "text": block.text})
                                            elif block.type == "thinking":
                                                # Preserve thinking blocks with signature for tool continuity
                                                assistant_content_dicts.append({
                                                    "type": "thinking",
                                                    "thinking": block.thinking,
                                                    "signature": block.signature
                                                })
                                            elif block.type == "redacted_thinking":
                                                # Preserve redacted blocks if API returns them (rare)
                                                assistant_content_dicts.append({
                                                    "type": "redacted_thinking",
                                                    "data": block.data
                                                })
                                            elif block.type == "tool_use":
                                                assistant_content_dicts.append({
                                                    "type": "tool_use",
                                                    "id": block.id,
                                                    "name": block.name,
                                                    "input": block.input
                                                })
                                            # Bug #9 fix: Capture server-side tool uses (code_execution, web_search, etc.)
                                            # Bug #10 fix: Also capture bash_code_execution, text_editor_code_execution tool uses
                                            elif block.type in ("server_tool_use", "bash_code_execution", "text_editor_code_execution"):
                                                assistant_content_dicts.append({
                                                    "type": block.type,  # Bug #10: Preserve original type
                                                    "id": getattr(block, 'id', 'unknown'),
                                                    "name": getattr(block, 'name', 'unknown'),
                                                    "input": getattr(block, 'input', {})
                                                })
                                                logger.debug(f"Captured {block.type} for persistence: {getattr(block, 'name', 'unknown')}")
                                            # Bug #9 fix: Capture server tool results (code execution output, web search results)
                                            # Bug #10 fix: Added bash_code_execution_tool_result, text_editor_code_execution_tool_result
                                            elif block.type in ("code_execution_result", "bash_code_execution_tool_result", "text_editor_code_execution_tool_result", "web_search_tool_result", "web_fetch_tool_result"):
                                                # Store the result block for conversation history
                                                # Bug #12 fix: Include tool_use_id which is required by token counting API
                                                result_content = getattr(block, 'content', None) or getattr(block, 'text', 'No content')
                                                result_block = {
                                                    "type": block.type,
                                                    "content": str(result_content)[:5000]  # Truncate very long results
                                                }
                                                # Server tool results have tool_use_id referencing the tool call
                                                if hasattr(block, 'tool_use_id'):
                                                    result_block["tool_use_id"] = block.tool_use_id
                                                assistant_content_dicts.append(result_block)
                                                logger.debug(f"Captured {block.type} for persistence (tool_use_id: {getattr(block, 'tool_use_id', 'N/A')})")
                                        elif isinstance(block, dict):
                                            assistant_content_dicts.append(block)

                                    conversation_state.add_tool_use_and_results(
                                        assistant_content=assistant_content_dicts,
                                        tool_results=tool_results
                                    )
                                    # Save state after adding tool results
                                    await self.conversation_state_manager.save(conversation_state)
                                    logger.info("Persisted tool use and results to conversation state")

                                    # Enforce message cap after adding tool results to prevent accumulation
                                    removed_count = conversation_state.enforce_message_cap()
                                    if removed_count > 0:
                                        logger.info(f"Message cap enforced during tool loop: removed {removed_count} messages (now {len(conversation_state.messages)}/{self.config.api.context_messages})")
                                        # Re-save state after cap enforcement
                                        await self.conversation_state_manager.save(conversation_state)

                                        # Rebuild api_params["messages"] to stay in sync with conversation_state
                                        # after message cap removed Discord messages (prevents orphaned tool_results)
                                        api_params["messages"] = conversation_state.get_messages_for_api()
                                        logger.debug(f"Rebuilt api_params messages from conversation state after cap enforcement")
                                except Exception as e:
                                    logger.error(f"Failed to persist tool results to conversation state: {e}", exc_info=True)

                            # Bug #14 fix: Update container with captured id before next iteration
                            if container_id and self.skills_manager and 'container' in api_params:
                                api_params["container"]["id"] = container_id
                                logger.debug(f"Updated container with id for next iteration: {container_id}")

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

            # Add assistant response to conversation state (v0.5.0)
            if conversation_state:
                try:
                    # Construct content blocks array (thinking + text)
                    assistant_content = []
                    if thinking_block:
                        # Preserve thinking block with signature for next turn
                        assistant_content.append({
                            "type": "thinking",
                            "thinking": thinking_block.thinking,
                            "signature": thinking_block.signature
                        })
                    if response_text:
                        assistant_content.append({
                            "type": "text",
                            "text": response_text
                        })

                    # Add assistant response to state
                    conversation_state.add_message("assistant", assistant_content)
                    logger.debug("Added assistant response to conversation state (with thinking block)")

                    # Enforce message cap after adding assistant response
                    removed_count = conversation_state.enforce_message_cap()
                    if removed_count > 0:
                        logger.info(f"Message cap enforced after response: removed {removed_count} oldest messages")

                    # Record session usage watermark and stub old tool results (v0.6.0)
                    if hasattr(response, "usage"):
                        conversation_state.record_usage(total_input_tokens(response.usage))
                    conversation_state.stub_old_tool_results(keep_turns=TOOL_STUB_KEEP_TURNS)

                    # Save conversation state to database
                    await self.conversation_state_manager.save(conversation_state)
                    logger.debug(f"Saved conversation state: {conversation_state}")

                    # Session over usage threshold -> close the episode in the background
                    if (self.episode_manager
                            and conversation_state.session_input_tokens > self.config.api.context_tokens):
                        logger.info(
                            f"Session usage {conversation_state.session_input_tokens:,} over threshold "
                            f"{self.config.api.context_tokens:,} - episodizing channel {channel_id}"
                        )
                        task = asyncio.create_task(
                            self.episode_manager.episodize_channel(channel_id, force=True)
                        )
                        self._background_tasks.add(task)
                        task.add_done_callback(self._background_tasks.discard)

                except Exception as e:
                    logger.error(f"Failed to update conversation state: {e}", exc_info=True)

            # Record response and start engagement tracking
            self.rate_limiter.record_response(channel_id)

            # Log engagement tracking start
            self.conversation_logger.log_engagement_tracking(
                started=True,
                delay_seconds=self._rate_limiting_config["engagement_tracking_delay"]
            )
            self.conversation_logger.log_separator()

            # Create and track background task
            task = asyncio.create_task(
                self._track_engagement(
                    sent_message.id,
                    message.channel,
                    original_author_id=message.author.id,
                    delay=self._rate_limiting_config["engagement_tracking_delay"],
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

        # Cleanup v0.5.0 managers
        if self.mcp_manager:
            try:
                await self.mcp_manager.shutdown()
                logger.info("MCP manager shut down successfully")
            except Exception as e:
                logger.error(f"Error shutting down MCP manager: {e}")

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
        Periodic check loop - scans pending messages for response opportunities.

        Bug #7 fix (revised): Two-phase processing
        1. Process all pending messages (add to conversation state)
        2. Make one response decision per channel

        Runs every check_interval_seconds (default 30s).
        """
        try:
            while self._running:
                await asyncio.sleep(self.config.reactive.check_interval_seconds)

                # Episode idle sweep (v0.6.0) - cheap; ~every 10 min
                self._episode_sweep_counter = getattr(self, "_episode_sweep_counter", 0) + 1
                sweep_every = max(1, 600 // max(1, self.config.reactive.check_interval_seconds))
                if self.episode_manager and self._episode_sweep_counter % sweep_every == 0:
                    task = asyncio.create_task(self.episode_manager.check_idle_channels())
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)

                if not self.pending_messages:
                    continue

                # Get copy of pending messages to process
                messages_to_check = self.pending_messages.copy()
                self.pending_messages.clear()

                logger.debug(f"Processing {len(messages_to_check)} pending messages")

                # PHASE 1: Process all messages (add to conversation state)
                # Track which channels received updates
                channels_with_updates = set()
                for channel_id, message_id in messages_to_check:
                    try:
                        if await self._process_message_to_state(channel_id, message_id):
                            channels_with_updates.add(channel_id)
                    except Exception as e:
                        logger.error(f"Error processing message {message_id} in channel {channel_id}: {e}", exc_info=True)

                logger.debug(f"Phase 1 complete: {len(channels_with_updates)} channels with updates")

                # PHASE 2: Make one response decision per channel
                for channel_id in channels_with_updates:
                    try:
                        await self._decide_channel_response(channel_id)
                    except Exception as e:
                        logger.error(f"Error deciding response for channel {channel_id}: {e}", exc_info=True)

        except asyncio.CancelledError:
            logger.info("Periodic check loop cancelled")
        except Exception as e:
            logger.error(f"Fatal error in periodic check loop: {e}", exc_info=True)

    async def _process_message_to_state(self, channel_id: str, message_id: int) -> bool:
        """
        Process a specific message and add it to conversation state.

        Bug #7 fix (revised): Phase 1 processing - just add message to state, no response decision.

        Args:
            channel_id: Discord channel ID
            message_id: Specific Discord message ID to process

        Returns:
            True if message was successfully processed, False otherwise
        """
        if not self.discord_client:
            logger.warning("Discord client not set, cannot process message")
            return False

        # Get Discord channel object
        channel = self.discord_client.get_channel(int(channel_id))
        if not channel:
            logger.warning(f"Channel {channel_id} not found")
            return False

        # Fetch the specific message by ID
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            logger.warning(f"Message {message_id} not found (may have been deleted)")
            return False
        except discord.Forbidden:
            logger.warning(f"No permission to fetch message {message_id}")
            return False
        except Exception as e:
            logger.error(f"Error fetching message {message_id}: {e}", exc_info=True)
            return False

        # Skip if message is from the bot
        if message.author == self.discord_client.user:
            logger.debug(f"Message {message_id} is from bot, skipping processing")
            return False

        # Load or create conversation state for this channel
        if not self.conversation_state_manager:
            logger.warning("No conversation state manager, cannot process message")
            return False

        try:
            conversation_state = await self.conversation_state_manager.get_or_create(channel_id)

            # If state is empty, initialize with recent DB messages
            if len(conversation_state.messages) == 0:
                await self._initialize_conversation_from_db(channel_id, conversation_state)

            # Process attachments if present
            processed_attachments = []
            should_process_attachments = (
                self.attachment_manager and
                message.attachments and
                (not message.author.bot or self.config.discord.allow_bot_interactions)
            )
            if should_process_attachments:
                for attachment in message.attachments:
                    try:
                        result = await self.attachment_manager.process_attachment(
                            attachment=attachment,
                            message=message,
                            is_realtime=True
                        )
                        if result and result.get("for_api"):
                            processed_attachments.append(result)
                            logger.info(f"Processed attachment (periodic): {attachment.filename}")
                    except Exception as e:
                        logger.error(f"Failed to process attachment {attachment.filename}: {e}", exc_info=True)

            # Build message content with attachments
            user_content = message.content
            attachment_ids = None
            if processed_attachments:
                text_content = message.content if message.content.strip() else "[Attachment]"
                user_content = [{"type": "text", "text": text_content}]
                attachment_ids = []
                for att in processed_attachments:
                    attachment_ids.append(att["attachment_id"])
                    api_data = att["for_api"]
                    if api_data["method"] == "file_id":
                        file_id = api_data["data"]
                        if api_data.get("use_as_document_block", True):
                            user_content.append({
                                "type": "document",
                                "source": {"type": "file", "file_id": file_id}
                            })
                            logger.info(f"Added document block for {att['filename']}")
                        else:
                            user_content.append({
                                "type": "container_upload",
                                "file_id": file_id
                            })
                            logger.info(f"Added container_upload block for {att['filename']}")
                    elif api_data["method"] == "base64":
                        user_content.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": api_data["media_type"],
                                "data": api_data["data"]
                            }
                        })
                        logger.info(f"Added image block for {att['filename']}")

            # Add message to conversation state
            conversation_state.add_message("user", user_content, attachment_ids)
            logger.info(f"Added message {message_id} to conversation state (attachments: {len(attachment_ids) if attachment_ids else 0})")

            # Save state immediately (Bug #7 fix for persistence)
            await self.conversation_state_manager.save(conversation_state)
            logger.debug(f"Saved conversation state after processing message {message_id}")

            return True

        except Exception as e:
            logger.error(f"Failed to process message {message_id} to state: {e}", exc_info=True)
            return False

    async def _decide_channel_response(self, channel_id: str):
        """
        Make response decision for a channel (Phase 2 of periodic check).

        Bug #7 fix (revised): Called after all messages processed to make ONE decision per channel.

        Args:
            channel_id: Discord channel ID
        """
        if not self.discord_client:
            logger.warning("Discord client not set, cannot decide response")
            return

        # Get Discord channel object
        channel = self.discord_client.get_channel(int(channel_id))
        if not channel:
            logger.warning(f"Channel {channel_id} not found")
            return

        # Fetch latest message for context building
        try:
            latest_message = None
            async for msg in channel.history(limit=1):
                latest_message = msg
                break

            if not latest_message:
                logger.debug(f"No messages found in channel {channel_id}")
                return

            # Don't respond if latest message is from the bot
            if latest_message.author == self.discord_client.user:
                logger.debug(f"Latest message in {channel_id} is from bot, skipping")
                return

            # Don't respond if we already responded to this message
            if latest_message.id in self._responded_messages:
                logger.debug(f"Already responded to latest message {latest_message.id}, skipping")
                return

        except Exception as e:
            logger.error(f"Error fetching latest message in channel {channel_id}: {e}", exc_info=True)
            return

        # Detect if latest message is an @mention of this bot
        # Check both message.mentions and content string (bot-to-bot mentions
        # may not populate message.mentions in discord.py)
        is_mention = False
        if self.discord_client.user in latest_message.mentions:
            is_mention = True
        elif self.discord_client.user and f"<@{self.discord_client.user.id}>" in (latest_message.content or ""):
            is_mention = True

        # Check rate limits (mentions bypass silence threshold)
        can_respond, reason = self.rate_limiter.can_respond(channel_id, is_mention=is_mention)
        if not can_respond:
            logger.debug(f"Channel {channel_id} rate limited: {reason}")
            return

        # Build context using the latest Discord message
        context = await self.context_builder.build_context(latest_message)

        # Calculate conversation momentum
        momentum = await self._calculate_conversation_momentum(channel_id)
        context["conversation_momentum"] = momentum

        # Call Claude API with response decision focus
        # Note: Message already in conversation state from Phase 1 processing
        await self._call_claude_for_response_decision(latest_message, context)

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

        # Load or create conversation state for this channel (v0.5.0)
        conversation_state = None
        if self.conversation_state_manager:
            try:
                conversation_state = await self.conversation_state_manager.get_or_create(channel_id)
                logger.debug(f"Loaded conversation state for periodic check: {conversation_state}")

                # If state is empty, initialize with recent DB messages (Disconnect #1 fix)
                if len(conversation_state.messages) == 0:
                    await self._initialize_conversation_from_db(channel_id, conversation_state)

                # Bug #7 fix (revised): Attachment processing removed - now done in Phase 1
                # Messages are already in conversation state with attachments from _process_message_to_state()

            except Exception as e:
                logger.error(f"Failed to load conversation state for periodic check: {e}", exc_info=True)
                conversation_state = None

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

            # Add skills catalog for progressive disclosure (v0.5.0)
            if self.skills_manager and conversation_state:
                skills_prompt = self.context_builder.build_skills_prompt(
                    conversation_state.get_active_skills()
                )
                if skills_prompt and isinstance(system_prompt, list) and len(system_prompt) > 0:
                    # Inject skills catalog into the system prompt text
                    system_prompt[0]["text"] += "\n\n" + skills_prompt

            # Attachment index (v0.6.0 Phase 4) - this path handles bot-posted
            # files too, so it needs the same retrieval awareness
            if conversation_state and self.attachment_manager:
                try:
                    attachments_index = await self._generate_uploaded_files_manifest(conversation_state)
                    if attachments_index and isinstance(system_prompt, list) and len(system_prompt) > 0:
                        system_prompt[0]["text"] += "\n\n" + attachments_index
                except Exception as e:
                    logger.error(f"Failed to generate attachment index: {e}", exc_info=True)

            # Prepare API parameters
            tools = [{"type": "memory_20250818", "name": "memory"}]

            # Add Discord tools if enabled (Phase 4)
            if self.discord_tool_executor:
                tools.extend(get_discord_tools())
                logger.debug("Discord tools added to periodic check")
            else:
                logger.warning("Discord tool executor is None - tools NOT added to periodic check!")

            # Add web search tools if enabled (all-or-nothing, no rate limits)
            beta_headers = []
            if self.web_search_enabled:
                web_search_config = self.config.get_web_search_config()
                tools.extend(get_web_search_tools(citations_enabled=web_search_config["citations_enabled"]))
                logger.debug("Web search tools added to periodic check (unlimited)")

            # Add MCP tools if enabled (v0.5.0)
            if self.mcp_manager:
                mcp_tools = self.mcp_manager.get_tools_for_api()
                if mcp_tools:
                    tools.extend(mcp_tools)
                    logger.debug(f"Added {len(mcp_tools)} MCP tools to periodic check")

            # Add code execution tool and skills (Bug #14 fix: skills REQUIRE code_execution)
            if self.skills_manager:
                tools.append({
                    "type": "code_execution_20260120",
                    "name": "code_execution"
                })
                # Add request_skill tool for progressive disclosure (v0.5.0)
                tools.append(get_skill_request_tool())
                beta_headers.append("skills-2025-10-02")
                logger.debug("Code execution, request_skill, and skills beta header added to periodic check")

            # Add files API beta if attachments enabled
            if self.attachment_manager:
                beta_headers.append("files-api-2025-04-14")

            # Get messages from conversation state if available, otherwise use context (v0.5.0)
            if conversation_state:
                messages = conversation_state.get_messages_for_api()
                logger.debug(f"Using {len(messages)} messages from conversation state (periodic)")
            else:
                # Fallback to context builder (legacy behavior)
                messages = context["messages"].copy()
                logger.warning("Conversation state not available for periodic check, falling back to context builder")

            # Bug #14 fix: Track container.id across tool loop iterations (periodic path)
            container_id = None

            api_params = {
                "model": self.config.api.model,
                "max_tokens": self.config.api.max_tokens,
                "system": system_prompt,
                "messages": messages,
                "tools": tools,
                "betas": beta_headers  # SDK's beta endpoint uses 'betas' parameter, not extra_headers
            }

            # Add skills container with progressive disclosure (v0.5.0)
            if self.skills_manager:
                # Get active skills from conversation state (progressive disclosure)
                active_skill_names = []
                if conversation_state:
                    active_skill_names = conversation_state.get_active_skills()

                # Use defaults if no skills selected yet
                if not active_skill_names:
                    default_skills = self.config.skills.default_skills
                    if default_skills:
                        active_skill_names = default_skills
                    else:
                        active_skill_names = ["pdf"]
                    if conversation_state:
                        conversation_state.set_active_skills(active_skill_names, self.skills_manager.MAX_SKILLS_PER_REQUEST)

                # Select skills by name using progressive disclosure
                skills_list = self.skills_manager.select_skills(active_skill_names)
                if skills_list:
                    container_config = {"skills": skills_list}
                    if container_id:  # Reuse from previous iteration
                        container_config["id"] = container_id
                    api_params["container"] = container_config
                    logger.debug(f"Container (periodic): id={container_id}, skills={active_skill_names}")

            # Add adaptive thinking and effort if configured
            if self.config.api.thinking.enabled:
                api_params["thinking"] = {"type": "adaptive"}
            if self.config.api.effort:
                api_params["output_config"] = {"effort": self.config.api.effort}

            # Call Claude API
            try:
                # Initialize response tracking
                thinking_text = ""
                thinking_block = None  # Store full thinking block for persistence
                response_text = ""
                loop_iteration = 0
                tools_were_used = False  # Track if any tools were executed (Bug #7 fix)

                while True:
                    loop_iteration += 1

                    # Orphan detection removed - was causing false positives
                    # API will reject actual orphaned tool_results with clear error message

                    # Call API (beta endpoint carries files/skills betas)
                    logger.debug(f"API params (periodic): model={api_params.get('model')}, betas={api_params.get('betas')}")
                    logger.debug(f"  Tools: {[t.get('name') or t.get('type') for t in api_params.get('tools', [])]}")
                    if 'container' in api_params:
                        logger.debug(f"  Container/Skills: {api_params.get('container')}")
                    response = await self.anthropic.beta.messages.create(**api_params)

                    # Bug #14 fix: Capture container.id from response for subsequent iterations (periodic path)
                    if hasattr(response, 'container') and response.container and hasattr(response.container, 'id'):
                        container_id = response.container.id
                        logger.debug(f"Captured container.id (periodic): {container_id}")

                    # Log server tool usage (web_search, web_fetch)
                    if self.web_search_enabled:
                        logger.debug(f"Response has {len(response.content)} content blocks (periodic)")
                        for i, block in enumerate(response.content):
                            logger.debug(f"  Block {i}: type={block.type}")

                            if hasattr(block, 'text'):
                                preview = block.text[:200] if len(block.text) > 200 else block.text
                                logger.debug(f"    Text preview: {preview}")

                            if block.type == "container_upload":
                                file_id = getattr(block, 'file_id', 'unknown')
                                filename = getattr(block, 'filename', 'unknown')
                                logger.debug(f"    container_upload: file_id={file_id}, filename={filename}")

                            if block.type == "web_fetch_tool_result":
                                if hasattr(block, 'content') and hasattr(block.content, 'content'):
                                    if hasattr(block.content.content, 'source') and hasattr(block.content.content.source, 'data'):
                                        fetch_text = block.content.content.source.data[:500]
                                        logger.debug(f"    web_fetch content: {fetch_text}")

                            if hasattr(block, 'type') and block.type == "server_tool_use":
                                if hasattr(block, 'name'):
                                    logger.info(f"Server tool used (periodic): {block.name}")
                                    logger.debug(f"  Server tool {block.name} input: {block.input}")

                    # Log input token usage (first iteration only) - from response.usage, no count_tokens calls
                    if loop_iteration == 1 and hasattr(response, 'usage'):
                        logger.info(f"Input tokens: {total_input_tokens(response.usage):,} "
                                    f"(uncached: {response.usage.input_tokens:,})")

                    # Extract thinking if present (store full block for persistence)
                    for block in response.content:
                        if block.type == "thinking":
                            thinking_text += block.thinking
                            thinking_block = block  # Store full block with signature

                    # Check stop reason
                    if response.stop_reason == "tool_use":
                        # Execute tool calls
                        tools_were_used = True  # Track that tools were executed (Bug #7 fix)
                        tool_results = []
                        for content_block in response.content:
                            if content_block.type == "tool_use":
                                # Handle memory tool
                                if content_block.name == "memory":
                                    result = self.memory_tool_executor.execute(
                                        content_block.input,
                                        current_server_id=str(message.guild.id) if message.guild else None,
                                        current_channel_id=channel_id
                                    )
                                    tool_results.append({
                                        "type": "tool_result",
                                        "tool_use_id": content_block.id,
                                        "content": result
                                    })

                                # Handle request_skill tool (v0.5.0 Progressive Disclosure)
                                elif content_block.name == "request_skill":
                                    skill_name = content_block.input.get('skill_name', 'unknown')
                                    logger.info(f"Executing request_skill tool (periodic): {skill_name}")

                                    if conversation_state:
                                        result = self.skill_request_executor.execute(
                                            content_block.input,
                                            conversation_state
                                        )
                                        # Save updated state with new skills
                                        await self.conversation_state_manager.save(conversation_state)
                                    else:
                                        result = "Error: Conversation state not available"

                                    tool_results.append({
                                        "type": "tool_result",
                                        "tool_use_id": content_block.id,
                                        "content": result
                                    })

                                # Handle Discord tools (Phase 4)
                                elif content_block.name == "discord_tools":
                                    if self.discord_tool_executor:
                                        result = await self.discord_tool_executor.execute(
                                            content_block.input,
                                            current_server_id=str(message.guild.id) if message.guild else None,
                                            current_channel_id=channel_id
                                        )
                                        tool_results.append({
                                            "type": "tool_result",
                                            "tool_use_id": content_block.id,
                                            "content": result
                                        })

                                # Handle MCP tools (v0.5.0)
                                elif "_" in content_block.name and self.mcp_manager:
                                    logger.debug(f"Executing MCP tool (periodic): {content_block.name}")
                                    try:
                                        result = await self.mcp_manager.execute_tool(
                                            tool_name=content_block.name,
                                            arguments=content_block.input
                                        )
                                        tool_results.append({
                                            "type": "tool_result",
                                            "tool_use_id": content_block.id,
                                            "content": str(result)
                                        })
                                    except Exception as e:
                                        logger.error(f"MCP tool execution failed (periodic): {e}", exc_info=True)
                                        tool_results.append({
                                            "type": "tool_result",
                                            "tool_use_id": content_block.id,
                                            "content": f"Error executing MCP tool: {str(e)}",
                                            "is_error": True
                                        })

                        # Continue conversation with tool results
                        api_params["messages"].append({"role": "assistant", "content": response.content})
                        api_params["messages"].append({"role": "user", "content": tool_results})

                        # Persist tool use and results to conversation state (periodic path)
                        try:
                            # Convert response.content to assistant_content_dicts
                            assistant_content_dicts = []
                            for block in response.content:
                                if hasattr(block, 'type'):
                                    if block.type == "text":
                                        assistant_content_dicts.append({"type": "text", "text": block.text})
                                    elif block.type == "thinking":
                                        # Preserve thinking blocks with signature for tool continuity
                                        assistant_content_dicts.append({
                                            "type": "thinking",
                                            "thinking": block.thinking,
                                            "signature": block.signature
                                        })
                                    elif block.type == "redacted_thinking":
                                        # Preserve redacted blocks if API returns them (rare)
                                        assistant_content_dicts.append({
                                            "type": "redacted_thinking",
                                            "data": block.data
                                        })
                                    elif block.type == "tool_use":
                                        assistant_content_dicts.append({
                                            "type": "tool_use",
                                            "id": block.id,
                                            "name": block.name,
                                            "input": block.input
                                        })
                                    # Bug #9 fix: Capture server-side tool uses (code_execution, web_search, etc.)
                                    # Bug #10 fix: Also capture bash_code_execution, text_editor_code_execution tool uses
                                    elif block.type in ("server_tool_use", "bash_code_execution", "text_editor_code_execution"):
                                        assistant_content_dicts.append({
                                            "type": block.type,  # Preserve original type
                                            "id": getattr(block, 'id', 'unknown'),
                                            "name": getattr(block, 'name', 'unknown'),
                                            "input": getattr(block, 'input', {})
                                        })
                                        logger.debug(f"Captured {block.type} for persistence (periodic): {getattr(block, 'name', 'unknown')}")
                                    # Bug #9 fix: Capture server tool results (code execution output, web search results)
                                    # Bug #10 fix: Added bash_code_execution_tool_result, text_editor_code_execution_tool_result
                                    elif block.type in ("code_execution_result", "bash_code_execution_tool_result", "text_editor_code_execution_tool_result", "web_search_tool_result", "web_fetch_tool_result"):
                                        # Store the result block for conversation history
                                        # Bug #12 fix: Include tool_use_id which is required by token counting API
                                        result_content = getattr(block, 'content', None) or getattr(block, 'text', 'No content')
                                        result_block = {
                                            "type": block.type,
                                            "content": str(result_content)[:5000]  # Truncate very long results
                                        }
                                        # Server tool results have tool_use_id referencing the tool call
                                        if hasattr(block, 'tool_use_id'):
                                            result_block["tool_use_id"] = block.tool_use_id
                                        assistant_content_dicts.append(result_block)
                                        logger.debug(f"Captured {block.type} for persistence (periodic) (tool_use_id: {getattr(block, 'tool_use_id', 'N/A')})")
                                elif isinstance(block, dict):
                                    assistant_content_dicts.append(block)

                            conversation_state.add_tool_use_and_results(
                                assistant_content=assistant_content_dicts,
                                tool_results=tool_results
                            )
                            # Save state after adding tool results
                            await self.conversation_state_manager.save(conversation_state)
                            logger.info("Persisted tool use and results to conversation state (periodic)")

                            # Enforce message cap after adding tool results to prevent accumulation
                            removed_count = conversation_state.enforce_message_cap()
                            if removed_count > 0:
                                logger.info(f"Message cap enforced during tool loop (periodic): removed {removed_count} messages (now {len(conversation_state.messages)}/{self.config.api.context_messages})")
                                # Re-save state after cap enforcement
                                await self.conversation_state_manager.save(conversation_state)

                                # Rebuild api_params["messages"] to stay in sync with conversation_state
                                # after message cap removed Discord messages (prevents orphaned tool_results)
                                api_params["messages"] = conversation_state.get_messages_for_api()
                                logger.debug(f"Rebuilt api_params messages from conversation state after cap enforcement (periodic)")
                        except Exception as e:
                            logger.error(f"Failed to persist tool results to conversation state (periodic): {e}", exc_info=True)

                        # Bug #14 fix: Update container with captured id before next iteration (periodic path)
                        if container_id and self.skills_manager and 'container' in api_params:
                            api_params["container"]["id"] = container_id
                            logger.debug(f"Updated container with id for next iteration (periodic): {container_id}")

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

                        # Bug #7 fix: If tools were used but no text response, prompt Claude to confirm
                        if not response_text.strip() and tools_were_used and loop_iteration < 10:
                            logger.warning(f"Tools used but no text response in periodic check - prompting for confirmation (iteration {loop_iteration})")
                            api_params["messages"].append({"role": "assistant", "content": response.content})
                            api_params["messages"].append({
                                "role": "user",
                                "content": [{"type": "text", "text": "Please provide a brief response confirming what you just did for the user."}]
                            })
                            continue  # Loop back to get text response

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

                    # Add assistant response to conversation state (v0.5.0)
                    if conversation_state:
                        try:
                            # Construct content blocks array (thinking + text)
                            assistant_content = []
                            if thinking_block:
                                # Preserve thinking block with signature for next turn
                                assistant_content.append({
                                    "type": "thinking",
                                    "thinking": thinking_block.thinking,
                                    "signature": thinking_block.signature
                                })
                            if response_text:
                                assistant_content.append({
                                    "type": "text",
                                    "text": response_text
                                })

                            # Add assistant response to state
                            conversation_state.add_message("assistant", assistant_content)
                            logger.debug("Added assistant response to conversation state (with thinking block)")

                            # Enforce message cap after adding assistant response
                            removed_count = conversation_state.enforce_message_cap()
                            if removed_count > 0:
                                logger.info(f"Message cap enforced in periodic response: removed {removed_count} oldest messages")

                            # Record session usage watermark and stub old tool results (v0.6.0)
                            if hasattr(response, "usage"):
                                conversation_state.record_usage(total_input_tokens(response.usage))
                            conversation_state.stub_old_tool_results(keep_turns=TOOL_STUB_KEEP_TURNS)

                            # Save conversation state to database
                            await self.conversation_state_manager.save(conversation_state)
                            logger.debug(f"Saved conversation state: {conversation_state}")

                            # Session over usage threshold -> close the episode in the background
                            if (self.episode_manager
                                    and conversation_state.session_input_tokens > self.config.api.context_tokens):
                                logger.info(
                                    f"Session usage {conversation_state.session_input_tokens:,} over threshold "
                                    f"{self.config.api.context_tokens:,} - episodizing channel {channel_id}"
                                )
                                task = asyncio.create_task(
                                    self.episode_manager.episodize_channel(channel_id, force=True)
                                )
                                self._background_tasks.add(task)
                                task.add_done_callback(self._background_tasks.discard)

                        except Exception as e:
                            logger.error(f"Failed to update conversation state: {e}", exc_info=True)

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
                        delay_seconds=self._rate_limiting_config["engagement_tracking_delay"]
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
                    # Log at INFO level for visibility (Bug #7 fix)
                    if tools_were_used:
                        logger.warning(f"Claude used tools but returned no text response in channel {channel_id}")
                    else:
                        logger.info(f"Claude decided not to respond in channel {channel_id}")
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

        # Qualitative momentum descriptions (generic for all bots - v0.5.0 Phase 9)
        momentum_descriptions = {
            "cold": "COLD: Conversation is idle or slow - minimal activity, quiet day",
            "warm": "WARM: Conversation has steady movement - moderate discussion happening",
            "hot": "HOT: Conversation is active and lively - messages flowing rapidly"
        }

        decision_criteria = f"""

# Response Decision Criteria (Periodic Check)

You are monitoring an ongoing Discord conversation. Decide whether to participate based on:

**Direct Triggers** (Always consider responding):
- Your name "{bot_name}" mentioned (even without @)
- Question you can answer
- Topic within your expertise
- Someone needs help you can provide
- Conversation about technical topics you understand

**Conversation Momentum**:
{momentum_descriptions[context["conversation_momentum"]]}

Your personality and base prompt guide whether to participate. Consider relevance, value-add, and whether the activity level matches when you'd naturally engage.

**DON'T Respond If**:
- Nothing meaningful to add
- Conversation doesn't need your input
- Would interrupt natural flow
- Just agreeing without adding value
- Making conversation about yourself

**Current Context**: {context["conversation_momentum"].upper()}

**RESPONSE FORMAT:**
- If you decide to respond: Output ONLY your message to the channel (no meta-commentary, no explanation of your decision)
- If you decide NOT to respond: Output ABSOLUTELY NOTHING (not even an explanation - complete silence)

DO NOT explain your reasoning for responding or not responding. DO NOT output meta-commentary about the conversation. Either respond naturally or output nothing.
"""

        system_prompt = [
            {
                "type": "text",
                "text": context["system_prompt"] + decision_criteria,
                "cache_control": {"type": "ephemeral"}
            }
        ]

        return system_prompt

    def add_pending_message(self, channel_id: str, message_id: int):
        """
        Add message to pending list for periodic check.

        Bug #7 fix: Track individual messages instead of just channels
        to prevent message loss in rapid multi-message sequences.

        Args:
            channel_id: Discord channel ID
            message_id: Discord message ID
        """
        self.pending_messages.append((channel_id, message_id))
        logger.debug(f"Added message {message_id} to pending queue (channel {channel_id})")
