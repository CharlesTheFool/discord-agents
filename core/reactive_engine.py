"""
Reactive Engine - Message Handling

Handles incoming messages and generates responses.
Both response paths (@mention and periodic check) share one request
assembly + tool-loop + delivery pipeline; the paths differ only in
their system prompt, trigger semantics, and silence policy.
"""

import discord
import asyncio
import hashlib
import io
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional, TYPE_CHECKING
from pathlib import Path
from collections import deque
import sys
import os

# Add tools directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

if TYPE_CHECKING:
    from .config import BotConfig
    from .model.driver import ModelDriver
    from .rate_limiter import RateLimiter
    from .message_memory import MessageMemory
    from .memory_manager import MemoryManager
    from .conversation_logger import ConversationLogger
    from .user_cache import UserCache

from .memory_tool_executor import MemoryToolExecutor
from .context_builder import ContextBuilder
from .mcp_manager import MCPManager
from .skills import SkillCatalog
from .unified_attachment_manager import UnifiedAttachmentManager
from .vaults import VaultEnforcer
from .conversation_state import without_ref
from .conversation_state_manager import ConversationStateManager
from .channel_locks import ChannelLocks
from .internal_constants import (
    TOOL_STUB_KEEP_TURNS, PRIME_CONTEXT_TEMPLATE, WORKSHOP_DM_LINE, WORKSHOP_PROMPT,
    NOTE_TURN_LINE, NOTE_TURN_SILENT,
    WORKSHOP_SHELF_LINE, WORKSHOP_TURN_WALL_CLOCK_SECONDS, format_size,
)
from .model.driver import DocumentRefusedError, ModelRequest, StaleFileError, Usage, total_input_tokens
from .model.registry import ToolContext, assemble_tools
from tools.bash_tool import run_bash
from tools.editor_tool import EDITOR_NAME, run_editor
from tools.network_access_tool import run_request_network_access
from tools.skills_tool import run_request_skill
from .workshop.notes import ProactiveLimits
from .workshop.projects import CheckIns, ProjectBook, manifest as projects_manifest
from .workshop.scopes import tree_of
from .workshop.panel import Panel
from .workshop.workers import Workers, copy_to_inbox

logger = logging.getLogger(__name__)

MAX_TOOL_LOOP_ITERATIONS = 10
# Work in the workshop is steps (install, write, run, fix, run, send): its
# turns get more iterations; the turn wall clock still bounds them
WORKSHOP_TOOL_LOOP_ITERATIONS = 25


def memory_intent(kind: str, text: str, display_name: str) -> str:
    """The structured intent a /memory command injects into the DM (v0.9)."""
    return (
        f'<memory_request kind="{kind}">{text}</memory_request>\n'
        f"(A /memory {kind} command from {display_name} - they're "
        f"deliberately asking; handle it with your memory tool, then "
        f"answer them in your own voice. Anything you write down from "
        f"this carries the private origin tag - it shapes how you treat "
        f"them everywhere, cited nowhere.)"
    )


class _SyntheticDMMessage:
    """Message-shaped carrier for /memory commands (no discord.Message exists
    for a slash interaction). Carries exactly the attributes the context
    builder and tool loop touch."""

    def __init__(self, channel, author, content: str):
        self.channel = channel
        self.author = author
        self.content = content
        self.guild = None
        self.id = 0
        self.reference = None
        self.attachments = []
        self.reactions = []
        self.mentions = []
        self.created_at = datetime.now(timezone.utc)


@dataclass
class ToolLoopResult:
    """Outcome of one full tool-use loop (one bot turn)."""
    response_text: str = ""
    thinking_text: str = ""
    thinking_block: Any = None
    tools_were_used: bool = False
    model: str = ""  # the model that wrote this turn (stamped on persisted assistant messages)
    usage: Any = None  # the final call's usage
    # The session watermark (0.14.4): the last call before any server tool
    # (web search/fetch) ran this turn. A server-tool call's usage sums its
    # internal passes, and every later call carries its never-persisted
    # results, so neither measures the conversation. None: nothing clean.
    context_usage: Any = None
    total_usage: Usage = field(default_factory=Usage)  # every call this turn, for cost
    ended_on_send: bool = False  # the turn's last tool call was a send that posted
    dropped_text_chars: int = 0  # trailing text the send guard held back
    attached_files: int = 0  # files send_message actually attached this turn
    tool_calls: List[dict] = field(default_factory=list)  # {tool, action, detail} per call (v0.9)
    # Native messaging (v0.12.0): messages the model sent via send_message
    sent_message_ids: List[str] = field(default_factory=list)
    sent_texts: List[str] = field(default_factory=list)  # what those messages said (the turn's event)
    last_sent_message: Any = None  # discord.Message, for engagement tracking

    @property
    def did_send(self) -> bool:
        """True when the model already delivered messages via send_message."""
        return bool(self.sent_message_ids)



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
        driver: "ModelDriver",
        conversation_logger: "ConversationLogger",
        channel_locks: ChannelLocks,
        user_cache: Optional["UserCache"] = None,
    ):
        """
        Initialize reactive engine.

        Args:
            config: Bot configuration
            rate_limiter: Rate limiter instance
            message_memory: Message storage
            memory_manager: Memory tool manager
            driver: Model driver every model call goes through
            conversation_logger: Conversation logger
            channel_locks: Per-channel turn locks, shared with every other
                writer of conversation state
            user_cache: User cache for DM channel registry (v0.9)
        """
        self.config = config
        self.rate_limiter = rate_limiter
        self.message_memory = message_memory
        self.memory_manager = memory_manager
        self.conversation_logger = conversation_logger
        self.user_cache = user_cache
        # Sync callable: () -> list of guild names (set by discord_client in
        # on_ready); feeds the DM prime-context line (v0.9).
        self.list_servers = None

        self.driver = driver
        self.channel_locks = channel_locks

        # Cache internal config values (v0.6.0 - simplified config)
        self._rate_limiting_config = config.get_rate_limiting_config()

        # Vault enforcement (v0.7.0) - the one mechanical isolation gate
        self.vaults = VaultEnforcer(config.vaults)
        self.vaults.thread_parent_resolver = message_memory.thread_parent
        self.vaults.threads_of = message_memory.threads_of
        self.vaults.dm_partner_resolver = user_cache.dm_partner if user_cache else None

        self.memory_tool_executor = MemoryToolExecutor(
            memory_base_path=Path("memories"),
            bot_id=config.bot_id,
            vaults=self.vaults,
        )

        self.context_builder = ContextBuilder(
            config=config,
            message_memory=message_memory,
            memory_manager=memory_manager,
        )

        # Web search: all-or-nothing (no rate limiting)
        self.web_search_enabled = config.api.web_search.enabled
        if self.web_search_enabled:
            logger.info("Web search enabled (unlimited)")

        # Initialize Discord tool executor (Phase 4)
        # Get user_cache from discord_client once it's set in on_ready
        self.discord_tool_executor = None
        self._discord_tools_enabled = True  # Always enabled for Phase 4

        # ask_prime executor (v0.9) - constructed in on_ready (needs the client)
        self.ask_prime_executor = None

        # The workshop (0.14): set by bot_manager when it runs; its job watcher
        # starts in async_initialize, once conversation state exists
        self.workshop = None
        self.job_watcher = None
        self.approvals = None      # network approvals, wired in on_ready (needs the client)
        self.projects = None       # project notes (0.15.0), read where the workshop runs
        self.workers = None        # delegated workers (0.15.0), with the job watcher
        # Daily caps on unprompted turns, shared with the agentic engine
        self.proactive_limits = ProactiveLimits(config)

        # Initialize v0.5.0 managers
        # MCP Manager
        self.mcp_manager = None
        if config.mcp.enabled:
            mcp_config = config.get_mcp_config()
            self.mcp_manager = MCPManager(config_path=Path(mcp_config["config_file"]))
            logger.info("MCP manager initialized")

        # Skills (0.14): local folders, listed where the workshop runs
        self.skills = SkillCatalog(Path(config.get_skills_config()["skills_dir"]),
                                   config.skills.default_skills)

        # Unified Attachment Manager (replaces multimedia_processor)
        self.attachment_manager = None  # Initialized in async_initialize
        self.repository_manager = None  # v0.6.1, initialized in async_initialize

        # Conversation State Manager (v0.5.0 - dual-cap context management)
        self.conversation_state_manager = None  # Initialized in async_initialize

        # Episode Manager (v0.6.0 episodic sessions)
        self.episode_manager = None  # Initialized in async_initialize

        # Track background tasks for clean shutdown
        self._background_tasks = set()

        # Track messages that have been responded to (prevent race condition)
        # Using deque with maxlen to automatically discard old entries
        self._responded_messages = deque(maxlen=1000)

        # Pending channels for periodic check (Phase 3)
        # Bug #7 fix: Track individual messages, not just channels
        # List of (channel_id, message_id) tuples to prevent message loss
        self.pending_messages = []
        self._expedited_scans = set()  # channels with a soft-reply scan queued
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
                driver=self.driver,
                model=self.config.api.background_model,
                channel_locks=self.channel_locks,
            )

        # Initialize MCP Manager (discover tools from servers)
        if self.mcp_manager:
            try:
                await self.mcp_manager.initialize()
                logger.info("MCP manager initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize MCP manager: {e}", exc_info=True)
                self.mcp_manager = None  # Disable on failure

        # Initialize Unified Attachment Manager (replaces multimedia_processor)
        if self.config.attachments.enabled:
            try:
                self.attachment_manager = UnifiedAttachmentManager(
                    config=self.config,
                    anthropic_client=self.driver.client,
                    message_memory=self.message_memory
                )
                await self.attachment_manager.initialize()
                logger.info("UnifiedAttachmentManager initialized successfully")

                # Pass attachment_manager to ContextBuilder
                self.context_builder.attachment_manager = self.attachment_manager
                logger.debug("ContextBuilder updated with attachment_manager")

                # Bot file repository (v0.6.1) - rides the attachment pipeline
                if self.config.attachments.repository.enabled:
                    from core.repository_manager import RepositoryManager
                    self.repository_manager = RepositoryManager(
                        bot_id=self.config.bot_id,
                        attachment_manager=self.attachment_manager,
                        vaults=self.vaults,
                    )
                    logger.info("Repository feature enabled")
                if hasattr(self.driver, "resolve_attachment"):
                    self.driver.resolve_attachment = self.attachment_manager.resolve_ref
                if self.conversation_state_manager:
                    self.conversation_state_manager.attachment_for_file = (
                        self.attachment_manager.attachment_for_file_id)
            except Exception as e:
                logger.error(f"Failed to initialize UnifiedAttachmentManager: {e}", exc_info=True)
                self.attachment_manager = None  # Disable on failure
                self.repository_manager = None

        # once: on_ready runs again after a reconnect, and a second watcher
        # would follow the same jobs twice
        if self.workshop is not None and self.conversation_state_manager and self.job_watcher is None:
            from core.workshop.notes import ContextNoteWriter
            from core.workshop.watcher import JobWatcher
            self.job_watcher = JobWatcher(
                self.workshop,
                ContextNoteWriter(self.conversation_state_manager, self.channel_locks),
                wake=self.run_note_turn, config=self.config, events=self.message_memory)
            self.workshop.on_background = self.job_watcher.follow
            self.projects = ProjectBook(self.memory_tool_executor.base_path, self.config.bot_id,
                                        self.vaults, self._channel_place)
            self.workers = Workers(
                workshop=self.workshop, config=self.config, memory=self.memory_tool_executor,
                skills=self.skills, deliver=self.job_watcher.deliver,
                transcripts=Path("persistence") / "workshop" / self.config.bot_id / "workers",
                discord_tools=lambda: self.discord_tool_executor,
                attachments=lambda: self.attachment_manager)
            checkins = CheckIns(self.projects, self.workshop,
                                ContextNoteWriter(self.conversation_state_manager, self.channel_locks),
                                self.job_watcher.request_turn, self.config, self.proactive_limits,
                                events=self.message_memory)
            self._track_task(asyncio.ensure_future(checkins.run()))
            panel = Panel(self.workshop, Path("persistence") / "workshop" / self.config.bot_id,
                          approvals=lambda: self.approvals)
            self._track_task(asyncio.ensure_future(panel.run()))
            # its own task: reconciling jobs (and their tails) talks to the
            # runtime, which must never hold up the rest of on_ready
            self._track_task(asyncio.ensure_future(self._start_watcher()))

    def _channel_place(self, channel_id: str):
        """(server id or None for a DM, thread parent, is_thread) of a channel
        this bot can see right now, or None: how a project's home channel is
        checked against the scope holding its note."""
        if self.vaults.dm_partner_resolver and self.vaults.dm_partner_resolver(str(channel_id)):
            return None, None, False
        channel = self.discord_client.get_channel(int(channel_id)) if self.discord_client else None
        if channel is None or isinstance(channel, discord.DMChannel):
            return (None, None, False) if channel is not None else None
        if isinstance(channel, discord.Thread):
            return str(channel.guild.id), str(channel.parent_id), True
        return str(channel.guild.id), None, False

    def _projects_place(self, message):
        """(scope, memory tree) of this conversation where its workshop runs."""
        if not self._workshop_on(message):
            return None
        channel = message.channel
        server_id = str(message.guild.id) if message.guild else None
        parent_id = getattr(channel, "parent_id", None)
        parent_id = str(parent_id) if parent_id else None
        scope = self.workshop.scope(server_id, str(channel.id), parent_id=parent_id,
                                    is_thread=isinstance(channel, discord.Thread))
        return scope, tree_of(scope, server_id, str(channel.id), parent_id)

    async def _start_watcher(self) -> None:
        try:
            await self.job_watcher.start()
        except Exception:
            logger.exception("the job watcher couldn't settle jobs at boot")

    async def _load_conversation_state(self, channel_id: str,
                                       exclude_message_ids: Optional[List[int]] = None):
        """
        The channel's conversation state, seeded from recent DB messages when
        empty; None when there's no state manager or loading fails. Call it
        with the channel's lock held - the seeding writes the state.
        """
        if not self.conversation_state_manager:
            return None
        try:
            state = await self.conversation_state_manager.get_or_create(channel_id)
            if len(state.messages) == 0:
                await self._initialize_conversation_from_db(
                    channel_id, state, exclude_message_ids=exclude_message_ids)
            return state
        except Exception as e:
            logger.error(f"Failed to load conversation state for channel {channel_id}: {e}",
                         exc_info=True)
            return None

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
                    async with self.attachment_manager.attachment_db.db.execute(
                        "SELECT attachment_id, filename, attachment_type FROM attachments WHERE message_id = ?",
                        (str(db_msg.message_id),)
                    ) as cursor:
                        rows = await cursor.fetchall()

                    for row in rows:
                        attachment_id, filename, attachment_type = row

                        try:
                            if role != "user":
                                # image and file blocks are only valid in user turns
                                content_blocks.append({"type": "text", "text": f"\n[Attachment: {filename}]"})
                            elif attachment_type == "image":
                                file_data = await self.attachment_manager.get_attachment_for_processing(attachment_id)
                                if file_data and file_data["method"] == "base64":
                                    attachment_ids.append(attachment_id)
                                    content_blocks.append({"type": "text", "text": f"\n[Image: {filename}]"})
                                    content_blocks.append({
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": file_data["media_type"],
                                            "data": file_data["data"],
                                        },
                                    })
                            else:
                                # our own id, resolved per request (spec 4.6): no
                                # upload here for a file the model may never open
                                attachment_ids.append(attachment_id)
                                content_blocks.append({"type": "text", "text": f"\n[Document: {filename}]"})
                                content_blocks.append({"type": "attachment_ref", "attachment_id": attachment_id})

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
                if not (db_msg.content or "").strip():
                    continue  # system/sticker messages index with no text - nothing to seed
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

    def _workspace_for(self, message, server_id, channel_id):
        """This turn's /workspace (WorkspaceFiles), or None with the workshop off here."""
        from core.workshop.service import WorkshopUnavailable, WorkspaceFiles
        if self.workshop is None:
            return None
        channel = getattr(message, "channel", None)
        try:
            scope = self.workshop.scope_for_turn(
                server_id, channel_id, parent_id=getattr(channel, "parent_id", None),
                is_thread=isinstance(channel, discord.Thread))
        except WorkshopUnavailable:
            return None
        return WorkspaceFiles(self.workshop, scope)

    async def _workspace_attachments(self, paths: list, message, server_id, channel_id):
        """(discord.File list, notes): workspace files read as bytes, checked
        against Discord's size limit - the tool result says exactly what made it."""
        from core.workshop.backend import FileTooLarge
        workspace = self._workspace_for(message, server_id, channel_id)
        if workspace is None:
            return [], ["the workshop isn't running here, so nothing was attached"]
        guild = getattr(message, "guild", None)
        limit = getattr(guild, "filesize_limit", None) or 10 * 1024 * 1024
        files, notes = [], []
        for path in list(dict.fromkeys(paths))[:10]:
            try:
                data = await workspace.read(path, max_bytes=limit)   # sized before it's pulled
            except FileNotFoundError:
                notes.append(f"'{path}' doesn't exist in your workshop - it was not attached")
                continue
            except FileTooLarge as e:
                notes.append(f"'{path}' is {format_size(e.size)}, over Discord's "
                             f"{format_size(limit)} limit here - it was not attached")
                continue
            except Exception as e:   # the workshop's problem, reported - never a lost message
                logger.warning(f"attaching {path} failed: {e!r}")
                notes.append(f"'{path}' couldn't be read ({e}) - it was not attached")
                continue
            files.append(discord.File(io.BytesIO(data), filename=path.rsplit("/", 1)[-1]))
        return files, notes

    async def _execute_send_message(self, tool_input: dict,
                                    message: discord.Message,
                                    loop_result: ToolLoopResult) -> str:
        """
        Execute a send_message tool call (v0.12.0): deliver one message to
        the current channel mid-turn and return ground truth - the sent
        message id and exactly which files attached - so the model can never
        sustain a false belief about its own output.
        """
        content = (tool_input.get("content") or "").strip()
        if not content:
            return "Error: content is required - nothing was sent."

        channel = message.channel
        notes = []

        # Resolve the reply anchor, degrading to a standalone send
        reference = None
        reply_to = tool_input.get("reply_to_message_id")
        if reply_to:
            try:
                reference = await channel.fetch_message(int(reply_to))
            except (ValueError, discord.NotFound, discord.HTTPException):
                notes.append(f"reply target {reply_to} not found - sent standalone")

        # Workspace files to attach, read as bytes (never a mount)
        outgoing_files = []
        requested = tool_input.get("attach_outputs") or []
        if requested:
            server_id = str(message.guild.id) if message.guild else None
            outgoing_files, file_notes = await self._workspace_attachments(
                requested, message, server_id, str(channel.id))
            notes += file_notes

        # Deliver, fragmented texting-style; files ride the first fragment
        from .discord_client import fragment_message
        sent = None
        for i, chunk in enumerate(fragment_message(content)):
            try:
                sent = await channel.send(
                    chunk,
                    reference=reference if i == 0 else None,
                    files=outgoing_files if i == 0 and outgoing_files else None,
                )
            except discord.HTTPException as e:
                logger.error(f"send_message delivery failed: {e}")
                if sent is None:
                    return f"Error: Discord rejected the send ({e}) - nothing was sent."
                notes.append(f"a later fragment failed to send ({e})")
                break

        if sent is None:
            return "Error: nothing was sent (empty after fragmentation)."

        # Same registration the legacy text path does on first outbound (v0.9)
        await self._register_dm_surface(channel)

        loop_result.sent_message_ids.append(str(sent.id))
        loop_result.sent_texts.append(sent.content or "")
        loop_result.last_sent_message = sent
        loop_result.attached_files += len(outgoing_files)

        attached = [f.filename for f in outgoing_files]
        report = f"Sent (message id {sent.id})."
        if attached:
            report += f" Attached: {', '.join(attached)}."
        if notes:
            report += " Note: " + "; ".join(notes) + "."
        return report

    async def _build_attachment_tool_result(self, result, block_id, conversation_state, *,
                                            message=None, server_id=None, channel_id=None):
        """
        Convert a structured get_attachment result into (tool_result, file_block).

        Images inline into the tool_result (valid content). A document is not
        valid tool_result content: its attachment_ref rides in the same user
        message after the tool_results, and is persisted as its own annotated
        user message. Any other file goes into the workshop's inbox as bytes,
        and so does a document where the workshop is on (to merge, edit,
        convert - reading it is not enough). Where the workshop is off, a
        larger document is read instead, its ref marked read (0.14.1).
        """
        content_blocks = [{"type": "text", "text": result["text"]}]
        file_block = None
        file_data = result["file_data"]
        metadata = result["metadata"]
        read = (file_data.get("method") == "inbox" and self.attachment_manager is not None
                and self.attachment_manager.should_use_document_block(
                    metadata["filename"], metadata.get("size_bytes") or 0, read=True)
                and not self._workshop_on(message))

        if file_data.get("method") == "base64":
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": file_data["media_type"],
                    "data": file_data["data"],
                },
            })
        elif file_data.get("method") == "ref" or read:
            file_block = {"type": "attachment_ref", "attachment_id": metadata["attachment_id"]}
            if read:
                file_block["read"] = True
            content_blocks[0]["text"] += "\nFile attached to the conversation as a readable document."
            if conversation_state:
                conversation_state.add_message("user", [file_block], [metadata["attachment_id"]])
                logger.info(f"Added fetched attachment to conversation state: {metadata['filename']}")
            if not read and self._workshop_on(message):
                content_blocks[0]["text"] += " " + await self._copy_to_inbox(
                    metadata, message=message, server_id=server_id, channel_id=channel_id)
        elif file_data.get("method") == "inbox":
            if (UnifiedAttachmentManager.should_use_document_block(metadata["filename"], 0)
                    and not self._workshop_on(message)):
                # a document past even the larger limit, with nothing to open it in
                content_blocks[0]["text"] += (
                    f"\nIt's too large to read here (documents up to "
                    f"{format_size(UnifiedAttachmentManager.READ_DOCUMENT_MAX_BYTES)} can be), and "
                    f"the workshop, which could open it, is off here.")
            else:
                content_blocks[0]["text"] += "\n" + await self._copy_to_inbox(
                    metadata, message=message, server_id=server_id, channel_id=channel_id)

        tool_result = {"type": "tool_result", "tool_use_id": block_id, "content": content_blocks}
        return tool_result, file_block

    async def _copy_to_inbox(self, metadata: dict, *, message, server_id, channel_id) -> str:
        """A fetched non-document file, as bytes, into /workspace/inbox (the
        attachment's own vault check already ran in get_attachment)."""
        from core.workshop.service import WorkshopUnavailable
        if self.workshop is None:
            return "This kind of file opens in the workshop, which isn't running here."
        channel = getattr(message, "channel", None)
        try:
            scope = self.workshop.scope_for_turn(
                server_id, channel_id, parent_id=getattr(channel, "parent_id", None),
                is_thread=isinstance(channel, discord.Thread))
        except WorkshopUnavailable as e:
            return f"Couldn't put it in the workshop ({e})."
        return await copy_to_inbox(self.workshop, self.attachment_manager, metadata, scope)

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

    async def _reply_quote_prefix(self, message: discord.Message) -> str:
        """If this message replies to another, a short quote of the original so
        the model always knows what it's answering - however old the original is
        (pulled durably from message memory, falling back to Discord's cache)."""
        ref = getattr(message, "reference", None)
        if not ref or not getattr(ref, "message_id", None):
            return ""
        author, text = None, None
        try:
            orig = await self.message_memory.get_stored_message(
                str(ref.message_id), str(message.channel.id))
        except Exception:
            orig = None
        if orig is not None:
            author, text = orig.author_name, orig.content
        else:
            resolved = getattr(ref, "resolved", None)
            if resolved is not None and not isinstance(
                    resolved, discord.DeletedReferencedMessage):
                author = getattr(resolved.author, "display_name", None)
                text = resolved.content
        if not text:
            return ""
        text = " ".join(text.split())
        if len(text) > 280:
            text = text[:279] + "…"
        return f"[↩ replying to {author or 'someone'}: \"{text}\"]\n"

    async def _build_user_content(self, message: discord.Message, processed_attachments: list):
        """
        Discord message -> (content, attachment_ids) for conversation state.
        Attachment blocks follow the text block in processing order.
        """
        reply_prefix = await self._reply_quote_prefix(message)

        if not processed_attachments:
            text = reply_prefix + message.content
            return (text if reply_prefix else message.content), None

        # Empty text alongside attachments would be rejected by the API
        base = message.content if message.content.strip() else "[Attachment]"
        text_content = reply_prefix + base
        content = [{"type": "text", "text": text_content}]
        attachment_ids = []
        for att in processed_attachments:
            attachment_ids.append(att["attachment_id"])
            block = self.attachment_manager.build_content_block(
                att.get("for_api"), att["filename"], attachment_id=att["attachment_id"]
            )
            if block:
                content.append(block)
                logger.info(f"Added {block['type']} block for {att['filename']}")
        return content, attachment_ids

    async def _workshop_awareness(self, message) -> str:
        """One line per job running in this conversation's workshop scope."""
        server_id = str(message.guild.id) if message.guild else None
        scope = self.workshop.scope(server_id, str(message.channel.id),
                                    parent_id=getattr(message.channel, "parent_id", None),
                                    is_thread=isinstance(message.channel, discord.Thread))
        if scope is None:
            return ""
        jobs = await self.workshop.jobs.running(scope.key)
        if not jobs:
            return ""
        now = datetime.now(timezone.utc)
        lines = []
        for job in jobs[:5]:
            minutes = int((now - datetime.fromisoformat(job.started_at)).total_seconds() // 60)
            where = ""
            if job.home_channel_id != str(message.channel.id) and self.discord_client:
                channel = self.discord_client.get_channel(int(job.home_channel_id))
                where = f", from #{channel.name}" if channel is not None and getattr(channel, "name", None) else ""
            what = f"helper working on: {job.command[:80]}" if job.kind == "worker" else job.command[:80]
            if job.kind == "shell" and job.executor_key.startswith("worker:"):   # outlives its helper
                what += f" (started by helper {job.executor_key[len('worker:'):]}, not you)"
            lines.append(f"- {job.id}: {what} (running {minutes} min{where})")
        return ("<workshop_jobs>\n(running in your workshop; a note arrives when each finishes)\n"
                + "\n".join(lines) + "\n</workshop_jobs>")

    async def _build_volatile_tail(self, base: str, conversation_state, message=None,
                                   closing: str = "") -> str:
        """
        Per-request context (timestamp, momentum, attachment index, repository
        manifest) that must stay OUT of the cached prompt prefix. It rides as
        a transient user message appended at request time in _run_tool_loop -
        downstream of both cache breakpoints, never persisted to conversation
        state. closing, when given, is its last line.
        """
        parts = [base] if base else []

        # DMs: name where the mind lives (v0.9). Guild list changes rarely
        # but this whole message is per-request anyway - cache-safe by place.
        if message is not None and message.guild is None and self.list_servers:
            try:
                names = [n for n in self.list_servers() if n]
                server_list = ", ".join(names) if names else "your servers (none yet)"
                parts.append(PRIME_CONTEXT_TEMPLATE.format(server_list=server_list))
            except Exception as e:
                logger.error(f"Failed to build prime context: {e}")

        if conversation_state and self.attachment_manager:
            try:
                attachments_index = await self._generate_uploaded_files_manifest(conversation_state)
                if attachments_index:
                    parts.append(attachments_index)
            except Exception as e:
                logger.error(f"Failed to generate attachment index: {e}", exc_info=True)

        # The repository is per server: DMs have none (0.14 - their workshop
        # is their private drive)
        if conversation_state and self.repository_manager and message is not None and message.guild:
            try:
                repo_server_id = str(message.guild.id)
                in_context_ids = set()
                for msg in conversation_state.messages:
                    in_context_ids.update(msg.get("attachment_ids", []))
                repo_manifest = await self.repository_manager.render_manifest(
                    repo_server_id, in_context_ids
                )
                if repo_manifest:
                    parts.append(repo_manifest)
            except Exception as e:
                logger.error(f"Failed to generate repository manifest: {e}", exc_info=True)

        if getattr(self, "workshop", None) is not None and message is not None:
            try:
                awareness = await self._workshop_awareness(message)
                if awareness:
                    parts.append(awareness)
            except Exception as e:
                logger.error(f"Failed to build workshop awareness line: {e}")

        if getattr(self, "projects", None) is not None and message is not None:
            try:
                place = self._projects_place(message)
                if place:
                    listed = projects_manifest(self.projects.in_scope(*place),
                                               f"/memories/{self.config.bot_id}/{place[1]}")
                    if listed:
                        parts.append(listed)
            except Exception as e:
                logger.error(f"Failed to build projects manifest: {e}")

        # Recent message ids (v0.12.0): send_message reply_to targets. The
        # model can't anchor a reply to a message it can't name; ids ride
        # here because they're per-request noise everywhere else.
        if message is not None and getattr(self, "message_memory", None):
            try:
                recent = await self.message_memory.get_recent(
                    str(message.channel.id), limit=15)
                client = getattr(self, "discord_client", None)
                own_id = str(client.user.id) if (client and client.user) else None
                lines = []
                for m in recent:
                    # Anyone else's message is a valid reply target, other
                    # bots included (twins/Prime servers). Own messages show
                    # Discord's delivery record - ground truth against the
                    # model trusting its own prior attachment claims.
                    if m.is_system:
                        continue
                    snippet = " ".join(m.content.split())[:60]
                    if m.author_id == own_id:
                        marker = (" [attached a file]" if m.has_attachments
                                  else " [no file attached]")
                        lines.append(f"{m.message_id} — {m.author_name} (you)"
                                     f"{marker}: {snippet}")
                    else:
                        lines.append(f"{m.message_id} — {m.author_name}: {snippet}")
                if lines:
                    parts.append(
                        "<recent_messages>\n"
                        "(recent channel messages: ids for send_message "
                        "reply_to targeting, oldest first; your own messages "
                        "show what ACTUALLY attached, which may differ from "
                        "what they claim)\n"
                        + "\n".join(lines)
                        + "\n</recent_messages>"
                    )
            except Exception as e:
                logger.error(f"Failed to build recent-messages index: {e}")

        if closing:
            parts.append(closing)
        if not parts:
            return ""
        return (
            "<context_update>\n"
            "(Automated context, not a user message)\n"
            + "\n\n".join(parts)
            + "\n</context_update>"
        )

    async def _workshop_tool(self, block, call) -> dict:
        """A workshop tool's result, whatever happens in it: an unexpected
        error is the model's to see (and the log's), never a crashed turn."""
        if self.workshop is None:
            content, error = "The workshop isn't available here.", False
        else:
            try:
                content, error = await call(), False
            except Exception as e:
                logger.error(f"{block.name} failed unexpectedly: {e!r}", exc_info=True)
                content, error = (f"{block.name} hit an unexpected error ({type(e).__name__}: {e}). "
                                  f"Trying a different way may work; if it keeps happening, say so."), True
        result = {"type": "tool_result", "tool_use_id": block.id, "content": content}
        if error:
            result["is_error"] = True
        return result

    @staticmethod
    async def _no_workers() -> str:
        return "Delegating isn't available right now."

    def _workshop_on(self, message) -> bool:
        """Does this conversation's scope run the workshop (container setting)?"""
        if getattr(self, "workshop", None) is None or message is None:
            return False
        channel = message.channel
        server_id = str(message.guild.id) if message.guild else None
        parent_id = getattr(channel, "parent_id", None)
        scope = self.workshop.scope(server_id, str(channel.id), parent_id=parent_id,
                                    is_thread=isinstance(channel, discord.Thread))
        return self.workshop.setting(scope, server_id=server_id, channel_id=str(channel.id),
                                     parent_id=parent_id) == "container"

    def _build_request(self, system_blocks: list, conversation_state, context,
                          is_dm: bool = False, volatile_tail: str = "",
                          message=None) -> ModelRequest:
        """
        Assemble the request shared by both response paths: tools from the
        registry, message history, thinking and effort. Where the workshop
        runs, its paragraph and the skills catalog join the cached system
        prefix (both stable per channel; the catalog changes only when the
        skills folder does); per-request context rides volatile_tail.

        is_dm (v0.9): DM requests don't get ask_prime - a DM already IS the
        Prime - nor the repository, which is per server. Tool sets stay
        stable per channel, so the cache holds.
        """
        workshop = self._workshop_on(message)
        repository = self.repository_manager is not None and not is_dm
        if workshop:
            place = WORKSHOP_DM_LINE if is_dm else WORKSHOP_SHELF_LINE if repository else ""
            projects = f"/memories/{self.config.bot_id}/{self._projects_place(message)[1]}/projects"
            system_blocks[0]["text"] += "\n\n" + WORKSHOP_PROMPT.format(place=place, projects=projects)
        skills_prompt = self.skills.prompt(workshop=workshop)
        if skills_prompt:
            system_blocks[0]["text"] += "\n\n" + skills_prompt

        if not self.discord_tool_executor:
            logger.warning("Discord tool executor is None - discord tools NOT added!")
        mcp_tools = self.mcp_manager.get_tools_for_api() if self.mcp_manager else []
        if mcp_tools:
            logger.debug(f"Added {len(mcp_tools)} MCP tools to API request")

        model = self.config.api.model
        spec = self.driver.spec_for(model)
        tools, betas = assemble_tools(ToolContext(
            is_dm=is_dm,
            discord_tools=self.discord_tool_executor is not None,
            repository=repository,
            ask_prime=self.ask_prime_executor is not None,
            web_search=self.web_search_enabled,
            web_citations=self.config.get_web_search_config()["citations_enabled"],
            mcp_tools=mcp_tools,
            workshop=workshop,
            text_skills=bool(skills_prompt),
            # Documents resolve to Files API ids whenever the manager exists
            files=self.attachment_manager is not None,
        ), spec)

        if conversation_state:
            messages = conversation_state.get_messages_for_api()
            logger.debug(f"Using {len(messages)} messages from conversation state")
        else:
            messages = context["messages"].copy()
            logger.warning("Conversation state not available, falling back to context builder")

        return ModelRequest(
            model=model,
            # Derived, never user-set: as much output room as the input budget
            # allows, clamped to the model's real output ceiling
            max_tokens=min(self.config.api.context_tokens, spec.max_output),
            system=system_blocks,
            cache_system=True,
            messages=messages,
            cache_messages=True,
            volatile_tail=volatile_tail,
            tools=tools,
            betas=betas,
            thinking=self.config.api.thinking.enabled,
            effort=self.config.api.effort,
            stream=True,
            beta=True,
        )

    def _log_response_blocks(self, response, log_suffix: str = "") -> None:
        """Verbose per-block debug logging (active only with web search on)."""
        if not self.web_search_enabled:
            return
        logger.debug(f"Response has {len(response.content)} content blocks{log_suffix}")
        for i, block in enumerate(response.content):
            logger.debug(f"  Block {i}: type={block.type}")

            if hasattr(block, 'text'):
                logger.debug(f"    Text preview: {block.text[:200]}")

            if block.type == "web_fetch_tool_result":
                if hasattr(block, 'content') and hasattr(block.content, 'content'):
                    if hasattr(block.content.content, 'source') and hasattr(block.content.content.source, 'data'):
                        logger.debug(f"    web_fetch content: {block.content.content.source.data[:500]}")

            if block.type == "server_tool_use" and hasattr(block, 'name'):
                logger.info(f"Server tool used{log_suffix}: {block.name}")
                logger.debug(f"  Server tool {block.name} input: {block.input}")

    # ------------------------------------------------------------------
    # Turn events (v0.9): structured rows in messages.db, one per bot
    # turn-event. The supervisor's channel monitor reads these.
    # ------------------------------------------------------------------

    @staticmethod
    def _trigger_from(message, addressed: bool = False) -> dict:
        return {
            "user": message.author.display_name,
            "text": (message.content or "")[:500],
            "addressed": addressed,
        }

    @staticmethod
    def _event_payload(result: "ToolLoopResult", triggers: list, **extra) -> dict:
        # Every model call of the turn: a tool round is several calls, and
        # recording only the last undercounted those turns' cost
        usage = result.total_usage
        payload = {
            "triggers": triggers,
            "thinking": result.thinking_text or "",
            "tool_calls": result.tool_calls,
            # a reply sent through send_message is the turn's response too: the
            # monitor otherwise read those turns as "nothing needed a reply"
            "response": result.response_text or " / ".join(t for t in result.sent_texts if t) or None,
            "tokens": {
                "uncached_in": usage.input_tokens,
                "cache_read": usage.cache_read_input_tokens,
                "cache_write": usage.cache_creation_input_tokens,
                "out": usage.output_tokens,
            } if result.usage else {},
            **extra,
        }
        if result.dropped_text_chars:
            payload["dropped_text_chars"] = result.dropped_text_chars
        return payload

    async def _emit_turn_event(self, kind: str, message, result: "ToolLoopResult",
                               triggers: list, **extra) -> None:
        """Record one turn event; never breaks the pipeline (event loss is
        acceptable, broken turns are not)."""
        if self.message_memory is None:
            return
        try:
            payload = self._event_payload(result, triggers[-5:], **extra)
            await self.message_memory.add_event(
                kind,
                str(message.guild.id) if message.guild else None,
                str(message.channel.id),
                payload,
            )
        except Exception:
            logger.exception(f"Turn-event emit failed ({kind})")

    async def _execute_tool_blocks(self, response, message: discord.Message,
                                   conversation_state, request: ModelRequest,
                                   tool_calls_log: list = None,
                                   memory_write_grant: Optional[str] = None,
                                   loop_result: Optional[ToolLoopResult] = None):
        """
        Execute the client-side tool_use blocks in a response.

        Returns (tool_results, pending_file_blocks): pending_file_blocks are
        document refs from get_attachment that ride in the same user message
        AFTER the results.
        tool_calls_log (v0.9): caller-owned accumulator; every dispatched
        block lands there as {tool, action, detail} and (memory excepted -
        it has its richer line) in the conversations log.
        """
        channel_id = str(message.channel.id)
        server_id = str(message.guild.id) if message.guild else None

        tool_results = []
        pending_file_blocks = []
        channel = message.channel
        parent_id = getattr(channel, "parent_id", None)
        is_thread = isinstance(channel, discord.Thread)

        def note(tool: str, action: str, detail: str = ""):
            detail = detail[:120]
            if tool_calls_log is not None:
                tool_calls_log.append({"tool": tool, "action": action, "detail": detail})
            if tool != "memory":
                self.conversation_logger.log_tool_call(tool, action, detail)

        def kv(tool_input: dict, *exclude) -> str:
            return ", ".join(
                f"{k}={v}" for k, v in tool_input.items() if k not in exclude)

        for block in response.content:
            if block.type != "tool_use":
                continue
            if loop_result is not None:
                loop_result.ended_on_send = False   # send_message sets it back

            if block.name == "memory":
                command = block.input.get('command', 'unknown')
                # rename names its paths old_path/new_path
                path = block.input.get('path') or (
                    f"{block.input.get('old_path', '?')} -> {block.input.get('new_path', '?')}"
                    if command == "rename" else "unknown")
                logger.debug(f"Executing memory tool: {command} {path}")

                result = self.memory_tool_executor.execute(
                    block.input,
                    current_server_id=server_id,
                    current_channel_id=channel_id,
                    write_grant=memory_write_grant,
                    parent_id=str(parent_id) if parent_id else None,
                    is_thread=is_thread,
                )
                self.conversation_logger.log_memory_tool(command, path, result)
                note("memory", command, path)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result
                })

            # Progressive disclosure (v0.5.0)
            elif block.name == "request_skill":
                logger.info(f"Executing request_skill tool: {block.input.get('skill_name', 'unknown')}")
                note("skills", "request_skill", block.input.get("skill_name", ""))
                if self.message_memory:
                    await self.message_memory.add_event(
                        "skill", server_id, channel_id,
                        {
                            "triggers": [], "thinking": "", "tool_calls": [],
                            "response": None,
                            "skill_name": block.input.get("skill_name", ""),
                            "replace": block.input.get("replace"),
                        },
                    )
                result = run_request_skill(self.skills, block.input,
                                           workshop=any(t.name == "bash" for t in request.tools))

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result
                })

            elif block.name == "discord_tools":
                if self.discord_tool_executor:
                    logger.debug(f"Executing Discord tool: {block.input.get('command', 'unknown')}")
                    note("discord_tools", block.input.get("command", "unknown"),
                         kv(block.input, "command"))
                    result = await self.discord_tool_executor.execute(
                        block.input,
                        current_server_id=server_id,
                        current_channel_id=channel_id
                    )
                    # Structured data from get_attachment
                    if isinstance(result, dict) and result.get("_structured"):
                        tool_result, file_block = await self._build_attachment_tool_result(
                            result, block.id, conversation_state,
                            message=message, server_id=server_id, channel_id=channel_id,
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

            elif block.name == "repository":
                if self.repository_manager:
                    logger.debug(f"Executing repository tool: {block.input.get('action', 'unknown')}")
                    note("repository", block.input.get("action", "unknown"),
                         kv(block.input, "action"))
                    result = await self.repository_manager.execute(
                        block.input, current_server_id=server_id,
                        current_channel_id=channel_id,
                        workspace=self._workspace_for(message, server_id, channel_id),
                    )
                else:
                    result = "Error: repository feature not enabled"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result
                })

            # The workshop (0.14) - MUST precede the MCP "_" fallthrough
            # (str_replace_based_edit_tool and request_network_access hold
            # underscores). Each runs the workshop's own gate first.
            elif block.name in ("bash", EDITOR_NAME, "request_network_access"):
                runner, label, detail = {
                    "bash": (run_bash, "bash", (block.input.get("command") or "")[:120]),
                    EDITOR_NAME: (run_editor, block.input.get("command", "edit"), block.input.get("path", "")),
                    "request_network_access": (
                        lambda workshop, tool_input, **kw: run_request_network_access(
                            self.approvals, workshop, tool_input, **kw),
                        "request_network_access", block.input.get("host", "")),
                }[block.name]
                note("workshop", label, detail)
                tool_results.append(await self._workshop_tool(
                    block, lambda: runner(self.workshop, block.input, server_id=server_id,
                                          channel_id=channel_id, parent_id=parent_id,
                                          is_thread=is_thread)))

            elif block.name == "delegate":
                note("workshop", "delegate", (block.input.get("task") or "")[:120])
                tool_results.append(await self._workshop_tool(block, lambda: (
                    self.workers.delegate(block.input, server_id=server_id, channel_id=channel_id,
                                          parent_id=parent_id, is_thread=is_thread)
                    if self.workers is not None else self._no_workers())))

            # ask_prime (v0.9) - MUST precede the MCP "_" fallthrough
            elif block.name == "ask_prime":
                note("prime", "ask_prime", kv(block.input))
                if self.ask_prime_executor and server_id is not None:
                    result = await self.ask_prime_executor.execute(
                        block.input,
                        current_server_id=server_id,
                        current_channel_id=channel_id,
                    )
                else:
                    result = "ask_prime isn't available here."
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result
                })

            # Native messaging (v0.12.0) - MUST precede the MCP "_" fallthrough
            elif block.name == "send_message":
                preview = (block.input.get("content") or "")[:80]
                note("send_message",
                     "reply" if block.input.get("reply_to_message_id") else "send",
                     preview)
                if loop_result is not None:
                    sent_before = len(loop_result.sent_message_ids)
                    result = await self._execute_send_message(
                        block.input, message, loop_result)
                else:
                    result = "send_message isn't available in this context."
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result
                })
                if loop_result is not None:
                    loop_result.ended_on_send = len(loop_result.sent_message_ids) > sent_before

            # MCP tools are prefixed with their server name (v0.5.0)
            elif "_" in block.name and self.mcp_manager:
                logger.debug(f"Executing MCP tool: {block.name} with input: {block.input}")
                note("mcp", block.name, kv(block.input))
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

        return tool_results, pending_file_blocks

    async def _run_tool_loop(
        self,
        request: ModelRequest,
        message: discord.Message,
        conversation_state,
        fallback_text: Optional[str] = None,
        log_suffix: str = "",
        memory_write_grant: Optional[str] = None,
    ) -> ToolLoopResult:
        """
        Drive the model's tool-use loop to completion (both response paths).

        fallback_text marks a must-reply path: the result text is never empty
        (loop overruns, blank replies, and unexpected stops degrade to it).
        When None (periodic path), the same outcomes end with empty text,
        which the caller treats as silence.

        The request carries its own volatile tail (timestamp, momentum,
        attachment index): the driver appends it after the message cache
        breakpoint on every call, so per-request churn never invalidates the
        cached conversation prefix. It is never persisted.
        """
        result = ToolLoopResult(model=request.model)
        recovered_file_ids = set()  # stale file_ids already recovered this turn
        refused_documents = set()   # documents swapped for a note this turn
        unsaved_refusals = {}       # ...not yet in state: only after a call succeeds without them
        ran_server_tools = False    # once any call has, no later one measures the conversation
        loop_iteration = 0
        attachment_nudges = 0  # phantom-attachment guard, max 2 bounces per turn
        repeat_sig = None  # repeat-call guard: identical consecutive tool calls
        repeat_count = 0
        started = time.monotonic()
        workshop_turn = any(getattr(t, "name", None) == "bash" for t in (request.tools or []))
        cap = WORKSHOP_TOOL_LOOP_ITERATIONS if workshop_turn else MAX_TOOL_LOOP_ITERATIONS

        while True:
            loop_iteration += 1

            logger.debug(f"Request{log_suffix}: model={request.model}, betas={request.betas}")
            logger.debug(f"  Tools: {[t.name for t in request.tools]}")

            try:
                response = await self.driver.generate(request)
            except StaleFileError as e:
                # The Files API dropped a cached upload: forget the id and the
                # resolver re-uploads from local storage on the retry
                if (e.file_id not in recovered_file_ids and self.attachment_manager
                        and await self.attachment_manager.forget_file_id(e.file_id)):
                    recovered_file_ids.add(e.file_id)
                    continue
                # A stale id persisted before 0.14 (healing normally removes these)
                if conversation_state and e.file_id not in recovered_file_ids:
                    recovered_file_ids.add(e.file_id)
                    if await self._recover_stale_file_in_state(conversation_state, e.file_id):
                        request.messages = conversation_state.get_messages_for_api()
                        continue
                raise
            except DocumentRefusedError as e:
                # A document read on request was past what the provider takes:
                # a note replaces it and the call is retried once
                if e.attachment_id in refused_documents:
                    raise
                logger.warning(f"Document {e.attachment_id} refused ({e.reason}); replaced with a note")
                refused_documents.add(e.attachment_id)
                note = {"type": "text", "text": f"\n[A document read here ({e.attachment_id}) couldn't be "
                                                f"opened: {e.reason}]"}
                request.messages = without_ref(request.messages, e.attachment_id, note)
                unsaved_refusals[e.attachment_id] = note
                continue

            # ...and once a call without it succeeds, state too, or every later
            # turn in the channel would fail on it
            if unsaved_refusals and conversation_state:
                for attachment_id, note in unsaved_refusals.items():
                    conversation_state.messages = without_ref(conversation_state.messages, attachment_id, note)
                await self.conversation_state_manager.save(conversation_state)
            unsaved_refusals = {}

            result.usage = response.usage
            ran_server_tools = ran_server_tools or any(b.type == "server_tool_use" for b in response.content)
            if not ran_server_tools:
                result.context_usage = response.usage
            result.total_usage = result.total_usage + response.usage

            self.conversation_logger.log_tool_use_loop(loop_iteration, response.stop_detail)
            self._log_response_blocks(response, log_suffix)

            if loop_iteration == 1:
                usage = result.usage
                logger.info(
                    f"Input tokens: {total_input_tokens(usage):,} "
                    f"(uncached: {usage.input_tokens:,}, "
                    f"cache_read: {usage.cache_read_input_tokens:,}, "
                    f"cache_write: {usage.cache_creation_input_tokens:,})"
                )

            # Extract thinking (full block kept for persistence with signature)
            for block in response.content:
                if block.type == "thinking":
                    result.thinking_text += block.thinking
                    result.thinking_block = block

            if response.stop_reason == "tool_use":
                # Safety cap: a model that keeps requesting tools must not loop unbounded
                if loop_iteration >= cap:
                    logger.warning(f"Tool use loop exceeded {cap} iterations{log_suffix}")
                    if fallback_text and not result.did_send:
                        result.response_text = result.response_text or "I got stuck in a tool loop - try asking again."
                    break
                # Turn wall clock (spec 7.1): no new model calls past it; the
                # workshop's commands carry on as jobs and report back
                if time.monotonic() - started >= WORKSHOP_TURN_WALL_CLOCK_SECONDS:
                    logger.warning(f"Turn wall clock reached{log_suffix} - ending the turn")
                    if fallback_text and not result.did_send:
                        result.response_text = result.response_text or (
                            "That's taking longer than one go allows - anything still running "
                            "keeps going in the workshop, and I'll pick it up when it reports back.")
                    break
                result.tools_were_used = True

                tool_results, pending_file_blocks = await self._execute_tool_blocks(
                    response, message, conversation_state, request,
                    tool_calls_log=result.tool_calls,
                    memory_write_grant=memory_write_grant,
                    loop_result=result,
                )

                # Repeat-call guard (0.12.3): the gif saga showed the model
                # re-fetching the same attachment 8x when its real problem was
                # elsewhere. Detect identical consecutive tool calls and say so.
                sig = "|".join(sorted(
                    f"{b.name}:{json.dumps(b.input, sort_keys=True, default=str)}"
                    for b in response.content
                    if getattr(b, "type", None) == "tool_use"
                ))
                if sig and sig == repeat_sig:
                    repeat_count += 1
                else:
                    repeat_sig, repeat_count = sig, 0
                guard_note = []
                if repeat_count >= 2:
                    logger.warning(
                        f"Identical tool call repeated {repeat_count + 1}x"
                        f"{log_suffix} - injecting repeat note"
                    )
                    guard_note = [{"type": "text", "text": (
                        f"<system_note>You have now made this exact tool call "
                        f"{repeat_count + 1} times in a row with identical input, and it "
                        "returned the same result each time. The call is working - "
                        "repeating it will not change anything. Whatever you're stuck "
                        "on, the problem is elsewhere: reconsider your approach, or "
                        "say plainly what's blocking you.</system_note>"
                    )}]

                # Tool results ride in the next user message; fetched file
                # blocks come AFTER them (API: tool_result blocks must come first)
                request.messages.append({"role": "assistant", "content": response.content})
                request.messages.append({"role": "user", "content": tool_results + pending_file_blocks + guard_note})

                # Persist tool use and results to conversation state
                if conversation_state:
                    try:
                        conversation_state.add_tool_use_and_results(
                            assistant_content=response.persistable,
                            tool_results=tool_results,
                            model=result.model,
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
                            request.messages = conversation_state.get_messages_for_api()
                    except Exception as e:
                        logger.error(f"Failed to persist tool results to conversation state{log_suffix}: {e}", exc_info=True)

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

                # Send guard (0.13.1): text right after a send_message call
                # that posted is the double-post pattern (send, then restate
                # it). It's held back - never posted, never persisted - and
                # counted. Text after a failed send, after other work, or after
                # server tools in this response (search, code) still goes out.
                if any(getattr(b, "type", None) == "server_tool_use" for b in response.content):
                    result.ended_on_send = False
                if result.ended_on_send and result.response_text.strip():
                    logger.info(f"Dropped trailing text after send_message{log_suffix}: "
                                f"{result.response_text[:120]!r}")
                    result.dropped_text_chars = len(result.response_text)
                    result.response_text = ""

                # Tools ran but the model went quiet: reprompt once for a
                # brief confirmation (unless it already sent via send_message
                # - sending WAS the response)
                if (not result.response_text.strip() and result.tools_were_used
                        and not result.did_send
                        and loop_iteration < cap):
                    logger.warning(f"Tools used but no text response{log_suffix} - reprompting (iteration {loop_iteration})")
                    request.messages.append({"role": "assistant", "content": response.content})
                    request.messages.append({
                        "role": "user",
                        "content": [{"type": "text", "text": "Please provide a brief response confirming what you just did for the user."}]
                    })
                    continue

                # Phantom-attachment guard (v0.11.3, hardened 0.12.1): the
                # model sometimes announces a file it never produced, then
                # trusts its own claim next turn. Bounce up to twice with
                # escalating notes; a third strike never ships the claim on
                # the periodic path (silence beats a lie).
                if (result.response_text and not result.attached_files
                        and not result.did_send
                        and re.search(r"\battach(?:ed|ment|ments|ing)?\b",
                                      result.response_text, re.IGNORECASE)):
                    if (attachment_nudges < 2
                            and loop_iteration < cap):
                        if attachment_nudges == 0:
                            note_text = (
                                "<system_note>Your reply mentions an attached file, "
                                "but nothing is attached to it - a plain reply carries "
                                "no files. A file reaches Discord only through the "
                                "send_message tool, with its /workspace path in "
                                "attach_outputs. Either send it that way now, or reword "
                                "your reply so it doesn't claim an attachment.</system_note>"
                            )
                        else:
                            note_text = (
                                "<system_note>Reality check: nothing has been attached "
                                "this turn, and your reply still claims an attachment. "
                                "Earlier messages of yours may also claim attachments "
                                "that never happened - the <recent_messages> map shows "
                                "what actually attached. Do not describe a file as "
                                "attached. Either deliver it via send_message "
                                "attach_outputs, or say plainly that you don't have "
                                "it.</system_note>"
                            )
                        attachment_nudges += 1
                        logger.warning(
                            f"Response claims an attachment but no files are queued"
                            f"{log_suffix} - bounce {attachment_nudges}/2"
                        )
                        request.messages.append({"role": "assistant", "content": response.content})
                        request.messages.append({
                            "role": "user",
                            "content": [{"type": "text", "text": note_text}]
                        })
                        result.response_text = ""
                        continue
                    if fallback_text is None:
                        logger.warning(
                            f"Attachment claim persisted after {attachment_nudges} "
                            f"bounces{log_suffix} - suppressing response"
                        )
                        result.response_text = ""
                        break
                    logger.warning(
                        f"Attachment claim persisted after {attachment_nudges} "
                        f"bounces on the must-reply path{log_suffix} - shipping anyway"
                    )

                # Must-reply is satisfied by tool sends too (v0.12.0)
                if not result.response_text and fallback_text and not result.did_send:
                    result.response_text = fallback_text
                break

            elif response.stop_reason == "pause" and loop_iteration < cap:
                # A long server-tool turn (searches, fetches) paused: send its
                # content back as is and it carries on. It must end the
                # request, so the tail it was generated after becomes a real
                # message before it, where the model saw it
                logger.info(f"Server tools paused the turn{log_suffix} - resuming")
                if request.volatile_tail:
                    request.messages.append({"role": "user", "content": [
                        {"type": "text", "text": request.volatile_tail}]})
                    request.volatile_tail = ""
                request.messages.append({"role": "assistant", "content": response.content})
                continue

            else:
                logger.warning(f"Unexpected stop_reason{log_suffix}: {response.stop_detail}")
                if fallback_text and not result.did_send:
                    result.response_text = fallback_text
                break

        return result

    async def _register_dm_surface(self, channel) -> None:
        """
        Remember an outbound DM surface in the dm_channels registry (v0.9).

        channel.recipient is only populated on DMChannel (None on all other
        types), and on interaction-sourced channels it can resolve to the BOT
        itself - never register the bot as its own DM partner (a self row
        poisons dm_partner() and routes DM memory to the wrong tree).
        """
        if not (isinstance(channel, discord.DMChannel)
                and channel.recipient is not None
                and self.user_cache is not None):
            return
        bot_user = self.discord_client.user if self.discord_client else None
        if bot_user is not None and channel.recipient.id == bot_user.id:
            return
        await self.user_cache.set_dm_channel(
            str(channel.recipient.id), str(channel.id)
        )

    async def _send_response_chunks(
        self,
        channel,
        response_text: str,
        reference: Optional[discord.Message] = None,
    ) -> Optional[discord.Message]:
        """
        Send a response to Discord, split to the message-length limit. Files
        never ride here: they go out through send_message (0.14).

        Returns the last successfully sent message, or None if the first
        chunk could not be delivered at all.
        """
        from .discord_client import fragment_message
        message_chunks = fragment_message(response_text)

        sent_message = None
        for i, chunk in enumerate(message_chunks):
            try:
                if i == 0:
                    sent_message = await channel.send(chunk, reference=reference)
                else:
                    sent_message = await channel.send(chunk)
                # Register the DM surface on the first successful outbound send (v0.9)
                if i == 0:
                    await self._register_dm_surface(channel)
            except discord.HTTPException as e:
                if i == 0 and reference is not None:
                    # Reply target may have been deleted - retry standalone
                    try:
                        logger.warning(f"Failed to send reply, trying standalone: {e}")
                        sent_message = await channel.send(chunk)
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

            # All-tool-send turns (v0.12.0) may leave no final content; the
            # sends themselves are already in state via tool_use/tool_result
            if assistant_content:
                conversation_state.add_message("assistant", assistant_content,
                                               model=result.model or None)

            removed_count = conversation_state.enforce_message_cap()
            if removed_count > 0:
                logger.info(f"Message cap enforced after response: removed {removed_count} oldest messages")

            # Record session usage watermark and stub old tool results (v0.6.0)
            if result.context_usage is not None:
                conversation_state.record_usage(total_input_tokens(result.context_usage), seed_epoch)
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
                    self.episode_manager.episodize_channel(channel_id, force=True, reason="ceiling")
                ))

        except Exception as e:
            logger.error(f"Failed to update conversation state: {e}", exc_info=True)

    def _start_engagement_tracking(self, sent_message, channel) -> None:
        """Record the response for rate limiting and schedule the engagement check."""
        self.rate_limiter.record_response(str(channel.id))

        delay = self._rate_limiting_config["engagement_tracking_delay"]
        self.conversation_logger.log_engagement_tracking(started=True, delay_seconds=delay)
        self.conversation_logger.log_separator()

        self._track_task(asyncio.create_task(
            self._track_engagement(sent_message.id, channel, delay=delay)
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

        # Log incoming message
        self.conversation_logger.log_user_message(
            author=message.author.display_name,
            channel=getattr(message.channel, "name", "DM"),
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
            # One turn per channel, from reading the state to saving the
            # reply: the next message in this channel waits, other channels
            # don't
            async with self.channel_locks.lock(channel_id):
                # In-flight mentions (incl. this one) get appended by their
                # own handlers - seeding them here would duplicate them
                conversation_state = await self._load_conversation_state(
                    channel_id, exclude_message_ids=list(self._responded_messages))
                # Messages that came before this mention but haven't been
                # scanned in yet go first, so the mention can't overtake them
                await self._ingest_channel_backlog(channel_id, before_message_id=message.id)

                # Exclude messages currently being processed to prevent seeing
                # other pending @mentions
                context = await self.context_builder.build_context(
                    message,
                    exclude_message_ids=list(self._responded_messages)
                )

                # Add current user message to conversation state BEFORE API call
                if conversation_state:
                    try:
                        user_content, attachment_ids = await self._build_user_content(message, processed_attachments)
                        conversation_state.add_message("user", user_content, attachment_ids)
                        conversation_state.enforce_message_cap()
                    except Exception as e:
                        logger.error(f"Failed to add user message to conversation state: {e}", exc_info=True)

                if context.get("stats"):
                    self.conversation_logger.log_context_building(**context["stats"])

                async with message.channel.typing():
                    await asyncio.sleep(1.5)  # small delay for a more natural feel

                    # One cached system block; per-request context rides the
                    # volatile tail so it never breaks the cached prefix
                    system_blocks = [{"type": "text", "text": context["system_prompt"]}]
                    volatile_tail = await self._build_volatile_tail(
                        context.get("time_context", ""), conversation_state, message
                    )

                    request = self._build_request(
                        system_blocks, conversation_state, context,
                        is_dm=message.guild is None, volatile_tail=volatile_tail, message=message)
                    # Usage recorded later must match the session we measured
                    seed_epoch = conversation_state.seed_epoch if conversation_state else 0

                    try:
                        result = await self._run_tool_loop(
                            request, message, conversation_state,
                            fallback_text="I'm not sure how to respond to that.",
                        )
                    except Exception as e:
                        # Must-reply path: one retry covers transient API
                        # failures (overload, dropped stream) before giving up
                        logger.warning(f"Tool loop failed for @mention, retrying once: {e}")
                        await asyncio.sleep(3)
                        result = await self._run_tool_loop(
                            request, message, conversation_state,
                            fallback_text="I'm not sure how to respond to that.",
                        )

                # Final text may be empty when the model already delivered
                # everything via send_message (v0.12.0)
                sent_message = None
                if result.response_text.strip():
                    sent_message = await self._send_response_chunks(
                        message.channel, result.response_text, reference=message,
                    )
                    if sent_message is None and not result.did_send:
                        self.conversation_logger.log_separator()
                        return

                # Log thinking trace (if present) and bot response
                if result.thinking_text:
                    self.conversation_logger.log_thinking(result.thinking_text, len(result.thinking_text))
                self.conversation_logger.log_bot_response(
                    result.response_text or f"[Sent {len(result.sent_message_ids)} message(s) via send_message]",
                    len(result.response_text))

                await self._persist_assistant_response(conversation_state, channel_id, result, seed_epoch)

            await self._emit_turn_event(
                "dm" if message.guild is None else "mention",
                message, result, [self._trigger_from(message, addressed=True)],
            )

            last_sent = sent_message or result.last_sent_message
            if last_sent:
                self._start_engagement_tracking(last_sent, message.channel)

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

    async def handle_memory_command(self, channel, user, kind: str, text: str) -> None:
        """
        /memory show|remember|forget|feedback - the consent gate through the
        DM vault (v0.9, spec §2). The handler never writes files itself:
        remember/forget/feedback inject a structured intent into the DM
        conversation and run the normal pipeline with a one-shot grant that
        lets the memory tool write the caller's own global profile. show is
        modelless: the profile file, verbatim.
        """
        channel_id = str(channel.id)

        # The registry row must exist before memory routing resolves partner
        if self.user_cache:
            await self.user_cache.set_dm_channel(str(user.id), channel_id)

        if kind == "show":
            profile_path = self.memory_manager.get_global_user_profile_path(str(user.id))
            fs_path = self.memory_manager.resolve_path(profile_path)
            if fs_path.exists():
                content = fs_path.read_text(encoding="utf-8").strip()
            else:
                content = ""
            if content:
                await self._send_response_chunks(
                    channel, f"here's everything I have on you, verbatim:\n```\n{content}\n```")
            else:
                await self._send_response_chunks(
                    channel, "nothing written down about you yet - we're starting fresh.")
            return

        intent = memory_intent(kind, text, user.display_name)
        synthetic = _SyntheticDMMessage(channel=channel, author=user, content=intent)

        self.conversation_logger.log_user_message(
            author=user.display_name, channel="DM",
            content=f"/memory {kind} {text}".strip(), is_mention=True)

        try:
            async with self.channel_locks.lock(channel_id):
                conversation_state = await self._load_conversation_state(channel_id)
                context = await self.context_builder.build_context(synthetic)

                if conversation_state:
                    # The consent moment survives in history
                    conversation_state.add_message("user", intent)
                    conversation_state.enforce_message_cap()

                system_blocks = [{"type": "text", "text": context["system_prompt"]}]
                volatile_tail = await self._build_volatile_tail(
                    context.get("time_context", ""), conversation_state, synthetic)

                request = self._build_request(
                    system_blocks, conversation_state, context, is_dm=True,
                    volatile_tail=volatile_tail, message=synthetic)
                seed_epoch = conversation_state.seed_epoch if conversation_state else 0

                result = await self._run_tool_loop(
                    request, synthetic, conversation_state,
                    fallback_text="got it.",
                    memory_write_grant=str(user.id),
                )

                if result.response_text.strip():
                    await self._send_response_chunks(channel, result.response_text)

                if result.thinking_text:
                    self.conversation_logger.log_thinking(result.thinking_text, len(result.thinking_text))
                self.conversation_logger.log_bot_response(result.response_text, len(result.response_text))

                await self._persist_assistant_response(conversation_state, channel_id, result, seed_epoch)

            await self._emit_turn_event(
                "memory", synthetic, result,
                [{"user": user.display_name,
                  "text": f"/memory {kind} {text}"[:500], "addressed": True}],
                provenance=f"consent write - /memory {kind}",
            )

        except Exception as e:
            logger.error(f"Error handling /memory {kind}: {e}", exc_info=True)
            self.conversation_logger.log_error(f"/memory error: {str(e)}")
            await channel.send("something went sideways handling that - try again in a moment?")

    async def _track_engagement(
        self, message_id: int, channel: "discord.abc.Messageable", delay: int
    ):
        """
        Track engagement on bot message.

        Checks for reactions or follow-up messages at escalating intervals
        (delay, 4x, 10x - e.g. 30s/2min/5min) so long-form posts get human
        reading time before being judged ignored. ENGAGED records at the
        first check that finds anything; IGNORED only after the last.

        Args:
            message_id: Discord message ID to track
            channel: Channel where message was sent
            delay: Seconds to wait before the first check
        """
        channel_id = str(channel.id)
        check_at = [delay, delay * 4, delay * 10]

        elapsed = 0
        for checkpoint in check_at:
            await asyncio.sleep(checkpoint - elapsed)
            elapsed = checkpoint

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
            reaction_details = [
                f"{reaction.emoji}×{reaction.count}" for reaction in message.reactions
            ]
            has_reactions = bool(reaction_details)

            # Check for follow-up activity after the bot's message
            has_replies = await self._check_for_replies(message, channel)

            if has_reactions or has_replies:
                self.rate_limiter.record_engagement(channel_id)

                # Build detailed method string
                if has_reactions and has_replies:
                    method = f"reactions ({', '.join(reaction_details)}) + replies"
                elif has_reactions:
                    method = f"reactions ({', '.join(reaction_details)})"
                else:
                    method = "replies"

                self.conversation_logger.log_engagement_result(engaged=True, method=method)
                logger.debug(f"Message {message_id}: ENGAGED - {method} (at {checkpoint}s)")
                return

        self.rate_limiter.record_ignored(channel_id)
        self.conversation_logger.log_engagement_result(engaged=False)
        logger.debug(f"Message {message_id}: IGNORED (no reactions or replies)")

    async def _check_for_replies(
        self, message: discord.Message, channel: "discord.abc.Messageable"
    ) -> bool:
        """
        Check if anyone engaged after bot's message.

        Detects engagement via:
        - Formal Discord replies to bot's message
        - ANY human message in the channel afterwards (loose engagement -
          in the small servers this bot lives in, follow-up chatter after a
          bot message is response, not coincidence; the model's own scan
          judgment handles being talked past)

        Args:
            message: Bot's message to check engagement for
            channel: Channel to search

        Returns:
            True if engagement detected (reply or any human message)
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

                # Any human message after the bot's (loose engagement)
                if not msg.author.bot:
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

        # The workshop's own tasks, before the states they write close. Its
        # sandboxes stay up: jobs survive a restart, idle ones stop themselves
        for part in (self.workers, self.approvals, self.job_watcher, self.workshop):
            if part is not None:
                try:
                    await part.close()
                except Exception as e:
                    logger.error(f"Error closing {type(part).__name__}: {e}")

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

                # PHASE 1: Process all messages (add to conversation state)
                channel_triggers = await self._ingest_pending()

                # PHASE 2: Make one response decision per channel
                for channel_id, triggers in channel_triggers.items():
                    try:
                        await self._decide_channel_response(channel_id, triggers)
                    except Exception as e:
                        logger.error(f"Error deciding response for channel {channel_id}: {e}", exc_info=True)

        except asyncio.CancelledError:
            logger.info("Periodic check loop cancelled")
        except Exception as e:
            logger.error(f"Fatal error in periodic check loop: {e}", exc_info=True)

    async def _ingest_pending(self) -> dict:
        """
        Phase 1 of a scan: add every pending message to its channel's state.
        Returns {channel_id: [trigger, ...]} - the weighed messages, which
        become the scan/silent turn-event triggers (v0.9).

        A channel whose turn is still running keeps its messages pending for
        the next tick, so nothing lands between the turn's persisted tool
        pairs and its reply; ingestion holds the lock itself while it writes.
        """
        messages_to_check = self.pending_messages.copy()
        self.pending_messages.clear()
        logger.debug(f"Processing {len(messages_to_check)} pending messages")

        channel_triggers: dict = {}
        deferred, deferred_channels = [], set()
        for channel_id, message_id in messages_to_check:
            # Once a channel defers, its later messages defer too, or a
            # turn ending mid-pass would let them overtake the deferred ones
            if channel_id in deferred_channels or self.channel_locks.busy(channel_id):
                deferred_channels.add(channel_id)
                deferred.append((channel_id, message_id))
                continue
            try:
                async with self.channel_locks.lock(channel_id):
                    trigger = await self._process_message_to_state(channel_id, message_id)
                if trigger:
                    channel_triggers.setdefault(channel_id, []).append(trigger)
            except Exception as e:
                logger.error(f"Error processing message {message_id} in channel {channel_id}: {e}", exc_info=True)

        # Deferred messages are older than anything that arrived during the
        # pass, so they go back in front
        self.pending_messages[:0] = deferred
        logger.debug(f"Phase 1 complete: {len(channel_triggers)} channels with updates")
        return channel_triggers

    async def _ingest_channel_backlog(self, channel_id: str, before_message_id: int) -> None:
        """Claim and ingest this channel's pending messages older than
        before_message_id. Call with the channel's lock held."""
        mine = [p for p in self.pending_messages
                if p[0] == channel_id and int(p[1]) < int(before_message_id)]
        if not mine:
            return
        self.pending_messages = [p for p in self.pending_messages if p not in mine]
        for cid, message_id in mine:
            try:
                await self._process_message_to_state(cid, message_id)
            except Exception as e:
                logger.error(f"Error ingesting backlog message {message_id}: {e}", exc_info=True)

    async def _process_message_to_state(self, channel_id: str, message_id: int) -> bool:
        """
        Process a specific message and add it to conversation state.

        Bug #7 fix (revised): Phase 1 processing - just add message to state, no response decision.

        Args:
            channel_id: Discord channel ID
            message_id: Specific Discord message ID to process

        Returns:
            Trigger dict ({user, text, addressed}) if processed, None otherwise.
        """
        if not self.discord_client:
            logger.warning("Discord client not set, cannot process message")
            return None

        # Get Discord channel object
        channel = self.discord_client.get_channel(int(channel_id))
        if not channel:
            logger.warning(f"Channel {channel_id} not found")
            return None

        # Fetch the specific message by ID
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            logger.warning(f"Message {message_id} not found (may have been deleted)")
            return None
        except discord.Forbidden:
            logger.warning(f"No permission to fetch message {message_id}")
            return None
        except Exception as e:
            logger.error(f"Error fetching message {message_id}: {e}", exc_info=True)
            return None

        # Skip if message is from the bot
        if message.author == self.discord_client.user:
            logger.debug(f"Message {message_id} is from bot, skipping processing")
            return None

        # Load or create conversation state for this channel
        if not self.conversation_state_manager:
            logger.warning("No conversation state manager, cannot process message")
            return None

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
            user_content, attachment_ids = await self._build_user_content(message, processed_attachments)

            # Sticker-only and pin/system messages carry no usable content -
            # an empty text block would 400 every later request on this channel
            if isinstance(user_content, str) and not user_content.strip():
                logger.debug(f"Skipping contentless message {message_id} (system/sticker-only)")
                return None

            # Add message to conversation state
            conversation_state.add_message("user", user_content, attachment_ids)
            logger.info(f"Added message {message_id} to conversation state (attachments: {len(attachment_ids) if attachment_ids else 0})")

            # Save state immediately (Bug #7 fix for persistence)
            await self.conversation_state_manager.save(conversation_state)
            logger.debug(f"Saved conversation state after processing message {message_id}")

            return self._trigger_from(message)

        except Exception as e:
            logger.error(f"Failed to process message {message_id} to state: {e}", exc_info=True)
            return None

    async def _decide_channel_response(self, channel_id: str, triggers: list = None):
        """
        Make response decision for a channel (Phase 2 of periodic check).

        Bug #7 fix (revised): Called after all messages processed to make ONE decision per channel.

        Args:
            channel_id: Discord channel ID
            triggers: the weighed messages from Phase 1 (turn-event payload)
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
        await self._call_claude_for_response_decision(latest_message, context, triggers)

    async def run_note_turn(self, channel_id: str) -> None:
        """A may-stay-silent turn about a persisted note (a finished job, an
        approval) rather than a message. The carrier is the channel's real
        latest message, so guild, ids and DM detection are real
        (_SyntheticDMMessage would make it a DM turn); the periodic guards
        that exist because a scan is about its target are bypassed."""
        if not self.discord_client:
            return
        channel = self.discord_client.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.discord_client.fetch_channel(int(channel_id))
            except discord.HTTPException as e:
                logger.warning(f"Note-driven turn: channel {channel_id} unavailable ({e})")
                return
        latest = None
        async for m in channel.history(limit=1):
            latest = m
        if latest is None:
            logger.warning(f"Note-driven turn: no message to anchor in {channel_id}")
            return
        context = await self.context_builder.build_context(latest)
        context["conversation_momentum"] = await self._calculate_conversation_momentum(channel_id)
        await self._call_claude_for_response_decision(
            latest, context, [{"user": "workshop", "text": "a note arrived", "addressed": False}],
            note_driven=True)

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

    async def _call_claude_for_response_decision(self, message: discord.Message, context,
                                                 triggers: list = None, note_driven: bool = False):
        """
        Call Claude API to decide if bot should respond and generate response if yes.

        Args:
            message: Discord message object (latest in channel)
            context: Built context from ContextBuilder
            triggers: weighed messages from this scan (turn-event payload)
        """
        channel_id = str(message.channel.id)
        triggers = triggers or [self._trigger_from(message)]

        # Log periodic check decision attempt
        self.conversation_logger.log_user_message(
            author="[PERIODIC CHECK]",
            channel=getattr(message.channel, "name", "DM"),
            content=f"Scanning conversation (momentum: {context['conversation_momentum'].upper()})",
            is_mention=False
        )
        if context.get("stats"):
            self.conversation_logger.log_context_building(**context["stats"])

        # One turn per channel, from reading the state to saving the reply
        async with self.channel_locks.lock(channel_id):
            # Another turn may have held the channel while this scan waited:
            # if anyone (the bot included) spoke since the target, there's
            # nothing left to decide about it - newer messages get their own
            # scan. A note-driven turn is about its note, not a target.
            try:
                newest = None
                if not note_driven:
                    async for m in message.channel.history(limit=1):
                        newest = m
                if newest is not None and newest.id != message.id:
                    logger.info(f"Skipping scan in {channel_id}: the conversation moved past "
                                f"target {message.id} while it waited")
                    return
            except discord.HTTPException:
                pass  # can't verify; decide anyway

            # Messages are already in state (with attachments) from Phase 1
            # processing; seeding only happens when the state is empty
            conversation_state = await self._load_conversation_state(channel_id)
            system_blocks = self._build_response_decision_prompt(context)
            # Momentum is recomputed every check - volatile tail, not system
            volatile_tail = await self._build_volatile_tail(
                (context.get("time_context", "")
                 + f"\nConversation momentum right now: {context['conversation_momentum'].upper()}"),
                conversation_state,
                message,
                closing=NOTE_TURN_LINE if note_driven else "",
            )

            request = self._build_request(
                system_blocks, conversation_state, context,
                is_dm=message.guild is None, volatile_tail=volatile_tail, message=message)
            # Usage recorded later must match the session we measured
            seed_epoch = conversation_state.seed_epoch if conversation_state else 0

            # Show typing while generating (auto-refreshes until cancelled)
            typing_task = asyncio.create_task(self._keep_typing(message.channel))

            try:
                result = await self._run_tool_loop(
                    request, message, conversation_state, log_suffix=" (periodic)",
                )
                if note_driven and result.response_text.strip() == NOTE_TURN_SILENT:
                    result.response_text = ""

                # Empty text and no tool sends = Claude decided to stay silent
                if not result.response_text.strip() and not result.did_send:
                    if result.tools_were_used:
                        logger.warning(f"Claude used tools but returned no text response in channel {channel_id}")
                    else:
                        logger.info(f"Claude decided not to respond in channel {channel_id}")
                    if result.thinking_text:
                        self.conversation_logger.log_thinking(result.thinking_text, len(result.thinking_text))
                    self.conversation_logger.log_bot_response("[No response - staying silent]", 0)
                    self.conversation_logger.log_separator()
                    await self._emit_turn_event(
                        "silent", message, result, triggers, scan_count=len(triggers),
                        **({"note_driven": True} if note_driven else {}))
                    return

                # Staleness guard: generation takes tens of seconds and the
                # conversation may have moved on - a reply aimed at an old
                # message lands as a non sequitur. Drop it (nothing sent,
                # nothing persisted); the next tick re-evaluates fresh.
                # Tool-sent messages are already out (and can anchor via
                # reply_to), so a turn that sent skips the discard.
                if not result.did_send and not note_driven:
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
                self.conversation_logger.log_bot_response(
                    result.response_text or f"[Sent {len(result.sent_message_ids)} message(s) via send_message]",
                    len(result.response_text))

                # Send remaining final text as standalone (not a reply)
                sent_message = None
                if result.response_text.strip():
                    sent_message = await self._send_response_chunks(
                        message.channel, result.response_text,
                    )
                    if sent_message is None and not result.did_send:
                        return

                # Persist only what was actually delivered
                await self._persist_assistant_response(conversation_state, channel_id, result, seed_epoch)

                await self._emit_turn_event(
                    "scan", message, result, triggers, scan_count=len(triggers),
                    **({"note_driven": True} if note_driven else {}))

                last_sent = sent_message or result.last_sent_message
                if last_sent:
                    self._start_engagement_tracking(last_sent, message.channel)

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
        # check) - it rides in the <context_update> tail; the cached prefix
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

**Conversation Momentum** (the CURRENT level is given in the <context_update> at the end of the conversation):
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
- When your response answers ONE specific message in a batch of several, prefer the send_message tool with reply_to_message_id so it lands anchored to that message
- send_message can be called more than once in a turn (messages arrive in order) - e.g. an anchored reply to one person, then a separate message for the others. If you've sent everything that way, output nothing afterward

DO NOT explain your reasoning for responding or not responding. DO NOT output meta-commentary about the conversation. Either respond naturally or output nothing.
"""

        # Single cached block; volatile values (timestamp, momentum) ride the
        # request-time tail built by the caller, never a system block
        return [{"type": "text", "text": context["system_prompt"] + decision_criteria}]

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

    def schedule_expedited_scan(self, channel_id: str, delay: float = 10.0):
        """
        Schedule a soft-reply scan: a reply to the bot deserves prompt
        consideration (not the full periodic-check wait) without forcing a
        response the way an explicit @mention does. One in flight per channel.
        """
        if channel_id in self._expedited_scans:
            return
        self._expedited_scans.add(channel_id)
        self._track_task(asyncio.create_task(self._expedited_scan(channel_id, delay)))

    async def _expedited_scan(self, channel_id: str, delay: float):
        """Drain this channel's pending messages early and run one scan decision."""
        try:
            await asyncio.sleep(delay)

            # A turn holds the channel: the periodic loop picks these up
            # once it's done
            if self.channel_locks.busy(channel_id):
                return

            # Claim this channel's pending entries (no await between the two
            # list operations - atomic on the event loop)
            mine = [p for p in self.pending_messages if p[0] == channel_id]
            if not mine:
                return  # periodic loop already handled them
            self.pending_messages = [p for p in self.pending_messages if p[0] != channel_id]

            triggers = []
            async with self.channel_locks.lock(channel_id):
                for cid, message_id in mine:
                    try:
                        trigger = await self._process_message_to_state(cid, message_id)
                        if trigger:
                            triggers.append(trigger)
                    except Exception as e:
                        logger.error(f"Expedited scan: error processing message {message_id}: {e}", exc_info=True)

            if triggers:
                await self._decide_channel_response(channel_id, triggers)
        except Exception as e:
            logger.error(f"Expedited scan failed for channel {channel_id}: {e}", exc_info=True)
        finally:
            self._expedited_scans.discard(channel_id)
