"""
Discord Client Integration

Handles Discord.py setup, event handlers, and bot lifecycle.
"""

import discord
import asyncio
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import BotConfig
    from .reactive_engine import ReactiveEngine
    from .agentic_engine import AgenticEngine
    from .message_memory import MessageMemory
    from .user_cache import UserCache
    from .conversation_logger import ConversationLogger
    from .memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def iter_backfill_channels(guild):
    """Every readable history surface in a guild: text channels, their threads
    (active + archived - TextChannel.history() does NOT include thread
    messages), and voice-channel text chats (VoiceChannel is Messageable).

    Archived-thread enumeration is one HTTP call per text channel and needs
    read_message_history; failures skip that channel's archive quietly.
    """
    for channel in guild.text_channels:
        yield channel
        for thread in getattr(channel, "threads", []):
            yield thread
        try:
            async for thread in channel.archived_threads(limit=None):
                yield thread
        except Exception as e:
            logger.debug(f"Archived threads unavailable for #{channel.name}: {e}")
    for channel in guild.voice_channels:
        yield channel


def split_message(text: str, max_length: int = 2000) -> list[str]:
    """
    Split a message into chunks that fit Discord's character limit.

    Intelligently splits on:
    1. Code block boundaries (preserves ``` blocks intact)
    2. Paragraph boundaries (\n\n)
    3. Sentence boundaries (. ! ? followed by space or newline)
    4. Word boundaries (spaces)

    Args:
        text: Message text to split
        max_length: Maximum characters per chunk (default: 2000 for Discord)

    Returns:
        List of message chunks, each under max_length
    """
    if len(text) <= max_length:
        return [text]

    chunks = []

    # Check for code blocks and handle them specially
    import re
    parts = re.split(r'(```[\s\S]*?```)', text)

    current_chunk = ""

    for part in parts:
        is_code_block = part.startswith('```') and part.endswith('```')

        if len(current_chunk) + len(part) > max_length:
            # Save current chunk if not empty
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
                current_chunk = ""

            # Handle the part
            if is_code_block:
                # Code block too large - split while preserving markers
                lang_line = part.split('\n')[0]  # ```python or similar
                code_content = part[len(lang_line):-3]
                close_marker = '```'

                code_lines = code_content.split('\n')
                temp_code = lang_line + '\n'

                for line in code_lines:
                    if len(temp_code) + len(line) + len(close_marker) + 1 > max_length:
                        chunks.append(temp_code + close_marker)
                        temp_code = lang_line + '\n' + line + '\n'
                    else:
                        temp_code += line + '\n'

                if temp_code != lang_line + '\n':
                    chunks.append(temp_code + close_marker)
            else:
                # Non-code-block text - split intelligently
                chunks.extend(_split_text_intelligently(part, max_length))
        else:
            current_chunk += part

    # Add remaining chunk
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks if chunks else [text[:max_length]]


def _split_text_intelligently(text: str, max_length: int) -> list[str]:
    """
    Split plain text on natural boundaries.

    Tries in order:
    1. Paragraph boundaries (\n\n)
    2. Sentence boundaries (. ! ?)
    3. Word boundaries (spaces)
    4. Hard cut as fallback
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Try to split on paragraph boundary
        chunk = remaining[:max_length]
        split_pos = chunk.rfind('\n\n')

        # If no paragraph, try sentence boundary
        if split_pos == -1:
            for punct in ['. ', '! ', '? ', '.\n', '!\n', '?\n']:
                pos = chunk.rfind(punct)
                if pos > split_pos:
                    split_pos = pos + len(punct)

        # If no sentence, try word boundary
        if split_pos == -1:
            split_pos = chunk.rfind(' ')

        # Fallback: hard cut
        if split_pos == -1:
            split_pos = max_length

        chunks.append(remaining[:split_pos].strip())
        remaining = remaining[split_pos:].strip()

    return chunks


class DiscordClient(discord.Client):
    """
    Discord client with framework integration.

    Handles:
    - Discord gateway connection
    - Event routing
    - Message filtering
    - Integration with reactive engine
    """

    def __init__(
        self,
        config: "BotConfig",
        reactive_engine: "ReactiveEngine",
        agentic_engine: Optional["AgenticEngine"],
        message_memory: "MessageMemory",
        user_cache: "UserCache",
        conversation_logger: "ConversationLogger",
        memory_manager: "MemoryManager",
    ):
        """
        Initialize Discord client.

        Args:
            config: Bot configuration
            reactive_engine: Reactive engine for message handling
            agentic_engine: Agentic engine for autonomous behaviors
            message_memory: Message storage
            user_cache: User information cache
            conversation_logger: Conversation logger
            memory_manager: Memory manager for long-term memory
        """
        # Setup intents
        intents = discord.Intents.default()
        intents.message_content = True  # Required to read message text
        intents.reactions = True
        intents.guilds = True
        intents.members = True

        super().__init__(intents=intents)

        self.config = config
        self.reactive_engine = reactive_engine
        self.agentic_engine = agentic_engine
        self.message_memory = message_memory
        self.user_cache = user_cache
        self.conversation_logger = conversation_logger
        self.memory_manager = memory_manager

        # The event loop holds only weak refs to tasks - fire-and-forget
        # create_task calls can be garbage-collected mid-run
        self._background_tasks = set()

        # Slash commands (v0.9): /memory, DM-only, registered globally
        self.tree = discord.app_commands.CommandTree(self)

        logger.info(f"Discord client initialized for bot '{config.bot_id}'")

    async def setup_hook(self):
        """Register slash commands before the gateway connects (discord.py
        calls this once after login). /memory is DM-only by contexts."""
        memory_group = discord.app_commands.Group(
            name="memory",
            description="What this bot remembers about you",
            allowed_contexts=discord.app_commands.AppCommandContext(
                guild=False, dm_channel=True, private_channel=True),
        )

        @memory_group.command(name="show", description="See your profile, verbatim")
        async def memory_show(interaction: discord.Interaction):
            await self._run_memory_command(interaction, "show", "")

        @memory_group.command(name="remember", description="Teach the bot something, privately")
        @discord.app_commands.describe(text="What should it keep in mind?")
        async def memory_remember(interaction: discord.Interaction, text: str):
            await self._run_memory_command(interaction, "remember", text)

        @memory_group.command(name="forget", description="Correct or remove something it has on you")
        @discord.app_commands.describe(text="What should it forget or fix?")
        async def memory_forget(interaction: discord.Interaction, text: str):
            await self._run_memory_command(interaction, "forget", text)

        @memory_group.command(name="feedback", description="Tell it how its behavior landed")
        @discord.app_commands.describe(text="What worked, what didn't?")
        async def memory_feedback(interaction: discord.Interaction, text: str):
            await self._run_memory_command(interaction, "feedback", text)

        self.tree.add_command(memory_group)
        try:
            await self.tree.sync()
            logger.info("Slash commands synced (/memory, DM-only)")
        except Exception as e:
            logger.error(f"Slash command sync failed: {e}", exc_info=True)

    async def _run_memory_command(self, interaction: discord.Interaction,
                                  kind: str, text: str) -> None:
        """Ack within Discord's 3s window, then run the real pipeline; the
        bot's actual reply arrives as a normal DM message."""
        if interaction.channel is None:
            logger.warning("/memory command arrived with no channel context")
            try:
                await interaction.response.send_message(
                    "something's off with this channel - try DMing me a normal "
                    "message first, then run that again.", ephemeral=True)
            except discord.HTTPException:
                pass
            return
        try:
            await interaction.response.send_message(
                "let me think about that..." if kind != "show" else "pulling it up...",
                ephemeral=True)
        except discord.HTTPException as e:
            logger.error(f"/memory ack failed: {e}")
            return
        self._track_task(self.reactive_engine.handle_memory_command(
            interaction.channel, interaction.user, kind, text))

    def _track_task(self, coro) -> asyncio.Task:
        """create_task with a strong reference held until completion."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def on_ready(self):
        """Bot connected to Discord and ready"""
        logger.info(f"Bot connected: {self.user.name} (ID: {self.user.id})")
        logger.info(f"Logged into {len(self.guilds)} servers")

        # Log server details
        for guild in self.guilds:
            logger.info(f"  - {guild.name} (ID: {guild.id}, Members: {guild.member_count})")

        # Check for crash and insert lifecycle events
        from pathlib import Path
        from datetime import datetime

        flag_file = Path(f"persistence/{self.config.bot_id}_running.flag")

        if flag_file.exists():
            # Previous session crashed (flag wasn't removed)
            try:
                offline_time = datetime.fromtimestamp(flag_file.stat().st_mtime)
                logger.warning(f"Detected crash: Bot went offline at {offline_time}")

                # Insert crash/offline tag for each channel in each server
                for guild in self.guilds:
                    for channel in guild.text_channels:
                        try:
                            await self.message_memory.insert_system_message(
                                content="[YOU WENT OFFLINE - CRASH]",
                                channel_id=str(channel.id),
                                guild_id=str(guild.id),
                                timestamp=offline_time
                            )
                        except Exception as e:
                            logger.debug(f"Error inserting crash event to channel {channel.id}: {e}")
            except Exception as e:
                logger.error(f"Error processing crash detection: {e}", exc_info=True)

        # Insert online tag for each channel in each server. (Re-enabled: the
        # one-time 'startup hang' this was blamed for traced to the formerly
        # synchronous skills upload blocking the loop, plus a UNIQUE collision
        # on timestamp-only system message ids - both fixed.)
        online_time = datetime.utcnow()
        for guild in self.guilds:
            for channel in guild.text_channels:
                try:
                    await self.message_memory.insert_system_message(
                        content="[YOU CAME ONLINE]",
                        channel_id=str(channel.id),
                        guild_id=str(guild.id),
                        timestamp=online_time
                    )
                except Exception as e:
                    logger.debug(f"Error inserting online event to channel {channel.id}: {e}")

        # Channel-name cache sweep (v0.9): the dashboard reads names from
        # artifacts only, and the per-message upsert covers just channels with
        # traffic - seed every visible channel from the gateway cache instead.
        # Threads stay out (they resolve via the threads table).
        try:
            for guild in self.guilds:
                await self.message_memory.upsert_channel_name(
                    str(guild.id), guild.name, kind="server")
                for channel in [*guild.text_channels, *guild.voice_channels]:
                    await self.message_memory.upsert_channel_name(
                        str(channel.id), channel.name,
                        kind="channel", guild_id=str(guild.id))
        except Exception as e:
            logger.debug(f"Channel-name sweep failed: {e}")

        # Write running flag with current timestamp
        flag_file.parent.mkdir(parents=True, exist_ok=True)
        flag_file.write_text(str(online_time.timestamp()))
        logger.info(f"Running flag created: {flag_file}")

        # Set activity status
        activity = discord.Game(name=self.config.discord.status)
        await self.change_presence(activity=activity)

        # Give reactive engine access to Discord client for periodic checks
        self.reactive_engine.discord_client = self
        # DM prime-context line names the places this mind inhabits (v0.9)
        self.reactive_engine.list_servers = lambda: [g.name for g in self.guilds]

        # Initialize v0.5.0 managers (MCP, Skills)
        try:
            await self.reactive_engine.async_initialize()
            logger.info("v0.5.0 managers initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize v0.5.0 managers: {e}", exc_info=True)

        # Initialize Discord tools executor
        from tools.discord_tools import DiscordToolExecutor
        self.reactive_engine.discord_tool_executor = DiscordToolExecutor(
            message_memory=self.message_memory,
            user_cache=self.user_cache,
            vaults=self.reactive_engine.vaults,
            attachment_manager=self.reactive_engine.attachment_manager,
            conversation_state_manager=self.reactive_engine.conversation_state_manager,
            discord_client=self,
        )

        if self.reactive_engine.repository_manager:
            self.reactive_engine.repository_manager.guild_name_resolver = (
                lambda gid: self.get_guild(int(gid)).name
                if gid.isdigit() and self.get_guild(int(gid)) else None)

        # Watches inject relay notes into conversation states (v0.9); the
        # state manager lives on the reactive engine after async_initialize
        if self.agentic_engine:
            self.agentic_engine.conversation_state_manager = (
                self.reactive_engine.conversation_state_manager)

        # ask_prime executor (v0.9) - needs the connected client for guild
        # resolution; agentic engine carries the watch manager + send queue
        if self.agentic_engine and self.agentic_engine.watch_manager:
            from tools.ask_prime import AskPrimeExecutor
            self.reactive_engine.ask_prime_executor = AskPrimeExecutor(
                anthropic=self.reactive_engine.anthropic,
                model=self.config.api.model,
                vault_ids=self.config.vaults,
                watch_manager=self.agentic_engine.watch_manager,
                agentic_engine=self.agentic_engine,
                discord_client=self,
                message_memory=self.message_memory,
            )
            logger.info("ask_prime executor initialized")
        logger.info("Discord tools enabled")

        # Start periodic conversation scanning
        self.reactive_engine.start_periodic_check()

        # Start agentic loop if enabled (engine keeps the handle so its
        # shutdown() can actually cancel the loop)
        if self.agentic_engine:
            self.agentic_engine._task = self._track_task(self.agentic_engine.agentic_loop())
            logger.info("Agentic loop started")

        # Backfill historical messages if enabled (always in background -
        # blocking the gateway on startup is never right)
        if self.config.discord.backfill_enabled:
            self._track_task(self.backfill_message_history(
                days_back=self.config.discord.backfill_days,
                incremental=True,  # boot resumes where the last session stopped
            ))

            # Initialize memory structure with skeleton files (v0.5.0)
            try:
                await self.memory_manager.initialize_memory_structure(
                    message_memory=self.message_memory,
                    user_cache=self.user_cache,
                    discord_guilds=self.guilds
                )
            except Exception as e:
                logger.error(f"Failed to initialize memory structure: {e}", exc_info=True)

            # Start daily re-backfill task to catch edited messages
            self._track_task(self._daily_reindex_task())
            logger.info("Daily re-backfill task started (will run at 3 AM UTC)")

        logger.info("Bot is ready!")

    async def on_message(self, message: discord.Message):
        """
        New message received.

        Filters and routes messages to reactive engine.

        Args:
            message: Discord message object
        """
        # Only process messages from configured servers
        if message.guild:
            guild_id = str(message.guild.id)
            if self.config.discord.servers:
                if guild_id not in self.config.discord.servers:
                    logger.debug(
                        f"Ignoring message from unconfigured server: {message.guild.name}"
                    )
                    return

        # Check for timezone command (before storing message)
        if not message.author.bot:
            timezone_result = await self._handle_timezone_command(message)
            if timezone_result:
                return  # Command handled, don't process further

        # Store ALL messages in memory (including bot's own for context)
        try:
            await self.message_memory.add_message(message)
        except Exception as e:
            logger.error(f"Error storing message: {e}")

        # Thread registry (v0.8.0): keep thread -> parent mapping fresh
        if isinstance(message.channel, discord.Thread):
            try:
                await self.message_memory.upsert_thread(
                    str(message.channel.id),
                    parent_id=str(message.channel.parent_id),
                    name=message.channel.name,
                    archived=bool(message.channel.archived),
                )
            except Exception as e:
                logger.debug(f"Thread registry update failed: {e}")

        # Channel-name cache (v0.9): the supervisor labels channels offline.
        # No-op guarded, so steady state costs a dict lookup.
        try:
            if message.guild is not None:
                if not isinstance(message.channel, discord.Thread):
                    await self.message_memory.upsert_channel_name(
                        str(message.channel.id), message.channel.name,
                        kind="channel", guild_id=str(message.guild.id))
                await self.message_memory.upsert_channel_name(
                    str(message.guild.id), message.guild.name, kind="server")
            elif message.author.id != self.user.id:
                await self.message_memory.upsert_channel_name(
                    str(message.channel.id),
                    f"DM · {message.author.display_name}", kind="dm")
        except Exception as e:
            logger.debug(f"Channel-name cache update failed: {e}")

        # Process attachments if enabled (v0.5.0)
        processed_attachments = []
        if self.reactive_engine.attachment_manager and message.attachments:
            try:
                for attachment in message.attachments:
                    result = await self.reactive_engine.attachment_manager.process_attachment(
                        attachment=attachment,
                        message=message,
                        is_realtime=True
                    )
                    if result:
                        processed_attachments.append(result)
                logger.info(f"Processed {len(message.attachments)} attachments from message {message.id}")
            except Exception as e:
                logger.error(f"Error processing attachments: {e}", exc_info=True)

        # Track THIS bot's attachments in conversation state (v0.5.0)
        # Bug #4 fix: Only track THIS bot's attachments, not other bots (treat them as users)
        if message.author.id == self.user.id and processed_attachments and self.reactive_engine.conversation_state_manager:
            try:
                channel_id = str(message.channel.id)
                conversation_state = await self.reactive_engine.conversation_state_manager.get_or_create(channel_id)

                # Extract attachment IDs
                attachment_ids = [att["attachment_id"] for att in processed_attachments]

                # Build content blocks; role='assistant' yields text
                # placeholders only - image/document/container_upload blocks
                # in assistant turns 400 every subsequent API call
                content_blocks = []

                # Add text content first if exists
                if message.content:
                    content_blocks.append({"type": "text", "text": message.content})

                for att in processed_attachments:
                    block = self.reactive_engine.attachment_manager.build_content_block(
                        att.get("for_api"), att["filename"], role="assistant"
                    )
                    if block:
                        content_blocks.append(block)

                # Use content blocks if we have attachments, otherwise just text
                content = content_blocks if content_blocks else (message.content or "[File uploaded]")

                # Add bot message with attachments to conversation state
                conversation_state.add_message(
                    role="assistant",  # Bot messages are assistant role
                    content=content,
                    attachment_ids=attachment_ids
                )

                # Save updated state
                await self.reactive_engine.conversation_state_manager.save(conversation_state)
                logger.info(f"Tracked {len(attachment_ids)} bot attachments in conversation state for testing")

            except Exception as e:
                logger.error(f"Failed to track bot attachments in conversation state: {e}", exc_info=True)

        # Update user cache
        try:
            await self.user_cache.update_user(message.author, increment_messages=True)
        except Exception as e:
            logger.error(f"Error updating user cache: {e}")

        # Don't process bot's own messages
        if message.author == self.user:
            return

        # Filter other bots based on config
        if message.author.bot:
            if not self.config.discord.allow_bot_interactions:
                logger.debug(f"Ignoring message from bot: {message.author.name}")
                return
            else:
                logger.debug(f"Processing message from bot: {message.author.name} (allow_bot_interactions=True)")

        # Register DM channel so the bot remembers this surface across restarts (v0.9)
        if message.guild is None and message.author.id != self.user.id:
            await self.user_cache.set_dm_channel(
                str(message.author.id), str(message.channel.id)
            )

        # Urgent = @mention, or any DM (a DM is inherently addressed to the bot)
        is_urgent = self.user in message.mentions or message.guild is None

        if is_urgent:
            logger.info(
                f"@mention from {message.author.name} in "
                f"#{getattr(message.channel, 'name', 'DM')}: {message.content[:50]}..."
            )

            # Check for reindex command trigger (manual backfill)
            if "reindex" in message.content.lower():
                logger.info(f"Manual reindex triggered by {message.author.name}")

                start_msg = "Starting reindex... This will take ~10-15 seconds."
                await message.channel.send(start_msg)
                logger.info(f"Sent Discord message: {start_msg}")

                try:
                    total = await self.backfill_message_history(
                        days_back=self.config.discord.backfill_days,
                        )
                    complete_msg = f"Reindex complete! Updated {total} messages.\n*Note: If you edited messages during reindex, run again to catch them.*"
                    await message.channel.send(complete_msg)
                    logger.info(f"Sent Discord message: {complete_msg[:80]}...")
                except Exception as e:
                    logger.error(f"Manual reindex error: {e}", exc_info=True)
                    error_msg = f"Reindex failed: {e}"
                    await message.channel.send(error_msg)
                    logger.error(f"Sent Discord error message: {error_msg}")
                return

            # Handle immediately
            try:
                await self.reactive_engine.handle_urgent(message)
            except Exception as e:
                logger.error(f"Error handling urgent message: {e}", exc_info=True)
                # Send error message to user
                try:
                    await message.channel.send(
                        f"Sorry {message.author.mention}, I encountered an error processing your message."
                    )
                except Exception:
                    pass

        else:
            # Non-urgent message - add to pending for periodic check
            # Bug #7 fix: Track individual message IDs, not just channel IDs
            channel_id = str(message.channel.id)
            message_id = message.id
            self.reactive_engine.add_pending_message(channel_id, message_id)
            logger.debug(
                f"Message {message_id} from {message.author.name} in "
                f"#{getattr(message.channel, 'name', 'DM')} (stored, added to pending)"
            )

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """
        Message edited.

        Update stored message with new content.

        Args:
            before: Message before edit
            after: Message after edit
        """
        logger.info(f"[EDIT EVENT] Message {after.id} edited by {after.author.name} in #{getattr(after.channel, 'name', 'DM')}")
        logger.info(f"[EDIT EVENT] Before: {before.content[:100] if before.content else '(no content)'}")
        logger.info(f"[EDIT EVENT] After: {after.content[:100] if after.content else '(no content)'}")

        # Ignore bot's own edits
        if after.author == self.user:
            logger.info(f"[EDIT EVENT] Ignoring bot's own edit")
            return

        # Ignore edits from other bots
        if after.author.bot:
            logger.info(f"[EDIT EVENT] Ignoring edit from other bot")
            return

        # Update message in storage
        try:
            await self.message_memory.update_message(after)
            logger.info(f"[EDIT EVENT] Successfully processed edit from {after.author.name}")
        except Exception as e:
            logger.error(f"[EDIT EVENT] Error updating edited message: {e}", exc_info=True)

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        """
        Message deleted - remove from storage to maintain accuracy.

        Raw event: fires for ALL deletions, unlike on_message_delete which
        only covers messages still in discord.py's in-process cache (anything
        older than the current process would silently stay re-surfaceable).
        """
        await self._purge_deleted_message(payload.message_id)

    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        """Bulk deletions (mod sweeps) get the same purge per message."""
        for message_id in payload.message_ids:
            await self._purge_deleted_message(message_id)

    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        """Track archival state + renames (archived threads auto-unarchive on send)."""
        await self.message_memory.upsert_thread(
            str(after.id), parent_id=str(after.parent_id),
            name=after.name, archived=bool(after.archived),
        )

    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent):
        """Deleting a thread purges its stored messages + attachments, mirroring
        the per-message raw-deletion path (which never fires for container
        deletion)."""
        thread_id = str(payload.thread_id)
        try:
            message_ids = await self.message_memory.get_message_ids_in_channel(thread_id)
            for mid in message_ids:
                if mid.isdigit():
                    await self._purge_deleted_message(int(mid))
        except Exception as e:
            logger.warning(f"Thread {thread_id} purge incomplete: {e}")
        try:
            await self.message_memory.remove_thread(thread_id)
        except Exception as e:
            logger.warning(f"Thread {thread_id} registry removal failed: {e}")

    async def _purge_deleted_message(self, message_id: int):
        """Remove a deleted message from storage and the attachment pipeline."""
        try:
            await self.message_memory.delete_message(message_id)
            logger.info(f"Deleted message {message_id} from storage")
        except Exception as e:
            logger.error(f"Error deleting message from storage: {e}")

        # Deleted content must not stay re-surfaceable through the
        # attachment pipeline (local copy, Files API upload, index rows)
        if self.reactive_engine and self.reactive_engine.attachment_manager:
            try:
                await self.reactive_engine.attachment_manager.delete_attachments_for_message(message_id)
            except Exception as e:
                logger.error(f"Error purging attachments for deleted message: {e}")

    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        """
        Reaction added to message - track engagement.

        Args:
            reaction: Reaction object
            user: User who added reaction
        """
        # Ignore bot's own reactions
        if user == self.user:
            return

        # Check if reaction is to bot's message
        if reaction.message.author == self.user:
            # Record engagement
            channel_id = str(reaction.message.channel.id)
            self.reactive_engine.rate_limiter.record_engagement(channel_id)
            logger.debug(
                f"Engagement: {user.name} reacted {reaction.emoji} to bot message"
            )

    async def on_error(self, event: str, *args, **kwargs):
        """
        Global error handler - prevents bot from crashing on unhandled exceptions.

        Args:
            event: Event name that caused error
        """
        logger.error(f"Error in event {event}", exc_info=True)

    async def on_guild_join(self, guild: discord.Guild):
        """Bot joined a new server"""
        logger.info(f"Joined new server: {guild.name} (ID: {guild.id})")

    async def on_guild_remove(self, guild: discord.Guild):
        """Bot removed from server"""
        logger.info(f"Removed from server: {guild.name} (ID: {guild.id})")

    async def backfill_message_history(self, days_back: int = 30,
                                       incremental: bool = False):
        """
        Fetch and index historical messages from accessible channels.

        Populates message database with historical messages to enable
        powerful search capabilities beyond current session.

        Args:
            days_back: Number of days of history to fetch; 0 = unlimited
                       (all accessible history)
            incremental: resume each channel from its newest stored message
                         instead of re-fetching the whole window. Boot uses
                         this; the daily re-backfill stays full because its
                         job is catching edits to already-stored messages.
        """
        from datetime import datetime, timedelta, timezone

        if days_back <= 0:
            logger.info("Starting UNLIMITED message history backfill (all accessible history)...")
            cutoff = None
        else:
            logger.info(f"Starting message history backfill ({days_back} days)...")
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

        resume = await self.message_memory.newest_message_times() if incremental else {}

        total_messages = 0
        total_channels = 0
        failed_channels = 0

        for guild in self.guilds:
            # Only backfill configured servers
            if self.config.discord.servers:
                if str(guild.id) not in self.config.discord.servers:
                    continue

            logger.info(f"Backfilling server: {guild.name}")

            async for channel in iter_backfill_channels(guild):
                try:
                    channel_messages = 0
                    logger.debug(f"  Backfilling #{channel.name}...")

                    # Threads met during backfill register their parent mapping
                    if isinstance(channel, discord.Thread):
                        await self.message_memory.upsert_thread(
                            str(channel.id), parent_id=str(channel.parent_id),
                            name=channel.name, archived=bool(channel.archived))

                    # Resume point: newest stored message wins over the
                    # window cutoff when it's more recent
                    after = resume.get(str(channel.id)) or cutoff
                    if after and cutoff and cutoff > after:
                        after = cutoff

                    async for message in channel.history(limit=None, after=after):
                        try:
                            await self.message_memory.add_message(message)
                            channel_messages += 1
                            total_messages += 1

                            # Log progress every 100 messages
                            if total_messages % 100 == 0:
                                logger.info(f"  Progress: {total_messages} messages indexed...")

                        except Exception as e:
                            logger.debug(f"  Skipped message {message.id}: {e}")

                    if channel_messages > 0:
                        logger.debug(f"  #{channel.name}: {channel_messages} messages")
                        total_channels += 1

                except discord.Forbidden:
                    logger.debug(f"  #{channel.name}: No read permission")
                    failed_channels += 1
                except Exception as e:
                    logger.warning(f"  #{channel.name}: {e}")
                    failed_channels += 1

        logger.info(f"Backfill complete: {total_messages} messages from {total_channels} channels")
        if failed_channels > 0:
            logger.info(f"  ({failed_channels} channels skipped due to permissions/errors)")

        # Episodize the open span now that the message store is current (v0.6.0)
        if self.reactive_engine and getattr(self.reactive_engine, "episode_manager", None):
            self._track_task(self.reactive_engine.episode_manager.catch_up_all_channels())

        return total_messages

    async def _handle_timezone_command(self, message: discord.Message) -> bool:
        """
        Handle timezone setting command (!timezone or !tz).

        Returns True if command was handled, False otherwise.
        """
        content_lower = message.content.lower().strip()

        # Check for timezone command prefix
        if not (content_lower.startswith("!timezone") or content_lower.startswith("!tz")):
            return False

        # Extract timezone argument
        parts = message.content.split(maxsplit=1)
        if len(parts) < 2:
            await message.channel.send(
                f"{message.author.mention} Usage: `!timezone <timezone>` or `!tz <timezone>`\n"
                f"Example: `!timezone America/New_York` or `!tz EST`",
                delete_after=15
            )
            return True

        tz_input = parts[1].strip()

        # Validate and normalize timezone
        import pytz
        normalized_tz = None

        # Try as IANA timezone name first
        try:
            pytz.timezone(tz_input)
            normalized_tz = tz_input
        except pytz.exceptions.UnknownTimeZoneError:
            # Try common abbreviations
            abbreviation_map = {
                'est': 'America/New_York',
                'edt': 'America/New_York',
                'cst': 'America/Chicago',
                'cdt': 'America/Chicago',
                'mst': 'America/Denver',
                'mdt': 'America/Denver',
                'pst': 'America/Los_Angeles',
                'pdt': 'America/Los_Angeles',
                'utc': 'UTC',
                'gmt': 'GMT',
            }

            tz_lower = tz_input.lower()
            if tz_lower in abbreviation_map:
                normalized_tz = abbreviation_map[tz_lower]
            else:
                await message.channel.send(
                    f"{message.author.mention} Invalid timezone: `{tz_input}`\n"
                    f"Use IANA format (e.g., `America/New_York`) or common abbreviations (e.g., `EST`, `PST`).",
                    delete_after=15
                )
                return True

        # Write to user's memory profile (global path — works in DMs and guilds)
        user_id = str(message.author.id)
        memory_path = self.reactive_engine.memory_manager.get_global_user_profile_path(user_id)

        file_path = self.reactive_engine.memory_manager.resolve_path(memory_path)

        # Ensure directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Read existing profile or create new
        try:
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    profile_content = f.read()
            else:
                profile_content = ""

            # Update or add timezone
            import re
            if "**Timezone:**" in profile_content:
                # Replace existing timezone
                profile_content = re.sub(
                    r'\*\*Timezone:\*\* .*\n',
                    f'**Timezone:** {normalized_tz}\n',
                    profile_content
                )
            else:
                # Add timezone at top
                profile_content = f"**Timezone:** {normalized_tz}\n\n" + profile_content

            # Write back
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(profile_content)

            logger.info(f"Set timezone for user {message.author.name} to {normalized_tz}")

        except Exception as e:
            logger.error(f"Error writing timezone to user profile: {e}", exc_info=True)
            await message.channel.send(
                f"{message.author.mention} Error saving timezone. Please try again.",
                delete_after=10
            )
            return True

        # Send confirmation
        await message.channel.send(
            f"✓ Timezone set to **{normalized_tz}** for {message.author.mention}",
            delete_after=10
        )

        return True

    async def _daily_reindex_task(self):
        """
        Background task that runs daily re-backfill to catch edited messages.

        Runs at 3 AM UTC each day to update the message database with any
        edits that occurred during the previous day.
        """
        from datetime import datetime, timedelta

        logger.info("Daily reindex task initialized")

        while True:
            try:
                # Calculate time until next 3 AM UTC
                now = datetime.utcnow()
                target_hour = 3  # 3 AM UTC

                # Calculate next 3 AM
                next_run = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
                if now.hour >= target_hour:
                    # Already past 3 AM today, schedule for tomorrow
                    next_run += timedelta(days=1)

                wait_seconds = (next_run - now).total_seconds()
                logger.info(f"Next daily reindex scheduled for {next_run} UTC (in {wait_seconds/3600:.1f} hours)")

                # Wait until 3 AM
                await asyncio.sleep(wait_seconds)

                # Run backfill
                logger.info("Starting scheduled daily re-backfill...")
                total = await self.backfill_message_history(
                    days_back=self.config.discord.backfill_days,
                )
                logger.info(f"Daily re-backfill complete: {total} messages indexed")

                # Garbage-collect conversation states for long-dead channels
                if self.reactive_engine.conversation_state_manager:
                    try:
                        await self.reactive_engine.conversation_state_manager.cleanup_old_states()
                    except Exception as e:
                        logger.error(f"Conversation-state cleanup failed: {e}")

            except asyncio.CancelledError:
                logger.info("Daily reindex task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in daily reindex task: {e}", exc_info=True)
                # Wait 1 hour before retrying on error
                await asyncio.sleep(3600)
