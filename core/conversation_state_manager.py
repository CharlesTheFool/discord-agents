"""
Conversation State Manager - SQLite Persistence

Manages persistent conversation states across bot restarts.
One state per channel, stored in SQLite database.
"""

import aiosqlite
import asyncio
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
        max_messages: int
    ):
        """
        Initialize conversation state manager.

        Args:
            db_path: Path to SQLite database
            bot_id: Bot identifier
            max_messages: Default max messages for new states
        """
        self.db_path = db_path
        self.bot_id = bot_id
        self.max_messages = max_messages

        # In-memory cache of loaded states
        self._cache: Dict[str, ConversationState] = {}
        # Persistent connection (opened in initialize): save() runs several
        # times per response turn, and a fresh connection spawns a new OS
        # thread per call
        self._db: Optional[aiosqlite.Connection] = None
        # Per-channel locks: two concurrent get_or_create calls for the same
        # channel must not build two state objects (last save would win)
        self._creation_locks: Dict[str, asyncio.Lock] = {}

        logger.info(
            f"ConversationStateManager initialized for bot '{bot_id}' "
            f"(db={db_path}, max_messages={max_messages})"
        )

    async def initialize(self) -> None:
        """
        Initialize database schema.

        Creates conversation_states table if it doesn't exist.
        """
        # daemon=True before the thread starts: an unclosed connection must
        # not block interpreter exit (aiosqlite's worker is a real thread)
        connection = aiosqlite.connect(self.db_path)
        connection.daemon = True
        self._db = await connection
        # WAL mode for better concurrent access (fixes database locked errors)
        await self._db.execute("PRAGMA journal_mode=WAL;")

        await self._db.execute("""
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
        await self._db.commit()

        logger.info("ConversationStateManager schema initialized")

    async def close(self) -> None:
        """Close the persistent database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def get_or_create(self, channel_id: str) -> ConversationState:
        """
        Get existing conversation state or create new one.

        Checks in-memory cache first, then database, then creates new.

        Args:
            channel_id: Discord channel ID

        Returns:
            ConversationState instance
        """
        # Fast path outside the lock
        if channel_id in self._cache:
            logger.debug(f"ConversationState cache hit for channel {channel_id}")
            return self._cache[channel_id]

        lock = self._creation_locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
            # Re-check: another task may have populated the cache while we waited
            if channel_id in self._cache:
                return self._cache[channel_id]

            state = await self.load(channel_id)

            if state:
                # The configured cap always wins over whatever was persisted
                state.max_messages = self.max_messages
                self._cache[channel_id] = state
                logger.debug(f"ConversationState loaded from DB for channel {channel_id}")
                return state

            state = ConversationState(
                channel_id=channel_id,
                max_messages=self.max_messages
            )
            await self.save(state)
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
        async with self._db.execute(
            """
            SELECT state_json FROM conversation_states
            WHERE channel_id = ? AND bot_id = ?
            """,
            (channel_id, self.bot_id)
        ) as cursor:
            row = await cursor.fetchone()

        if row:
            try:
                state_data = json.loads(row[0])
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

        await self._db.execute(
            """
            INSERT OR REPLACE INTO conversation_states
            (channel_id, bot_id, state_json, message_count, last_updated)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                state.channel_id,
                self.bot_id,
                state_json,
                len(state.messages),
                datetime.utcnow()
            )
        )
        await self._db.commit()

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
        cursor = await self._db.execute(
            """
            DELETE FROM conversation_states
            WHERE channel_id = ? AND bot_id = ?
            """,
            (channel_id, self.bot_id)
        )
        await self._db.commit()

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
            Dictionary with stats (total_states, cached_states, total_messages)
        """
        async with self._db.execute(
            """
            SELECT
                COUNT(*) as total_states,
                SUM(message_count) as total_messages
            FROM conversation_states
            WHERE bot_id = ?
            """,
            (self.bot_id,)
        ) as cursor:
            row = await cursor.fetchone()

        return {
            "total_states": row[0] or 0,
            "cached_states": len(self._cache),
            "total_messages": row[1] or 0
        }

    async def cleanup_old_states(self, days: int = 30) -> int:
        """
        Delete conversation states not updated in N days.

        Args:
            days: Days of inactivity before deletion

        Returns:
            Number of states deleted
        """
        cursor = await self._db.execute(
            """
            DELETE FROM conversation_states
            WHERE bot_id = ? AND last_updated < datetime('now', '-' || ? || ' days')
            """,
            (self.bot_id, days)
        )
        await self._db.commit()

        deleted_count = cursor.rowcount

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old conversation states (>{days} days inactive)")

        return deleted_count
