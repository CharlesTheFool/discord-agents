"""
Conversation State Manager - SQLite Persistence

Manages persistent conversation states across bot restarts.
One state per channel, stored in SQLite database.
"""

import aiosqlite
import logging
import json
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime

from .conversation_state import ConversationState

logger = logging.getLogger(__name__)


class ConversationStateManager:
    """
    Manages persistent conversation states with SQLite backend.

    Stores one ConversationState per channel with automatic
    load/save operations.
    """

    def __init__(
        self,
        db_path: Path,
        bot_id: str,
        max_messages: int,
        max_tokens: int
    ):
        """
        Initialize conversation state manager.

        Args:
            db_path: Path to SQLite database
            bot_id: Bot identifier
            max_messages: Default max messages for new states
            max_tokens: Default max tokens for new states
        """
        self.db_path = db_path
        self.bot_id = bot_id
        self.max_messages = max_messages
        self.max_tokens = max_tokens

        # In-memory cache of loaded states
        self._cache: Dict[str, ConversationState] = {}

        logger.info(
            f"ConversationStateManager initialized for bot '{bot_id}' "
            f"(db={db_path}, max_messages={max_messages}, max_tokens={max_tokens})"
        )

    async def initialize(self) -> None:
        """
        Initialize database schema.

        Creates conversation_states table if it doesn't exist.
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Enable WAL mode for better concurrent access (fixes database locked errors)
            await db.execute("PRAGMA journal_mode=WAL;")

            await db.execute("""
                CREATE TABLE IF NOT EXISTS conversation_states (
                    channel_id TEXT NOT NULL,
                    bot_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    token_count INTEGER DEFAULT 0,
                    message_count INTEGER DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (channel_id, bot_id)
                )
            """)
            await db.commit()

        logger.info("ConversationStateManager schema initialized")

    async def get_or_create(self, channel_id: str) -> ConversationState:
        """
        Get existing conversation state or create new one.

        Checks in-memory cache first, then database, then creates new.

        Args:
            channel_id: Discord channel ID

        Returns:
            ConversationState instance
        """
        # Check cache first
        if channel_id in self._cache:
            logger.debug(f"ConversationState cache hit for channel {channel_id}")
            return self._cache[channel_id]

        # Try loading from database
        state = await self.load(channel_id)

        if state:
            # Add to cache
            self._cache[channel_id] = state
            logger.debug(f"ConversationState loaded from DB for channel {channel_id}")
            return state

        # Create new state
        state = ConversationState(
            channel_id=channel_id,
            max_messages=self.max_messages,
            max_tokens=self.max_tokens
        )

        # Save to database
        await self.save(state)

        # Add to cache
        self._cache[channel_id] = state

        logger.info(f"Created new ConversationState for channel {channel_id}")
        return state

    async def load(self, channel_id: str) -> Optional[ConversationState]:
        """
        Load conversation state from database.

        Args:
            channel_id: Discord channel ID

        Returns:
            ConversationState if found, None otherwise
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                """
                SELECT state_json FROM conversation_states
                WHERE channel_id = ? AND bot_id = ?
                """,
                (channel_id, self.bot_id)
            ) as cursor:
                row = await cursor.fetchone()

                if row:
                    try:
                        state_data = json.loads(row["state_json"])
                        state = ConversationState.from_dict(state_data)
                        logger.debug(f"Loaded ConversationState for channel {channel_id} ({state})")
                        return state

                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        logger.error(f"Failed to deserialize ConversationState for channel {channel_id}: {e}")
                        return None

        return None

    async def save(self, state: ConversationState) -> None:
        """
        Save conversation state to database.

        Args:
            state: ConversationState to save
        """
        state_dict = state.to_dict()
        state_json = json.dumps(state_dict)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO conversation_states
                (channel_id, bot_id, state_json, token_count, message_count, last_updated)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    state.channel_id,
                    self.bot_id,
                    state_json,
                    state.conversation_tokens,
                    len(state.messages),
                    datetime.utcnow()
                )
            )
            await db.commit()

        logger.debug(f"Saved ConversationState for channel {state.channel_id} ({state})")

    async def delete(self, channel_id: str) -> bool:
        """
        Delete conversation state from database and cache.

        Args:
            channel_id: Discord channel ID

        Returns:
            True if deleted, False if not found
        """
        # Remove from cache
        if channel_id in self._cache:
            del self._cache[channel_id]

        # Remove from database
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM conversation_states
                WHERE channel_id = ? AND bot_id = ?
                """,
                (channel_id, self.bot_id)
            )
            await db.commit()

            deleted = cursor.rowcount > 0

        if deleted:
            logger.info(f"Deleted ConversationState for channel {channel_id}")
        else:
            logger.debug(f"No ConversationState found to delete for channel {channel_id}")

        return deleted

    async def clear_cache(self) -> None:
        """Clear in-memory cache of conversation states"""
        self._cache.clear()
        logger.debug("ConversationState cache cleared")

    async def get_stats(self) -> Dict[str, int]:
        """
        Get statistics about conversation states.

        Returns:
            Dictionary with stats (total_states, cached_states, total_messages, total_tokens)
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                """
                SELECT
                    COUNT(*) as total_states,
                    SUM(message_count) as total_messages,
                    SUM(token_count) as total_tokens
                FROM conversation_states
                WHERE bot_id = ?
                """,
                (self.bot_id,)
            ) as cursor:
                row = await cursor.fetchone()

                return {
                    "total_states": row["total_states"] or 0,
                    "cached_states": len(self._cache),
                    "total_messages": row["total_messages"] or 0,
                    "total_tokens": row["total_tokens"] or 0
                }

    async def cleanup_old_states(self, days: int = 30) -> int:
        """
        Delete conversation states not updated in N days.

        Args:
            days: Days of inactivity before deletion

        Returns:
            Number of states deleted
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM conversation_states
                WHERE bot_id = ? AND last_updated < datetime('now', '-' || ? || ' days')
                """,
                (self.bot_id, days)
            )
            await db.commit()

            deleted_count = cursor.rowcount

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old conversation states (>{days} days inactive)")

        return deleted_count
