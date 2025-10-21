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

logger = logging.getLogger(__name__)


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

        logger.info(f"Discord client initialized for bot '{config.bot_id}'")

    async def on_ready(self):
        """Bot connected to Discord and ready"""
        logger.info(f"Bot connected: {self.user.name} (ID: {self.user.id})")
        logger.info(f"Logged into {len(self.guilds)} servers")

        # Log server details
        for guild in self.guilds:
            logger.info(f"  - {guild.name} (ID: {guild.id}, Members: {guild.member_count})")

        # Set activity status
        activity = discord.Game(name="Powered by Claude Sonnet 4.5")
        await self.change_presence(activity=activity)

        # Give reactive engine access to Discord client for periodic checks
        self.reactive_engine.discord_client = self

        # Initialize Discord tools executor
        from tools.discord_tools import DiscordToolExecutor
        self.reactive_engine.discord_tool_executor = DiscordToolExecutor(
            message_memory=self.message_memory,
            user_cache=self.user_cache
        )
        logger.info("Discord tools enabled")

        # Start periodic conversation scanning
        self.reactive_engine.start_periodic_check()

        # Start agentic loop if enabled
        if self.agentic_engine:
            asyncio.create_task(self.agentic_engine.agentic_loop())
            logger.info("Agentic loop started")

        # Backfill historical messages if enabled
        if self.config.discord.backfill_enabled:
            if self.config.discord.backfill_in_background:
                # Run in background - don't block bot startup
                asyncio.create_task(self.backfill_message_history(
                    days_back=self.config.discord.backfill_days,
                    unlimited=self.config.discord.backfill_unlimited
                ))
            else:
                # Block until backfill completes
                await self.backfill_message_history(
                    days_back=self.config.discord.backfill_days,
                    unlimited=self.config.discord.backfill_unlimited
                )

            # Start daily re-backfill task to catch edited messages
            asyncio.create_task(self._daily_reindex_task())
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

        # Store ALL messages in memory (including bot's own for context)
        try:
            await self.message_memory.add_message(message)
        except Exception as e:
            logger.error(f"Error storing message: {e}")

        # Update user cache
        try:
            await self.user_cache.update_user(message.author, increment_messages=True)
        except Exception as e:
            logger.error(f"Error updating user cache: {e}")

        # Don't process bot's own messages or other bots' messages
        if message.author == self.user:
            return

        if message.author.bot:
            logger.debug(f"Ignoring message from bot: {message.author.name}")
            return

        # Check if this is an urgent message (@mention)
        is_mention = self.user in message.mentions

        if is_mention:
            logger.info(
                f"@mention from {message.author.name} in "
                f"#{message.channel.name}: {message.content[:50]}..."
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
                        unlimited=self.config.discord.backfill_unlimited
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
            channel_id = str(message.channel.id)
            self.reactive_engine.add_pending_channel(channel_id)
            logger.debug(
                f"Message from {message.author.name} in #{message.channel.name} (stored, added to pending)"
            )

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """
        Message edited.

        Update stored message with new content.

        Args:
            before: Message before edit
            after: Message after edit
        """
        logger.info(f"[EDIT EVENT] Message {after.id} edited by {after.author.name} in #{after.channel.name}")
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

    async def on_message_delete(self, message: discord.Message):
        """
        Message deleted - remove from storage to maintain accuracy.

        Args:
            message: Deleted message
        """
        try:
            await self.message_memory.delete_message(message.id)
            logger.debug(f"Deleted message {message.id} from storage")
        except Exception as e:
            logger.error(f"Error deleting message from storage: {e}")

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

    async def backfill_message_history(self, days_back: int = 30, unlimited: bool = False):
        """
        Fetch and index historical messages from accessible channels.

        Populates message database with historical messages to enable
        powerful search capabilities beyond current session.

        Args:
            days_back: Number of days of history to fetch (default: 30, ignored if unlimited=True)
            unlimited: If True, fetch ALL message history (ignores days_back)
        """
        from datetime import datetime, timedelta

        if unlimited:
            logger.info(f"Starting UNLIMITED message history backfill (all accessible history)...")
            cutoff = None
        else:
            logger.info(f"Starting message history backfill ({days_back} days)...")
            cutoff = datetime.utcnow() - timedelta(days=days_back)

        total_messages = 0
        total_channels = 0
        failed_channels = 0

        for guild in self.guilds:
            # Only backfill configured servers
            if self.config.discord.servers:
                if str(guild.id) not in self.config.discord.servers:
                    continue

            logger.info(f"Backfilling server: {guild.name}")

            for channel in guild.text_channels:
                try:
                    channel_messages = 0
                    logger.debug(f"  Backfilling #{channel.name}...")

                    # Use cutoff if provided, otherwise fetch all
                    async for message in channel.history(limit=None, after=cutoff):
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

        return total_messages

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
                    unlimited=self.config.discord.backfill_unlimited
                )
                logger.info(f"Daily re-backfill complete: {total} messages indexed")

            except asyncio.CancelledError:
                logger.info("Daily reindex task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in daily reindex task: {e}", exc_info=True)
                # Wait 1 hour before retrying on error
                await asyncio.sleep(3600)
