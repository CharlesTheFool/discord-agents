"""
Reactive Engine - Message Handling

Handles incoming messages and generates responses.
Both response paths (@mention and periodic check) share one request
assembly + tool-loop + delivery pipeline; the paths differ only in
their system prompt, trigger semantics, and silence policy.
"""

import discord
import asyncio
import io
import logging
from anthropic import Anthropic, AsyncAnthropic, NotFoundError
from dataclasses import dataclass, field
from typing import Any, List, Optional, TYPE_CHECKING
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
from .files_api_client import FilesAPIClient
from .data_isolation import DataIsolationEnforcer
from .conversation_state_manager import ConversationStateManager
from .internal_constants import TOOL_STUB_KEEP_TURNS, format_size
from tools.web_search import get_web_search_tools
from tools.discord_tools import DiscordToolExecutor, get_discord_tools
from tools.skills_tool import get_skill_request_tool, SkillRequestExecutor

logger = logging.getLogger(__name__)

MAX_TOOL_LOOP_ITERATIONS = 10


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


def serialize_assistant_blocks(content) -> list:
    """
    Convert SDK response content into dicts safe to persist and replay.

    Server tool blocks (server_tool_use, code execution, web search/fetch and
    their results) are deliberately dropped: detached from the live response
    their content shapes can't be reproduced (a stringified result 400s on
    replay), and the assistant's final text already carries the conclusions.
    """
    serialized = []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            serialized.append({"type": "text", "text": block.text})
        elif block_type == "thinking":
            # Signature required for tool-use continuity on replay
            serialized.append({
                "type": "thinking",
                "thinking": block.thinking,
                "signature": block.signature,
            })
        elif block_type == "redacted_thinking":
            serialized.append({"type": "redacted_thinking", "data": block.data})
        elif block_type == "tool_use":
            serialized.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
    return serialized


def collect_container_output_file_ids(response) -> list:
    """
    File IDs of files written by code execution in this response.
    Output blocks ride inside code_execution / bash_code_execution tool
    results; error results have no output list (getattr returns None).
    """
    file_ids = []
    for block in response.content:
        if getattr(block, "type", None) in (
            "code_execution_tool_result",
            "bash_code_execution_tool_result",
        ):
            result_content = getattr(block, "content", None)
            for output in (getattr(result_content, "content", None) or []):
                file_id = getattr(output, "file_id", None)
                if file_id:
                    file_ids.append(file_id)
    return file_ids


_CACHEABLE_BLOCK_TYPES = frozenset({"text", "tool_result", "image", "document"})


def with_message_cache_breakpoint(messages: list) -> list:
    """
    Copy of messages with cache_control on the last cacheable block, so each
    request caches the whole conversation up to its tail (the system-prefix
    breakpoint alone re-reads the full history every turn). Old breakpoints
    need no removal: the marked message is rebuilt fresh from state each
    request, and markers don't affect prefix matching - the next turn's
    breakpoint lands further down and reads the previous one's cache.

    The source messages are never mutated (their dicts are shared with
    conversation state). SDK objects (in-loop assistant content) and
    unmarkable block types are skipped by walking backwards.
    """
    for mi in range(len(messages) - 1, -1, -1):
        content = messages[mi].get("content") if isinstance(messages[mi], dict) else None
        if isinstance(content, str):
            new_content = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
        elif isinstance(content, list):
            for bi in range(len(content) - 1, -1, -1):
                block = content[bi]
                if isinstance(block, dict) and block.get("type") in _CACHEABLE_BLOCK_TYPES:
                    new_content = list(content)
                    new_content[bi] = {**block, "cache_control": {"type": "ephemeral"}}
                    break
            else:
                continue
        else:
            continue
        new_msg = {**messages[mi], "content": new_content}
        return messages[:mi] + [new_msg] + messages[mi + 1:]
    return messages


@dataclass
class ToolLoopResult:
    """Outcome of one full tool-use loop (one bot turn)."""
    response_text: str = ""
    thinking_text: str = ""
    thinking_block: Any = None
    tools_were_used: bool = False
    usage: Any = None  # usage of the final response in the loop
    container_file_ids: List[str] = field(default_factory=list)


class ReactiveEngine:
    """
    Reactive message handling engine.

    Two entry points share one pipeline:
    - handle_urgent: @mentions; must always reply
    - periodic check: scans pending messages; replying is optional
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

        # Files API access for container output delivery. Independent of the
        # attachments feature: code execution is always available, and the
        # system prompt promises $OUTPUT_DIR files ride on the reply.
        self.files_api_client = FilesAPIClient(self.anthropic)

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

    def _track_task(self, task: asyncio.Task) -> None:
        """Hold a strong reference to a background task until it completes."""
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

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
        limit: int = None,
        exclude_message_ids: Optional[List[int]] = None
    ) -> None:
        """
        Initialize conversation state from database with full attachment data.

        Used by both urgent and periodic paths to ensure consistent state after restart.
        Loads recent messages with attachments, building proper content blocks.

        exclude_message_ids filters messages the caller is about to append
        itself (on_message stores to the DB before the engine runs, so without
        the filter the triggering message would land in state twice).
        """
        if limit is None:
            limit = self.config.api.context_messages

        logger.info("Conversation state empty, initializing from recent DB messages")
        recent_messages = await self.message_memory.get_recent(
            channel_id,
            limit=limit,
            exclude_message_ids=exclude_message_ids
        )

        # Add recent messages to state WITH attachments
        bot_user_id = (
            str(self.discord_client.user.id)
            if (self.discord_client and self.discord_client.user) else None
        )
        for db_msg in recent_messages:
            # Only THIS bot's messages are assistant turns; other bots are
            # interlocutors (treating them as assistant corrupts identity and
            # puts file blocks in assistant turns, which the API rejects)
            role = "assistant" if (db_msg.is_bot and str(db_msg.author_id) == bot_user_id) else "user"

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

                                    # document/container_upload blocks are only valid in user turns
                                    if role != "user":
                                        content_blocks.append({
                                            "type": "text",
                                            "text": f"\n[Attachment: {filename}]"
                                        })
                                        logger.debug(f"Skipped file block for assistant message: {filename}")

                                    # Add document block for reading (if eligible)
                                    elif file_data.get("use_as_document_block", True):
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

        # The oldest seeded message can be the bot's own - the API requires a
        # leading user turn
        conversation_state.trim_leading_non_user()

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

        lines = [
            "<attachments_index>",
            "Recent attachments in this channel (newest first):",
        ]
        for att_id, filename, size_bytes, att_type in rows:
            marker = "in context" if att_id in in_context_ids else "not in context"
            lines.append(f"- {att_id} | {filename} | {format_size(size_bytes)} | {att_type} | {marker}")
        lines += [
            "",
            "Retrieve any 'not in context' file with the discord tool: get_attachment + attachment_id.",
            "Spreadsheets and large files are processed via code execution; container files are",
            "mounted at the INPUT_DIR env var and are EPHEMERAL - read them in the same turn.",
            "</attachments_index>",
        ]
        return "\n".join(lines)

    async def _recover_stale_file_in_state(self, conversation_state, file_id: str) -> bool:
        """
        A file_id embedded in persisted conversation state 404'd at the API
        (file expired or deleted server-side). Re-upload from local storage and
        swap the id in place; if unrecoverable, strip the blocks so the channel
        unbricks. Returns True if the state changed.
        """
        new_file_id = None
        if self.attachment_manager:
            try:
                async with self.attachment_manager.attachment_db.db.execute(
                    "SELECT attachment_id, filename, local_path FROM attachments "
                    "WHERE file_id = ?", (file_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                if row and row[2]:
                    new_file_id = await self.attachment_manager._handle_file_expiration(
                        attachment_id=row[0], file_id=file_id,
                        local_path=row[2], filename=row[1],
                    )
            except Exception as e:
                logger.error(f"Stale-file recovery lookup failed for {file_id}: {e}")

        touched = conversation_state.swap_file_id(file_id, new_file_id)
        if touched:
            action = f"swapped to {new_file_id}" if new_file_id else "stripped"
            logger.warning(
                f"Recovered stale file {file_id} in conversation state: "
                f"{touched} block(s) {action}"
            )
            await self.conversation_state_manager.save(conversation_state)
        return touched > 0

    @staticmethod
    def _stale_file_id_from_error(error: Exception) -> Optional[str]:
        """Extract the file id from a 'File `file_xxx` not found.' API error."""
        import re
        match = re.search(r"File `(file_[A-Za-z0-9]+)` not found", str(error))
        return match.group(1) if match else None

    async def _container_files_for_discord(self, file_ids: list) -> list:
        """
        Download container-created files and wrap them as discord.File objects
        so deliverables (pptx, charts, ...) ride on the bot's reply.
        """
        if not file_ids:
            return []

        files = []
        for file_id in dict.fromkeys(file_ids):  # dedupe, keep order
            meta = await self.files_api_client.retrieve(file_id)
            data = await self.files_api_client.content(file_id)
            if not data:
                logger.warning(f"Could not download container output {file_id}, skipping")
                continue
            filename = (meta or {}).get("filename") or f"{file_id}.bin"
            files.append(discord.File(io.BytesIO(data), filename=filename))
            logger.info(f"Container output ready for Discord: {filename} ({len(data)} bytes)")
        return files[:10]  # Discord cap per message

    def _build_attachment_tool_result(self, result, block_id, conversation_state):
        """
        Convert a structured get_attachment result into (tool_result, file_block).

        Images inline into the tool_result (valid content). document /
        container_upload blocks are NOT valid tool_result content - the file
        block is returned separately to ride in the same user message after
        the tool_results, and is persisted as its own annotated user message.
        """
        content_blocks = [{"type": "text", "text": result["text"]}]
        file_block = None
        file_data = result["file_data"]

        if file_data.get("method") == "base64":
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": file_data["media_type"],
                    "data": file_data["data"],
                },
            })
        elif file_data.get("method") == "file_id":
            if file_data.get("use_as_document_block", True):
                file_block = {
                    "type": "document",
                    "source": {"type": "file", "file_id": file_data["data"]},
                }
                content_blocks[0]["text"] += "\nFile attached to the conversation as a readable document."
            else:
                file_block = {"type": "container_upload", "file_id": file_data["data"]}
                content_blocks[0]["text"] += "\nFile loaded for code execution (find it via the INPUT_DIR env var)."

            if conversation_state and file_block:
                att_id = result["metadata"]["attachment_id"]
                conversation_state.add_message("user", [file_block], [att_id])
                logger.info(f"Added fetched attachment to conversation state: {result['metadata']['filename']}")

        tool_result = {"type": "tool_result", "tool_use_id": block_id, "content": content_blocks}
        return tool_result, file_block

    # ========== SHARED RESPONSE PIPELINE ==========

    async def _process_message_attachments(self, message: discord.Message) -> list:
        """
        Process a message's attachments through the UnifiedAttachmentManager.
        Other bots' attachments are skipped unless bot interactions are enabled.
        """
        should_process = (
            self.attachment_manager and
            message.attachments and
            (not message.author.bot or self.config.discord.allow_bot_interactions)
        )
        if not should_process:
            return []

        processed = []
        for attachment in message.attachments:
            try:
                result = await self.attachment_manager.process_attachment(
                    attachment=attachment,
                    message=message,
                    is_realtime=True
                )
                if result and result.get("for_api"):
                    processed.append(result)
                    logger.info(f"Processed attachment: {attachment.filename}")
            except Exception as e:
                logger.error(f"Failed to process attachment {attachment.filename}: {e}", exc_info=True)
        return processed

    def _build_user_content(self, message: discord.Message, processed_attachments: list):
        """
        Discord message -> (content, attachment_ids) for conversation state.
        Attachment blocks follow the text block in processing order.
        """
        if not processed_attachments:
            return message.content, None

        # Empty text alongside attachments would be rejected by the API
        text_content = message.content if message.content.strip() else "[Attachment]"
        content = [{"type": "text", "text": text_content}]
        attachment_ids = []
        for att in processed_attachments:
            attachment_ids.append(att["attachment_id"])
            block = self.attachment_manager.build_content_block(
                att.get("for_api"), att["filename"]
            )
            if block:
                content.append(block)
                logger.info(f"Added {block['type']} block for {att['filename']}")
        return content, attachment_ids

    async def _finalize_system_blocks(self, system_blocks: list, conversation_state) -> list:
        """
        Append the shared system-prompt extras to a [cached, volatile] block
        pair: the skills catalog joins the cached prefix; the attachment index
        (volatile - rows and in-context markers track the rolling window)
        joins the uncached tail so the stable prefix keeps hitting the cache.
        """
        if self.skills_manager and conversation_state:
            skills_prompt = self.context_builder.build_skills_prompt(
                conversation_state.get_active_skills()
            )
            if skills_prompt:
                system_blocks[0]["text"] += "\n\n" + skills_prompt

        if conversation_state and self.attachment_manager:
            try:
                attachments_index = await self._generate_uploaded_files_manifest(conversation_state)
                if attachments_index:
                    if len(system_blocks) > 1:
                        system_blocks[-1]["text"] += "\n\n" + attachments_index
                    else:
                        system_blocks.append({"type": "text", "text": attachments_index})
            except Exception as e:
                logger.error(f"Failed to generate attachment index: {e}", exc_info=True)

        return system_blocks

    def _build_api_params(self, system_blocks: list, conversation_state, context) -> dict:
        """
        Assemble the Messages API request shared by both response paths:
        tool registry, beta headers, message history, skills container,
        thinking and effort.
        """
        tools = [{"type": "memory_20250818", "name": "memory"}]

        if self.discord_tool_executor:
            tools.extend(get_discord_tools())
        else:
            logger.warning("Discord tool executor is None - discord tools NOT added!")

        beta_headers = []
        if self.web_search_enabled:
            web_search_config = self.config.get_web_search_config()
            tools.extend(get_web_search_tools(citations_enabled=web_search_config["citations_enabled"]))

        if self.mcp_manager:
            mcp_tools = self.mcp_manager.get_tools_for_api()
            if mcp_tools:
                tools.extend(mcp_tools)
                logger.debug(f"Added {len(mcp_tools)} MCP tools to API request")

        # Skills REQUIRE code_execution: skill files load into the container
        if self.skills_manager:
            tools.append({"type": "code_execution_20260120", "name": "code_execution"})
            tools.append(get_skill_request_tool())
            beta_headers.append("skills-2025-10-02")

        # Persisted state may carry file_id references even when fresh
        # uploads are disabled, so the beta rides whenever the manager exists
        if self.attachment_manager:
            beta_headers.append("files-api-2025-04-14")

        if conversation_state:
            messages = conversation_state.get_messages_for_api()
            logger.debug(f"Using {len(messages)} messages from conversation state")
        else:
            messages = context["messages"].copy()
            logger.warning("Conversation state not available, falling back to context builder")

        api_params = {
            "model": self.config.api.model,
            "max_tokens": self.config.api.max_tokens,
            "system": system_blocks,
            "messages": messages,
            "tools": tools,
            "betas": beta_headers  # SDK's beta endpoint uses 'betas' parameter, not extra_headers
        }

        # Skills container with progressive disclosure (v0.5.0)
        if self.skills_manager:
            active_skill_names = conversation_state.get_active_skills() if conversation_state else []
            if not active_skill_names:
                active_skill_names = self.config.skills.default_skills or ["pdf"]
                if conversation_state:
                    conversation_state.set_active_skills(
                        active_skill_names, self.skills_manager.MAX_SKILLS_PER_REQUEST
                    )
            skills_list = self.skills_manager.select_skills(active_skill_names)
            if skills_list:
                api_params["container"] = {"skills": skills_list}
                logger.debug(f"Container skills: {active_skill_names}")

        # Adaptive thinking and effort if configured
        if self.config.api.thinking.enabled:
            api_params["thinking"] = {"type": "adaptive"}
        if self.config.api.effort:
            api_params["output_config"] = {"effort": self.config.api.effort}

        return api_params

    def _log_response_blocks(self, response, log_suffix: str = "") -> None:
        """Verbose per-block debug logging (active only with web search on)."""
        if not self.web_search_enabled:
            return
        logger.debug(f"Response has {len(response.content)} content blocks{log_suffix}")
        for i, block in enumerate(response.content):
            logger.debug(f"  Block {i}: type={block.type}")

            if hasattr(block, 'text'):
                logger.debug(f"    Text preview: {block.text[:200]}")

            if block.type == "container_upload":
                logger.debug(f"    container_upload: file_id={getattr(block, 'file_id', 'unknown')}, "
                             f"filename={getattr(block, 'filename', 'unknown')}")

            if block.type == "web_fetch_tool_result":
                if hasattr(block, 'content') and hasattr(block.content, 'content'):
                    if hasattr(block.content.content, 'source') and hasattr(block.content.content.source, 'data'):
                        logger.debug(f"    web_fetch content: {block.content.content.source.data[:500]}")

            if block.type == "server_tool_use" and hasattr(block, 'name'):
                logger.info(f"Server tool used{log_suffix}: {block.name}")
                logger.debug(f"  Server tool {block.name} input: {block.input}")

    async def _execute_tool_blocks(self, response, message: discord.Message,
                                   conversation_state, api_params: dict):
        """
        Execute the client-side tool_use blocks in a response.

        Returns (tool_results, pending_file_blocks, container_rebuilt):
        pending_file_blocks are document/container_upload blocks from
        get_attachment that ride in the same user message AFTER the results;
        container_rebuilt signals request_skill replaced the skills container.
        """
        channel_id = str(message.channel.id)
        server_id = str(message.guild.id) if message.guild else None

        tool_results = []
        pending_file_blocks = []
        container_rebuilt = False

        for block in response.content:
            if block.type != "tool_use":
                continue

            if block.name == "memory":
                command = block.input.get('command', 'unknown')
                path = block.input.get('path', 'unknown')
                logger.debug(f"Executing memory tool: {command} {path}")

                result = self.memory_tool_executor.execute(
                    block.input,
                    current_server_id=server_id,
                    current_channel_id=channel_id
                )
                self.conversation_logger.log_memory_tool(command, path, result)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result
                })

            # Progressive disclosure (v0.5.0)
            elif block.name == "request_skill":
                logger.info(f"Executing request_skill tool: {block.input.get('skill_name', 'unknown')}")
                if conversation_state:
                    result = self.skill_request_executor.execute(block.input, conversation_state)
                    await self.conversation_state_manager.save(conversation_state)
                    # Rebuild container so the NEXT iteration ships the new
                    # skill set; skills load at container creation, so the id
                    # is dropped to force a fresh container (files written in
                    # the old container are lost; message container_upload
                    # blocks re-mount automatically)
                    new_skills = self.skills_manager.select_skills(
                        conversation_state.get_active_skills()
                    )
                    if new_skills:
                        api_params["container"] = {"skills": new_skills}
                        container_rebuilt = True
                else:
                    result = "Error: Conversation state not available"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result
                })

            elif block.name == "discord_tools":
                if self.discord_tool_executor:
                    logger.debug(f"Executing Discord tool: {block.input.get('command', 'unknown')}")
                    result = await self.discord_tool_executor.execute(
                        block.input,
                        current_server_id=server_id,
                        current_channel_id=channel_id
                    )
                    # Structured data from get_attachment
                    if isinstance(result, dict) and result.get("_structured"):
                        tool_result, file_block = self._build_attachment_tool_result(
                            result, block.id, conversation_state
                        )
                        tool_results.append(tool_result)
                        if file_block:
                            pending_file_blocks.append(file_block)
                    else:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result
                        })

            # MCP tools are prefixed with their server name (v0.5.0)
            elif "_" in block.name and self.mcp_manager:
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

        return tool_results, pending_file_blocks, container_rebuilt

    async def _run_tool_loop(
        self,
        api_params: dict,
        message: discord.Message,
        conversation_state,
        fallback_text: Optional[str] = None,
        log_suffix: str = "",
    ) -> ToolLoopResult:
        """
        Drive the Claude API tool-use loop to completion (both response paths).

        fallback_text marks a must-reply path: the result text is never empty
        (loop overruns, blank replies, and unexpected stops degrade to it).
        When None (periodic path), the same outcomes end with empty text,
        which the caller treats as silence.
        """
        result = ToolLoopResult()
        container_id = None
        recovered_file_ids = set()  # stale file_ids already recovered this turn
        loop_iteration = 0

        while True:
            loop_iteration += 1

            logger.debug(f"API params{log_suffix}: model={api_params.get('model')}, betas={api_params.get('betas')}")
            logger.debug(f"  Tools: {[t.get('name') or t.get('type') for t in api_params.get('tools', [])]}")
            if 'container' in api_params:
                logger.debug(f"  Container/Skills: {api_params.get('container')}")

            try:
                response = await self.anthropic.beta.messages.create(
                    **{**api_params, "messages": with_message_cache_breakpoint(api_params["messages"])}
                )
            except NotFoundError as e:
                # Stale file_id in persisted state 404s the whole request
                stale_id = self._stale_file_id_from_error(e)
                if (stale_id and conversation_state
                        and stale_id not in recovered_file_ids):
                    recovered_file_ids.add(stale_id)
                    if await self._recover_stale_file_in_state(conversation_state, stale_id):
                        api_params["messages"] = conversation_state.get_messages_for_api()
                        continue
                raise

            result.usage = getattr(response, "usage", None)

            # Multi-iteration turns must reuse the container (per Anthropic docs)
            if getattr(response, "container", None) and getattr(response.container, "id", None):
                container_id = response.container.id

            # Collect files written by code execution for Discord delivery
            result.container_file_ids.extend(collect_container_output_file_ids(response))

            self.conversation_logger.log_tool_use_loop(loop_iteration, response.stop_reason)
            self._log_response_blocks(response, log_suffix)

            if loop_iteration == 1 and result.usage is not None:
                logger.info(f"Input tokens: {total_input_tokens(result.usage):,} "
                            f"(uncached: {result.usage.input_tokens:,})")

            # Extract thinking (full block kept for persistence with signature)
            for block in response.content:
                if block.type == "thinking":
                    result.thinking_text += block.thinking
                    result.thinking_block = block

            if response.stop_reason == "tool_use":
                # Safety cap: a model that keeps requesting tools must not loop unbounded
                if loop_iteration >= MAX_TOOL_LOOP_ITERATIONS:
                    logger.warning(f"Tool use loop exceeded {MAX_TOOL_LOOP_ITERATIONS} iterations{log_suffix}")
                    if fallback_text:
                        result.response_text = result.response_text or "I got stuck in a tool loop - try asking again."
                    break
                result.tools_were_used = True

                tool_results, pending_file_blocks, container_rebuilt = await self._execute_tool_blocks(
                    response, message, conversation_state, api_params
                )

                # Tool results ride in the next user message; fetched file
                # blocks come AFTER them (API: tool_result blocks must come first)
                api_params["messages"].append({"role": "assistant", "content": response.content})
                api_params["messages"].append({"role": "user", "content": tool_results + pending_file_blocks})

                # Persist tool use and results to conversation state
                if conversation_state:
                    try:
                        conversation_state.add_tool_use_and_results(
                            assistant_content=serialize_assistant_blocks(response.content),
                            tool_results=tool_results
                        )
                        await self.conversation_state_manager.save(conversation_state)

                        removed_count = conversation_state.enforce_message_cap()
                        if removed_count > 0:
                            logger.info(
                                f"Message cap enforced during tool loop{log_suffix}: "
                                f"removed {removed_count} messages"
                            )
                            await self.conversation_state_manager.save(conversation_state)
                            # Rebuild request messages to stay in sync with the
                            # state (prevents orphaned tool_results)
                            api_params["messages"] = conversation_state.get_messages_for_api()
                    except Exception as e:
                        logger.error(f"Failed to persist tool results to conversation state{log_suffix}: {e}", exc_info=True)

                if container_rebuilt:
                    container_id = None  # force a fresh container with the new skills
                if container_id and self.skills_manager and 'container' in api_params:
                    api_params["container"]["id"] = container_id

                continue

            elif response.stop_reason == "end_turn":
                # Extract final text response and citations
                citations_list = []
                for block in response.content:
                    if block.type == "text":
                        result.response_text += block.text
                        for citation in (getattr(block, 'citations', None) or []):
                            url = getattr(citation, 'url', None)
                            title = getattr(citation, 'title', None)
                            if url and title:
                                citations_list.append(f"[{title}]({url})")

                if citations_list:
                    result.response_text += "\n\n**Sources:**\n" + "\n".join(f"- {cite}" for cite in citations_list)

                # Tools ran but the model went quiet: reprompt once for a
                # brief confirmation
                if (not result.response_text.strip() and result.tools_were_used
                        and loop_iteration < MAX_TOOL_LOOP_ITERATIONS):
                    logger.warning(f"Tools used but no text response{log_suffix} - reprompting (iteration {loop_iteration})")
                    api_params["messages"].append({"role": "assistant", "content": response.content})
                    api_params["messages"].append({
                        "role": "user",
                        "content": [{"type": "text", "text": "Please provide a brief response confirming what you just did for the user."}]
                    })
                    continue

                if not result.response_text and fallback_text:
                    result.response_text = fallback_text
                break

            else:
                logger.warning(f"Unexpected stop_reason{log_suffix}: {response.stop_reason}")
                if fallback_text:
                    result.response_text = fallback_text
                break

        return result

    async def _send_response_chunks(
        self,
        channel,
        response_text: str,
        container_file_ids: list,
        reference: Optional[discord.Message] = None,
    ) -> Optional[discord.Message]:
        """
        Send a response to Discord, split to the message-length limit, with
        container-created deliverables riding on the first chunk.

        Returns the last successfully sent message, or None if the first
        chunk could not be delivered at all.
        """
        from .discord_client import split_message
        message_chunks = split_message(response_text)
        outgoing_files = await self._container_files_for_discord(container_file_ids)

        sent_message = None
        for i, chunk in enumerate(message_chunks):
            try:
                if i == 0:
                    sent_message = await channel.send(
                        chunk, reference=reference, files=outgoing_files or None
                    )
                else:
                    sent_message = await channel.send(chunk)
            except discord.HTTPException as e:
                if i == 0 and reference is not None:
                    # Reply target may have been deleted - retry standalone
                    try:
                        logger.warning(f"Failed to send reply, trying standalone: {e}")
                        # discord.File objects are single-use; rebuild for the retry
                        outgoing_files = await self._container_files_for_discord(container_file_ids)
                        sent_message = await channel.send(chunk, files=outgoing_files or None)
                    except discord.HTTPException as e2:
                        logger.error(f"Failed to send response to Discord: {e2}")
                        self.conversation_logger.log_error(f"Discord send failed: {str(e2)}")
                        return None
                elif i == 0:
                    logger.error(f"Failed to send response to Discord: {e}")
                    self.conversation_logger.log_error(f"Discord send failed: {str(e)}")
                    return None
                else:
                    logger.error(f"Failed to send message chunk {i+1}/{len(message_chunks)}: {e}")
                    self.conversation_logger.log_error(f"Discord send failed (chunk {i+1}): {str(e)}")
                    # Keep trying remaining chunks
        return sent_message

    async def _persist_assistant_response(
        self, conversation_state, channel_id: str, result: ToolLoopResult, seed_epoch: int
    ) -> None:
        """
        Append the assistant turn (thinking + text) to conversation state,
        enforce the cap, record the usage watermark, stub old tool results,
        save, and kick off episodization when the session is over threshold.
        """
        if not conversation_state:
            return
        try:
            assistant_content = []
            if result.thinking_block:
                # Preserve thinking block with signature for next turn
                assistant_content.append({
                    "type": "thinking",
                    "thinking": result.thinking_block.thinking,
                    "signature": result.thinking_block.signature
                })
            if result.response_text:
                assistant_content.append({"type": "text", "text": result.response_text})

            conversation_state.add_message("assistant", assistant_content)

            removed_count = conversation_state.enforce_message_cap()
            if removed_count > 0:
                logger.info(f"Message cap enforced after response: removed {removed_count} oldest messages")

            # Record session usage watermark and stub old tool results (v0.6.0)
            if result.usage is not None:
                conversation_state.record_usage(total_input_tokens(result.usage), seed_epoch)
            conversation_state.stub_old_tool_results(keep_turns=TOOL_STUB_KEEP_TURNS)

            await self.conversation_state_manager.save(conversation_state)
            logger.debug(f"Saved conversation state: {conversation_state}")

            # Session over usage threshold -> close the episode in the background
            if (self.episode_manager
                    and conversation_state.session_input_tokens > self.config.api.context_tokens):
                logger.info(
                    f"Session usage {conversation_state.session_input_tokens:,} over threshold "
                    f"{self.config.api.context_tokens:,} - episodizing channel {channel_id}"
                )
                self._track_task(asyncio.create_task(
                    self.episode_manager.episodize_channel(channel_id, force=True)
                ))

        except Exception as e:
            logger.error(f"Failed to update conversation state: {e}", exc_info=True)

    def _start_engagement_tracking(self, sent_message, channel, original_author_id: int) -> None:
        """Record the response for rate limiting and schedule the engagement check."""
        self.rate_limiter.record_response(str(channel.id))

        delay = self._rate_limiting_config["engagement_tracking_delay"]
        self.conversation_logger.log_engagement_tracking(started=True, delay_seconds=delay)
        self.conversation_logger.log_separator()

        self._track_task(asyncio.create_task(
            self._track_engagement(
                sent_message.id,
                channel,
                original_author_id=original_author_id,
                delay=delay,
            )
        ))

    # ========== URGENT PATH (@mentions) ==========

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
                # If state is empty, initialize with recent DB messages.
                # In-flight mentions (incl. this one) get appended by their
                # own handlers - seeding them here would duplicate them
                if len(conversation_state.messages) == 0:
                    await self._initialize_conversation_from_db(
                        channel_id, conversation_state,
                        exclude_message_ids=list(self._responded_messages)
                    )
            except Exception as e:
                logger.error(f"Failed to load conversation state for channel {channel_id}: {e}", exc_info=True)

        # Log incoming message
        self.conversation_logger.log_user_message(
            author=message.author.display_name,
            channel=message.channel.name,
            content=message.content,
            is_mention=True
        )

        processed_attachments = await self._process_message_attachments(message)

        # @mentions ALWAYS bypass rate limits (especially ignore threshold)
        self.conversation_logger.log_decision(
            should_respond=True,
            reason="mention detected (bypasses rate limits)",
            rate_limit_stats=self.rate_limiter.get_stats(channel_id)
        )

        logger.info(f"Calling Claude API for @mention from {message.author.name}")

        try:
            # Context is built inside the semaphore so each message gets isolated context
            async with self._response_semaphore:
                # Exclude messages currently being processed to prevent seeing
                # other pending @mentions
                context = await self.context_builder.build_context(
                    message,
                    exclude_message_ids=list(self._responded_messages)
                )

                # Add current user message to conversation state BEFORE API call
                if conversation_state:
                    try:
                        user_content, attachment_ids = self._build_user_content(message, processed_attachments)
                        conversation_state.add_message("user", user_content, attachment_ids)
                        conversation_state.enforce_message_cap()
                    except Exception as e:
                        logger.error(f"Failed to add user message to conversation state: {e}", exc_info=True)

                if context.get("stats"):
                    self.conversation_logger.log_context_building(**context["stats"])

                async with message.channel.typing():
                    await asyncio.sleep(1.5)  # small delay for a more natural feel

                    # Cached prefix + uncached volatile tail (timestamp etc.)
                    system_blocks = [{
                        "type": "text",
                        "text": context["system_prompt"],
                        "cache_control": {"type": "ephemeral"}
                    }]
                    volatile = context.get("time_context", "")
                    if volatile:
                        system_blocks.append({"type": "text", "text": volatile})
                    await self._finalize_system_blocks(system_blocks, conversation_state)

                    api_params = self._build_api_params(system_blocks, conversation_state, context)
                    # Usage recorded later must match the session we measured
                    seed_epoch = conversation_state.seed_epoch if conversation_state else 0

                    result = await self._run_tool_loop(
                        api_params, message, conversation_state,
                        fallback_text="I'm not sure how to respond to that.",
                    )

            # Send response (outside semaphore to allow concurrent API calls while sending)
            sent_message = await self._send_response_chunks(
                message.channel, result.response_text, result.container_file_ids,
                reference=message,
            )
            if sent_message is None:
                self.conversation_logger.log_separator()
                return

            # Log thinking trace (if present) and bot response
            if result.thinking_text:
                self.conversation_logger.log_thinking(result.thinking_text, len(result.thinking_text))
            self.conversation_logger.log_bot_response(result.response_text, len(result.response_text))

            await self._persist_assistant_response(conversation_state, channel_id, result, seed_epoch)

            self._start_engagement_tracking(sent_message, message.channel, message.author.id)

            logger.info(
                f"Response sent to {message.author.name} ({len(result.response_text)} chars)"
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

        if self.conversation_state_manager:
            try:
                await self.conversation_state_manager.close()
            except Exception as e:
                logger.error(f"Error closing conversation state DB: {e}")

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
                    self._track_task(asyncio.create_task(self.episode_manager.check_idle_channels()))

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

            # If state is empty, initialize with recent DB messages (excluding
            # the message we are about to append ourselves)
            if len(conversation_state.messages) == 0:
                await self._initialize_conversation_from_db(
                    channel_id, conversation_state,
                    exclude_message_ids=[message_id]
                )

            processed_attachments = await self._process_message_attachments(message)
            user_content, attachment_ids = self._build_user_content(message, processed_attachments)

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
                # Messages are already in state (with attachments) from Phase 1
                # processing; only seed from the DB when the state is empty
                if len(conversation_state.messages) == 0:
                    await self._initialize_conversation_from_db(channel_id, conversation_state)
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
        if context.get("stats"):
            self.conversation_logger.log_context_building(**context["stats"])

        # Prevent concurrent responses
        async with self._response_semaphore:
            system_blocks = self._build_response_decision_prompt(context)
            await self._finalize_system_blocks(system_blocks, conversation_state)

            api_params = self._build_api_params(system_blocks, conversation_state, context)
            # Usage recorded later must match the session we measured
            seed_epoch = conversation_state.seed_epoch if conversation_state else 0

            # Show typing while generating (auto-refreshes until cancelled)
            typing_task = asyncio.create_task(self._keep_typing(message.channel))

            try:
                result = await self._run_tool_loop(
                    api_params, message, conversation_state, log_suffix=" (periodic)",
                )

                # Empty text = Claude decided to stay silent
                if not result.response_text.strip():
                    if result.tools_were_used:
                        logger.warning(f"Claude used tools but returned no text response in channel {channel_id}")
                    else:
                        logger.info(f"Claude decided not to respond in channel {channel_id}")
                    if result.thinking_text:
                        self.conversation_logger.log_thinking(result.thinking_text, len(result.thinking_text))
                    self.conversation_logger.log_bot_response("[No response - staying silent]", 0)
                    self.conversation_logger.log_separator()
                    return

                # Staleness guard: generation takes tens of seconds and the
                # conversation may have moved on - a reply aimed at an old
                # message lands as a non sequitur. Drop it (nothing sent,
                # nothing persisted); the next tick re-evaluates fresh.
                try:
                    newest = None
                    async for m in message.channel.history(limit=1):
                        newest = m
                    if (newest and newest.id != message.id
                            and newest.author != self.discord_client.user):
                        logger.info(
                            f"Discarding stale periodic response in {channel_id}: "
                            f"conversation moved past target {message.id}"
                        )
                        return
                except discord.HTTPException:
                    pass  # can't verify; send anyway

                # Log thinking trace (if present) and bot response
                if result.thinking_text:
                    self.conversation_logger.log_thinking(result.thinking_text, len(result.thinking_text))
                self.conversation_logger.log_bot_response(result.response_text, len(result.response_text))

                # Send response as standalone (not a reply)
                sent_message = await self._send_response_chunks(
                    message.channel, result.response_text, result.container_file_ids,
                )
                if sent_message is None:
                    return

                # Persist only what was actually delivered
                await self._persist_assistant_response(conversation_state, channel_id, result, seed_epoch)

                self._start_engagement_tracking(sent_message, message.channel, message.author.id)

                # Mark message as responded to prevent duplicate responses
                self._responded_messages.append(message.id)

                logger.info(f"Periodic response sent in channel {channel_id} ({len(result.response_text)} chars)")

            except Exception as e:
                logger.error(f"Error calling Claude for periodic check: {e}", exc_info=True)
                self.conversation_logger.log_error(f"Periodic check error: {str(e)}")
                self.conversation_logger.log_separator()
            finally:
                typing_task.cancel()

    async def _keep_typing(self, channel) -> None:
        """Hold the typing indicator until cancelled (Discord refreshes ~10s)."""
        try:
            async with channel.typing():
                await asyncio.sleep(3600)
        except (asyncio.CancelledError, discord.HTTPException):
            pass

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

        # The current momentum value is volatile (recomputed every periodic
        # check) - it rides in the uncached trailing block; the cached prefix
        # only describes the levels
        decision_criteria = f"""

# Response Decision Criteria (Periodic Check)

You are monitoring an ongoing Discord conversation. Decide whether to participate based on:

**Direct Triggers** (Always consider responding):
- Your name "{bot_name}" mentioned (even without @)
- Question you can answer
- Topic within your expertise
- Someone needs help you can provide
- Conversation about technical topics you understand

**Conversation Momentum** (the CURRENT level is given in the context block below):
- {momentum_descriptions["cold"]}
- {momentum_descriptions["warm"]}
- {momentum_descriptions["hot"]}

Your personality and base prompt guide whether to participate. Consider relevance, value-add, and whether the activity level matches when you'd naturally engage.

**DON'T Respond If**:
- Nothing meaningful to add
- Conversation doesn't need your input
- Would interrupt natural flow
- Just agreeing without adding value
- Making conversation about yourself

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
        # Volatile values (timestamp, momentum) in a separate uncached block
        volatile = context.get("time_context", "")
        volatile += f"\nConversation momentum right now: {context['conversation_momentum'].upper()}"
        system_prompt.append({"type": "text", "text": volatile.strip()})

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
