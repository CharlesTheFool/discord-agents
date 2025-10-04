#!/usr/bin/env python3
"""
Discord-Claude Bot Framework - Bot Manager CLI

Entry point for spawning and managing bots.

Usage:
    python bot_manager.py spawn <bot_id>
    python bot_manager.py stop
    python bot_manager.py status
"""

import asyncio
import sys
import logging
import signal
from pathlib import Path
from dotenv import load_dotenv
import os

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core.config import BotConfig
from core.rate_limiter import RateLimiter
from core.message_memory import MessageMemory
from core.memory_manager import MemoryManager
from core.reactive_engine import ReactiveEngine
from core.discord_client import DiscordClient


class BotManager:
    """
    Manages bot lifecycle.

    Handles:
    - Bot initialization
    - Component setup
    - Graceful shutdown
    """

    def __init__(self, bot_id: str):
        """
        Initialize bot manager.

        Args:
            bot_id: Bot identifier (e.g., "alpha")
        """
        self.bot_id = bot_id
        self.config: BotConfig = None
        self.client: DiscordClient = None
        self.message_memory: MessageMemory = None

        # Setup logging
        self._setup_logging()

    def _setup_logging(self):
        """Configure logging"""
        # Create logs directory
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)

        # Configure root logger
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler(log_dir / f"{self.bot_id}.log"),
            ],
        )

        # Set discord.py logging to WARNING to reduce noise
        logging.getLogger("discord").setLevel(logging.WARNING)
        logging.getLogger("discord.http").setLevel(logging.WARNING)

    async def initialize(self):
        """
        Initialize all bot components.

        Loads config, sets up database, initializes engines.
        """
        logger = logging.getLogger(__name__)
        logger.info(f"Initializing bot '{self.bot_id}'...")

        # Load environment variables
        load_dotenv()

        # Load bot configuration first (to get token_env_var)
        config_path = Path(f"bots/{self.bot_id}.yaml")
        if not config_path.exists():
            raise FileNotFoundError(f"Bot config not found: {config_path}")

        self.config = BotConfig.load(config_path)
        self.config.validate()
        logger.info(f"Loaded config for bot '{self.config.name}'")

        # Check required environment variables
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        if not anthropic_key:
            raise ValueError("ANTHROPIC_API_KEY not set in .env file")

        # Get Discord token from environment variable specified in config
        token_env_var = self.config.discord.token_env_var
        discord_token = os.getenv(token_env_var)
        if not discord_token:
            raise ValueError(
                f"{token_env_var} not set in .env file. "
                f"This bot's config requires a Discord token in the {token_env_var} environment variable."
            )

        # Update log level from config
        log_level = getattr(logging, self.config.logging.level)
        logging.getLogger().setLevel(log_level)

        # Initialize message memory (SQLite)
        db_path = Path("persistence") / f"{self.bot_id}_messages.db"
        db_path.parent.mkdir(exist_ok=True)

        self.message_memory = MessageMemory(db_path)
        await self.message_memory.initialize()
        logger.info("Message memory initialized")

        # Initialize memory manager
        memory_base_path = Path("memories")
        memory_manager = MemoryManager(self.bot_id, memory_base_path)
        logger.info("Memory manager initialized")

        # Initialize conversation logger
        from core.conversation_logger import ConversationLogger
        log_dir = Path("logs")
        conversation_logger = ConversationLogger(self.bot_id, log_dir)
        logger.info("Conversation logger initialized")

        # Initialize rate limiter
        rate_limiter_config = {
            "short_window_minutes": self.config.rate_limiting.short.duration_minutes,
            "short_window_max": self.config.rate_limiting.short.max_responses,
            "long_window_minutes": self.config.rate_limiting.long.duration_minutes,
            "long_window_max": self.config.rate_limiting.long.max_responses,
            "ignore_threshold": self.config.rate_limiting.ignore_threshold,
        }
        rate_limiter = RateLimiter(rate_limiter_config)
        logger.info("Rate limiter initialized")

        # Initialize reactive engine
        reactive_engine = ReactiveEngine(
            config=self.config,
            rate_limiter=rate_limiter,
            message_memory=self.message_memory,
            memory_manager=memory_manager,
            anthropic_api_key=anthropic_key,
            conversation_logger=conversation_logger,
        )
        logger.info("Reactive engine initialized")

        # Initialize Discord client
        self.client = DiscordClient(
            config=self.config,
            reactive_engine=reactive_engine,
            message_memory=self.message_memory,
            conversation_logger=conversation_logger,
        )
        logger.info("Discord client initialized")

        logger.info("Bot initialization complete!")

        return discord_token

    async def run(self):
        """
        Run the bot.

        Connects to Discord and runs until interrupted.
        """
        logger = logging.getLogger(__name__)

        try:
            # Initialize components
            discord_token = await self.initialize()

            # Start bot
            logger.info("Connecting to Discord...")
            await self.client.start(discord_token)

        except KeyboardInterrupt:
            logger.info("Shutdown requested by user (Ctrl+C)")
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Graceful shutdown"""
        logger = logging.getLogger(__name__)
        logger.info("Shutting down bot...")

        try:
            # Shutdown reactive engine (cancel background tasks)
            if hasattr(self, 'client') and self.client and hasattr(self.client, 'reactive_engine'):
                await self.client.reactive_engine.shutdown()

            # Close Discord connection
            if self.client:
                await self.client.close()

            # Close database
            if self.message_memory:
                await self.message_memory.close()

            logger.info("Shutdown complete")

        except Exception as e:
            logger.error(f"Error during shutdown: {e}")


def print_usage():
    """Print CLI usage"""
    print("Discord-Claude Bot Framework - Bot Manager")
    print()
    print("Usage:")
    print("  python bot_manager.py spawn <bot_id>   - Start a bot")
    print("  python bot_manager.py --help           - Show this help")
    print()
    print("Examples:")
    print("  python bot_manager.py spawn alpha      - Start the alpha bot")
    print()
    print("Configuration:")
    print("  1. Copy .env.example to .env")
    print("  2. Fill in DISCORD_BOT_TOKEN and ANTHROPIC_API_KEY")
    print("  3. Create or edit bot config in bots/<bot_id>.yaml")
    print("  4. Run: python bot_manager.py spawn <bot_id>")


def main():
    """Main CLI entry point"""
    if len(sys.argv) < 2 or sys.argv[1] in ["--help", "-h", "help"]:
        print_usage()
        sys.exit(0)

    command = sys.argv[1]

    if command == "spawn":
        if len(sys.argv) < 3:
            print("Error: Missing bot_id")
            print("Usage: python bot_manager.py spawn <bot_id>")
            sys.exit(1)

        bot_id = sys.argv[2]

        # Create and run bot
        manager = BotManager(bot_id)
        asyncio.run(manager.run())

    else:
        print(f"Error: Unknown command '{command}'")
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
