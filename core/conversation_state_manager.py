"""
Conversation State Manager - SQLite Persistence

Manages persistent conversation states across bot restarts.
One state per channel, stored in SQLite database.
"""

import aiosqlite
import asyncio
import copy
import hashlib
import logging
import json
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime

from .conversation_state import ConversationState

logger = logging.getLogger(__name__)


def _walk_image_blocks(messages: list, source_type: str):
    """
    Yield (container_list, index) for every image block whose source type
    matches. Recurses into tool_result content - get_attachment inlines
    images there.
    """
    stack = [
        msg["content"] for msg in messages
        if isinstance(msg.get("content"), list)
    ]
    while stack:
        blocks = stack.pop()
        for i, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            source = block.get("source")
            if (block.get("type") == "image" and isinstance(source, dict)
                    and source.get("type") == source_type):
                yield blocks, i
            elif block.get("type") == "tool_result" and isinstance(block.get("content"), list):
                stack.append(block["content"])


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

        # Content-addressed store for inline image data: states persist a
        # sha256 ref instead of re-serializing megabytes of base64 on every
        # save (save() runs several times per response turn)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS image_blobs (
                sha256 TEXT NOT NULL,
                bot_id TEXT NOT NULL,
                media_type TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (sha256, bot_id)
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
                await self._rehydrate_images(state)
                logger.debug(f"Loaded ConversationState for channel {channel_id} ({state})")
                return state

            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.error(f"Failed to deserialize ConversationState for channel {channel_id}: {e}")
                return None

        return None

    async def _rehydrate_images(self, state: ConversationState) -> None:
        """
        Swap persisted base64_ref image blocks back to inline base64 from the
        blob store. A missing blob degrades to a text placeholder rather than
        an API-invalid block.
        """
        refs = list(_walk_image_blocks(state.messages, "base64_ref"))
        if not refs:
            return

        hashes = {blocks[i]["source"]["sha256"] for blocks, i in refs}
        placeholders = ",".join("?" * len(hashes))
        async with self._db.execute(
            f"""
            SELECT sha256, media_type, data FROM image_blobs
            WHERE bot_id = ? AND sha256 IN ({placeholders})
            """,
            (self.bot_id, *hashes)
        ) as cursor:
            found = {r[0]: (r[1], r[2]) for r in await cursor.fetchall()}

        for blocks, i in refs:
            digest = blocks[i]["source"]["sha256"]
            if digest in found:
                media_type, data = found[digest]
                blocks[i] = {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                }
            else:
                logger.warning(f"Image blob {digest[:12]}... missing from store; dropping to placeholder")
                blocks[i] = {"type": "text", "text": "[image no longer available]"}

    async def save(self, state: ConversationState) -> None:
        """
        Save conversation state to database.

        Inline base64 image data is externalized to the content-addressed
        blob store; the state row keeps a sha256 ref. The in-memory state is
        untouched (deepcopy shares the immutable strings, so this is cheap).

        Args:
            state: ConversationState to save
        """
        state_dict = state.to_dict()

        if next(_walk_image_blocks(state_dict["messages"], "base64"), None):
            messages = copy.deepcopy(state_dict["messages"])
            blobs = {}
            targets = list(_walk_image_blocks(messages, "base64"))
            for blocks, i in targets:
                source = blocks[i]["source"]
                media_type = source.get("media_type", "image/jpeg")
                digest = hashlib.sha256(source["data"].encode()).hexdigest()
                blobs[digest] = (media_type, source["data"])
                blocks[i] = {
                    "type": "image",
                    "source": {"type": "base64_ref", "media_type": media_type, "sha256": digest},
                }
            for digest, (media_type, data) in blobs.items():
                await self._db.execute(
                    "INSERT OR IGNORE INTO image_blobs (sha256, bot_id, media_type, data) "
                    "VALUES (?, ?, ?, ?)",
                    (digest, self.bot_id, media_type, data)
                )
            state_dict = {**state_dict, "messages": messages}

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

        # Sweep image blobs no remaining state references (refs roll out of
        # the message cap or die with their state row)
        blob_cursor = await self._db.execute(
            """
            DELETE FROM image_blobs
            WHERE bot_id = ? AND NOT EXISTS (
                SELECT 1 FROM conversation_states cs
                WHERE cs.bot_id = image_blobs.bot_id
                  AND instr(cs.state_json, image_blobs.sha256) > 0
            )
            """,
            (self.bot_id,)
        )
        await self._db.commit()

        deleted_count = cursor.rowcount

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old conversation states (>{days} days inactive)")
        if blob_cursor.rowcount > 0:
            logger.info(f"Swept {blob_cursor.rowcount} orphaned image blob(s)")

        return deleted_count
