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
    from .conversation_logger import ConversationLogger

logger = logging.getLogger(__name__)


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
        conversation_logger: "ConversationLogger",
    ):
        """
        Initialize Discord client.

        Args:
            config: Bot configuration
            reactive_engine: Reactive engine for message handling
            agentic_engine: Agentic engine for autonomous behaviors (Phase 3)
            message_memory: Message storage
            conversation_logger: Conversation logger
        """
        # Setup intents
        intents = discord.Intents.default()
        intents.message_content = True  # Required to read message text
        intents.reactions = True  # Required to see reactions
        intents.guilds = True  # Required for server info
        intents.members = True  # Required for member list

        super().__init__(intents=intents)

        self.config = config
        self.reactive_engine = reactive_engine
        self.agentic_engine = agentic_engine
        self.message_memory = message_memory
        self.conversation_logger = conversation_logger

        logger.info(f"Discord client initialized for bot '{config.bot_id}'")

    async def on_ready(self):
        """
        Bot connected to Discord and ready.

        This event fires once on startup.
        """
        logger.info(f"Bot connected: {self.user.name} (ID: {self.user.id})")
        logger.info(f"Logged into {len(self.guilds)} servers")

        # Log server details
        for guild in self.guilds:
            logger.info(f"  - {guild.name} (ID: {guild.id}, Members: {guild.member_count})")

        # Set activity status
        activity = discord.Game(name="Powered by Claude Sonnet 4.5")
        await self.change_presence(activity=activity)

        # Give reactive engine access to Discord client for periodic checks (Phase 3)
        self.reactive_engine.discord_client = self

        # Start periodic conversation scanning (Phase 3)
        self.reactive_engine.start_periodic_check()

        # Start agentic loop (Phase 3)
        if self.agentic_engine:
            asyncio.create_task(self.agentic_engine.agentic_loop())
            logger.info("Agentic loop started")

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
            # Handle immediately (Phase 1: only @mentions)
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
            # Non-urgent message - add to pending for periodic check (Phase 3)
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
        # Ignore bot's own edits
        if after.author == self.user:
            return

        # Ignore edits from other bots
        if after.author.bot:
            return

        # Update message in storage
        try:
            await self.message_memory.update_message(after)
            logger.debug(f"Updated edited message from {after.author.name}")
        except Exception as e:
            logger.error(f"Error updating edited message: {e}")

    async def on_message_delete(self, message: discord.Message):
        """
        Message deleted.

        Remove from storage to maintain accuracy.

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
        Reaction added to message.

        Used for engagement tracking.

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
        Global error handler for events.

        Prevents bot from crashing on unhandled exceptions.

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
