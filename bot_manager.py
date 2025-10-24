#!/usr/bin/env python3
"""
Discord Agents - Bot Manager CLI

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

sys.path.insert(0, str(Path(__file__).parent))

from core.config import BotConfig
from core.rate_limiter import RateLimiter
from core.message_memory import MessageMemory
from core.memory_manager import MemoryManager
from core.reactive_engine import ReactiveEngine
from core.discord_client import DiscordClient
from core.user_cache import UserCache


class BotManager:
    """
    Manages bot lifecycle: initialization, component setup, graceful shutdown.
    """

    def __init__(self, bot_id: str, crash_test: bool = False):
        self.bot_id = bot_id
        self.crash_test = crash_test
        self.config: BotConfig = None
        self.client: DiscordClient = None
        self.message_memory: MessageMemory = None

        self._setup_logging()

    def _setup_logging(self):
        """Configure file and console logging with reduced discord.py noise"""
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler(log_dir / f"{self.bot_id}.log"),
            ],
        )

        # Discord.py is chatty - quiet it down
        logging.getLogger("discord").setLevel(logging.WARNING)
        logging.getLogger("discord.http").setLevel(logging.WARNING)

    async def initialize(self):
        """
        Initialize all bot components in dependency order.
        Returns Discord token for client.start().
        """
        logger = logging.getLogger(__name__)
        logger.info(f"Initializing bot '{self.bot_id}'...")

        load_dotenv()

        # Locate config file (prefer .yaml, fall back to .yaml.example)
        config_path = Path(f"bots/{self.bot_id}.yaml")
        if not config_path.exists():
            template_path = Path(f"bots/{self.bot_id}.yaml.example")
            if template_path.exists():
                logger.warning(
                    f"Using template config for {self.bot_id}. "
                    f"Copy to bots/{self.bot_id}.yaml and customize."
                )
                config_path = template_path
            else:
                raise FileNotFoundError(
                    f"No config found for bot '{self.bot_id}'.\n"
                    f"Expected: bots/{self.bot_id}.yaml or bots/{self.bot_id}.yaml.example"
                )

        self.config = BotConfig.load(config_path)
        logger.info(f"Loaded config for bot '{self.config.name}'")

        # Fail fast if config is invalid
        validation_errors = self.config.validate()
        if validation_errors:
            logger.error(f"[{self.bot_id}] Configuration validation failed:")
            for error in validation_errors:
                logger.error(f"  - {error}")
            sys.exit(1)

        logger.info(f"[{self.bot_id}] Configuration validated successfully")

        # Extract API credentials (already validated)
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        discord_token = os.getenv(self.config.discord.token_env_var)

        # Apply log level from config
        log_level = getattr(logging, self.config.logging.level)
        logging.getLogger().setLevel(log_level)

        # Initialize message memory (SQLite for conversation history)
        db_path = Path("persistence") / f"{self.bot_id}_messages.db"
        db_path.parent.mkdir(exist_ok=True)

        self.message_memory = MessageMemory(db_path)
        await self.message_memory.initialize()
        logger.info("Message memory initialized")

        # Initialize user cache (username/display name lookups)
        user_cache_path = Path("persistence") / f"{self.bot_id}_users.db"
        user_cache = UserCache(user_cache_path)
        await user_cache.initialize()
        logger.info("User cache initialized")

        # Initialize memory manager (markdown-based long-term memory)
        memory_base_path = Path("memories")
        memory_manager = MemoryManager(self.bot_id, memory_base_path)
        logger.info("Memory manager initialized")

        # Initialize conversation logger (human-readable conversation dumps)
        from core.conversation_logger import ConversationLogger
        log_dir = Path("logs")
        conversation_logger = ConversationLogger(self.bot_id, log_dir)
        logger.info("Conversation logger initialized")

        # Initialize rate limiter (prevent API abuse)
        rate_limiter_config = {
            "short_window_minutes": self.config.rate_limiting.short.duration_minutes,
            "short_window_max": self.config.rate_limiting.short.max_responses,
            "long_window_minutes": self.config.rate_limiting.long.duration_minutes,
            "long_window_max": self.config.rate_limiting.long.max_responses,
            "ignore_threshold": self.config.rate_limiting.ignore_threshold,
        }
        rate_limiter = RateLimiter(rate_limiter_config)
        logger.info("Rate limiter initialized")

        # Initialize reactive engine (handles message reactions)
        reactive_engine = ReactiveEngine(
            config=self.config,
            rate_limiter=rate_limiter,
            message_memory=self.message_memory,
            memory_manager=memory_manager,
            anthropic_api_key=anthropic_key,
            conversation_logger=conversation_logger,
        )
        logger.info("Reactive engine initialized")

        # Initialize agentic engine if enabled (proactive actions)
        agentic_engine = None
        if self.config.agentic.enabled:
            from core.agentic_engine import AgenticEngine

            agentic_engine = AgenticEngine(
                config=self.config,
                memory_manager=memory_manager,
                message_memory=self.message_memory,
                anthropic_client=reactive_engine.anthropic,
            )
            logger.info("Agentic engine initialized")

        # Initialize Discord client (ties everything together)
        self.client = DiscordClient(
            config=self.config,
            reactive_engine=reactive_engine,
            agentic_engine=agentic_engine,
            message_memory=self.message_memory,
            user_cache=user_cache,
            conversation_logger=conversation_logger,
        )
        logger.info("Discord client initialized")

        # Wire up circular dependency: agentic engine needs client reference
        if agentic_engine:
            agentic_engine.set_discord_client(self.client)

        logger.info("Bot initialization complete!")

        return discord_token

    async def run(self):
        """
        Connect to Discord and run until interrupted.
        """
        logger = logging.getLogger(__name__)

        try:
            discord_token = await self.initialize()

            # Schedule crash test if enabled
            if self.crash_test:
                async def crash_after_delay():
                    await asyncio.sleep(5)
                    logger.warning("CRASH TEST: Forcefully terminating in 1 second...")
                    await asyncio.sleep(1)
                    logger.error("CRASH TEST: Simulating crash via os._exit(1)")
                    os._exit(1)

                asyncio.create_task(crash_after_delay())
                logger.warning("CRASH TEST MODE: Bot will crash in 6 seconds")

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
        """Graceful shutdown: cleanup engines, close connections, cancel tasks"""
        logger = logging.getLogger(__name__)
        logger.info("Shutting down bot...")

        try:
            # Insert offline lifecycle event before shutdown
            if self.client and self.message_memory:
                from datetime import datetime
                offline_time = datetime.utcnow()

                for guild in self.client.guilds:
                    for channel in guild.text_channels:
                        try:
                            await self.message_memory.insert_system_message(
                                content="[YOU WENT OFFLINE]",
                                channel_id=str(channel.id),
                                guild_id=str(guild.id),
                                timestamp=offline_time
                            )
                        except Exception as e:
                            logger.debug(f"Error inserting offline event to channel {channel.id}: {e}")

                logger.info("Offline lifecycle events inserted")

            # Remove running flag (indicates clean shutdown)
            from pathlib import Path
            flag_file = Path(f"persistence/{self.bot_id}_running.flag")
            if flag_file.exists():
                flag_file.unlink()
                logger.info("Running flag removed (clean shutdown)")

            # Shutdown engines (cancel background tasks)
            if hasattr(self, 'client') and self.client and hasattr(self.client, 'reactive_engine'):
                await self.client.reactive_engine.shutdown()

            if hasattr(self, 'client') and self.client and hasattr(self.client, 'agentic_engine'):
                if self.client.agentic_engine:
                    await self.client.agentic_engine.shutdown()

            # Close Discord connection
            if self.client:
                await self.client.close()
                await asyncio.sleep(0.5)  # Let Discord finish cleanup

            # Close database
            if self.message_memory:
                await self.message_memory.close()

            # Cancel any lingering tasks
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if tasks:
                logger.info(f"Cancelling {len(tasks)} remaining tasks...")
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            logger.info("Shutdown complete")

            # Log remaining threads for debugging hangs
            active_threads = threading.enumerate()
            if len(active_threads) > 1:
                logger.info(f"Active threads: {[t.name for t in active_threads]}")

        except Exception as e:
            logger.error(f"Error during shutdown: {e}")


def print_usage():
    """Print CLI usage"""
    print("Discord Agents - Bot Manager")
    print()
    print("Usage:")
    print("  python bot_manager.py spawn <bot_id>             - Start a bot")
    print("  python bot_manager.py spawn <bot_id> --crash-test - Start and crash after 6s (tests crash detection)")
    print("  python bot_manager.py --help                     - Show this help")
    print()
    print("Examples:")
    print("  python bot_manager.py spawn alpha                - Start the alpha bot")
    print("  python bot_manager.py spawn slh-01 --crash-test  - Test crash detection")
    print()
    print("Configuration:")
    print("  1. Copy .env.example to .env")
    print("  2. Fill in DISCORD_BOT_TOKEN and ANTHROPIC_API_KEY")
    print("  3. Create or edit bot config in bots/<bot_id>.yaml")
    print("  4. Run: python bot_manager.py spawn <bot_id>")


def main():
    """CLI entry point"""
    if len(sys.argv) < 2 or sys.argv[1] in ["--help", "-h", "help"]:
        print_usage()
        sys.exit(0)

    command = sys.argv[1]

    if command == "spawn":
        if len(sys.argv) < 3:
            print("Error: Missing bot_id")
            print("Usage: python bot_manager.py spawn <bot_id> [--crash-test]")
            sys.exit(1)

        bot_id = sys.argv[2]

        # Check for --crash-test flag
        crash_test = "--crash-test" in sys.argv

        manager = BotManager(bot_id, crash_test=crash_test)

        try:
            asyncio.run(manager.run())
        except KeyboardInterrupt:
            pass
        finally:
            # Hard exit to prevent hanging from stubborn threads
            os._exit(0)

    else:
        print(f"Error: Unknown command '{command}'")
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
