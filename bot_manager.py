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
import threading

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core.config import BotConfig
from core.rate_limiter import RateLimiter
from core.message_memory import MessageMemory
from core.memory_manager import MemoryManager
from core.reactive_engine import ReactiveEngine
from core.discord_client import DiscordClient
from core.user_cache import UserCache


# Helper functions for deployment submodule support
def resolve_config_path(bot_id: str) -> Path:
    """
    Resolve bot config path. Checks (in order):
    1. deployment/bots/{bot_id}.yaml (private submodule)
    2. bots/{bot_id}.yaml (local override)
    3. bots/{bot_id}.yaml.example (template fallback)

    Args:
        bot_id: Bot identifier

    Returns:
        Path to config file

    Raises:
        FileNotFoundError: If no config found
    """
    logger = logging.getLogger(__name__)

    deployment_path = Path("deployment/bots") / f"{bot_id}.yaml"
    local_path = Path("bots") / f"{bot_id}.yaml"
    template_path = Path("bots") / f"{bot_id}.yaml.example"

    if deployment_path.exists():
        logger.info(f"Using deployment config: {deployment_path}")
        return deployment_path
    elif local_path.exists():
        logger.info(f"Using local config: {local_path}")
        return local_path
    elif template_path.exists():
        logger.warning(
            f"Using template config for {bot_id}. "
            f"Copy to deployment/bots/{bot_id}.yaml or bots/{bot_id}.yaml and customize."
        )
        return template_path
    else:
        raise FileNotFoundError(
            f"No config found for {bot_id}. Tried:\n"
            f"  - {deployment_path}\n"
            f"  - {local_path}\n"
            f"  - {template_path}"
        )


def load_environment():
    """
    Load .env file. Checks (in order):
    1. deployment/.env (private submodule)
    2. .env (root)

    Logs which file was loaded.
    """
    logger = logging.getLogger(__name__)

    deployment_env = Path("deployment/.env")
    root_env = Path(".env")

    if deployment_env.exists():
        load_dotenv(deployment_env)
        logger.info("Loaded environment from deployment/.env")
    elif root_env.exists():
        load_dotenv(root_env)
        logger.info("Loaded environment from .env")
    else:
        logger.error(
            "No .env file found! Copy .env.example to .env or deployment/.env and configure."
        )
        sys.exit(1)


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

        # Load environment variables (deployment/.env or .env)
        load_environment()

        # Resolve config path (deployment/bots/, bots/, or template)
        config_path = resolve_config_path(self.bot_id)

        # Load bot configuration
        self.config = BotConfig.load(config_path)
        logger.info(f"Loaded config for bot '{self.config.name}'")

        # Validate configuration
        validation_errors = self.config.validate()
        if validation_errors:
            logger.error(f"[{self.bot_id}] Configuration validation failed:")
            for error in validation_errors:
                logger.error(f"  - {error}")
            sys.exit(1)

        logger.info(f"[{self.bot_id}] Configuration validated successfully")

        # Get environment variables (already validated by config.validate())
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        discord_token = os.getenv(self.config.discord.token_env_var)

        # Update log level from config
        log_level = getattr(logging, self.config.logging.level)
        logging.getLogger().setLevel(log_level)

        # Initialize message memory (SQLite)
        db_path = Path("persistence") / f"{self.bot_id}_messages.db"
        db_path.parent.mkdir(exist_ok=True)

        self.message_memory = MessageMemory(db_path)
        await self.message_memory.initialize()
        logger.info("Message memory initialized")

        # Initialize user cache (Phase 4)
        user_cache_path = Path("persistence") / f"{self.bot_id}_users.db"
        user_cache = UserCache(user_cache_path)
        await user_cache.initialize()
        logger.info("User cache initialized")

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

        # Initialize agentic engine (Phase 3)
        agentic_engine = None
        if self.config.agentic.enabled:
            from core.agentic_engine import AgenticEngine

            agentic_engine = AgenticEngine(
                config=self.config,
                memory_manager=memory_manager,
                message_memory=self.message_memory,
                anthropic_client=reactive_engine.anthropic,  # Share client
            )
            logger.info("Agentic engine initialized")

        # Initialize Discord client
        self.client = DiscordClient(
            config=self.config,
            reactive_engine=reactive_engine,
            agentic_engine=agentic_engine,
            message_memory=self.message_memory,
            user_cache=user_cache,
            conversation_logger=conversation_logger,
        )
        logger.info("Discord client initialized")

        # Set Discord client reference on agentic engine (Phase 3)
        if agentic_engine:
            agentic_engine.set_discord_client(self.client)

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

        except asyncio.CancelledError:
            logger.info("Shutdown requested")
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

            # Shutdown agentic engine (Phase 3)
            if hasattr(self, 'client') and self.client and hasattr(self.client, 'agentic_engine'):
                if self.client.agentic_engine:
                    await self.client.agentic_engine.shutdown()

            # Close Discord connection
            if self.client:
                await self.client.close()
                # Give Discord client time to finish closing
                await asyncio.sleep(0.5)

            # Close database
            if self.message_memory:
                await self.message_memory.close()

            # Cancel any remaining tasks
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if tasks:
                logger.info(f"Cancelling {len(tasks)} remaining tasks...")
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            logger.info("Shutdown complete")

            # Log active threads for debugging
            active_threads = threading.enumerate()
            if len(active_threads) > 1:  # More than just main thread
                logger.info(f"Active threads: {[t.name for t in active_threads]}")

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

        # Run with proper KeyboardInterrupt handling
        try:
            asyncio.run(manager.run())
        except KeyboardInterrupt:
            # Already handled in manager.run(), exit cleanly
            pass
        finally:
            # Force exit to prevent hanging - use os._exit for hard kill
            # This skips cleanup handlers but ensures termination
            os._exit(0)

    else:
        print(f"Error: Unknown command '{command}'")
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
