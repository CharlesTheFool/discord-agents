"""
Message Memory - SQLite Storage

Persistent message history for conversation context.
Stores Discord messages in SQLite for querying and analysis.
"""

import aiosqlite
import discord
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class StoredMessage:
    """
    Stored message representation.

    Attributes:
        message_id: Discord message ID
        channel_id: Discord channel ID
        guild_id: Discord guild/server ID
        author_id: Discord user ID
        author_name: Author's display name
        content: Message text content
        timestamp: When message was sent (UTC)
        is_bot: Whether author is a bot
        has_attachments: Whether message has attachments
        mentions: List of mentioned user IDs
    """

    message_id: str
    channel_id: str
    guild_id: str
    author_id: str
    author_name: str
    content: str
    timestamp: datetime
    is_bot: bool
    has_attachments: bool
    mentions: List[str]


class MessageMemory:
    """
    SQLite-based message storage.

    Provides persistent, queryable message history.
    Survives bot restarts.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id TEXT UNIQUE NOT NULL,
        channel_id TEXT NOT NULL,
        guild_id TEXT NOT NULL,
        author_id TEXT NOT NULL,
        author_name TEXT NOT NULL,
        content TEXT,
        timestamp DATETIME NOT NULL,
        is_bot BOOLEAN NOT NULL,
        has_attachments BOOLEAN NOT NULL,
        mentions TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_channel_timestamp
    ON messages(channel_id, timestamp DESC);

    CREATE INDEX IF NOT EXISTS idx_guild
    ON messages(guild_id);

    CREATE INDEX IF NOT EXISTS idx_author
    ON messages(author_id);

    -- FTS5 virtual table for full-text search
    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
        message_id UNINDEXED,
        content,
        author_name,
        content='messages',
        content_rowid='id'
    );

    -- Triggers to keep FTS5 table in sync
    CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, message_id, content, author_name)
        VALUES (new.id, new.message_id, new.content, new.author_name);
    END;

    CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
        DELETE FROM messages_fts WHERE rowid = old.id;
    END;

    CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
        UPDATE messages_fts
        SET content = new.content, author_name = new.author_name
        WHERE rowid = new.id;
    END;
    """

    def __init__(self, db_path: Path):
        """
        Initialize message memory.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        """
        Initialize database connection and create tables.
        Must be called before using the memory.
        """
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row

        # Create tables and indexes
        await self._db.executescript(self.SCHEMA)
        await self._db.commit()

        logger.info(f"Message memory initialized: {self.db_path}")

    async def close(self):
        """Close database connection"""
        if self._db:
            await self._db.close()
            logger.info("Message memory closed")

    async def add_message(self, message: discord.Message):
        """
        Store Discord message in database.

        Args:
            message: Discord message to store
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        # Extract mentions
        mentions = [str(user.id) for user in message.mentions]
        mentions_json = json.dumps(mentions)

        try:
            await self._db.execute(
                """
                INSERT INTO messages (
                    message_id, channel_id, guild_id,
                    author_id, author_name, content,
                    timestamp, is_bot, has_attachments, mentions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(message.id),
                    str(message.channel.id),
                    str(message.guild.id) if message.guild else "DM",
                    str(message.author.id),
                    message.author.display_name,
                    message.content,
                    message.created_at.isoformat(),
                    message.author.bot,
                    len(message.attachments) > 0,
                    mentions_json,
                ),
            )
            await self._db.commit()
            logger.debug(f"Stored message {message.id} from {message.author.name}")

        except aiosqlite.IntegrityError:
            # Message already exists (duplicate ID)
            logger.debug(f"Message {message.id} already stored, skipping")

    async def update_message(self, message: discord.Message):
        """
        Update message content when edited.

        Args:
            message: Edited Discord message
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        # Extract mentions
        mentions = [str(user.id) for user in message.mentions]
        mentions_json = json.dumps(mentions)

        await self._db.execute(
            """
            UPDATE messages
            SET content = ?, has_attachments = ?, mentions = ?
            WHERE message_id = ?
            """,
            (
                message.content,
                len(message.attachments) > 0,
                mentions_json,
                str(message.id),
            ),
        )
        await self._db.commit()
        logger.debug(f"Updated message {message.id}")

    async def delete_message(self, message_id: int):
        """
        Delete message from storage.

        Args:
            message_id: Discord message ID
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        await self._db.execute(
            "DELETE FROM messages WHERE message_id = ?",
            (str(message_id),)
        )
        await self._db.commit()
        logger.debug(f"Deleted message {message_id}")

    async def get_recent(
        self, channel_id: str, limit: int = 20
    ) -> List[StoredMessage]:
        """
        Get recent messages from channel.

        Args:
            channel_id: Discord channel ID
            limit: Maximum number of messages to return

        Returns:
            List of messages, newest first
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            SELECT * FROM messages
            WHERE channel_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (channel_id, limit),
        )

        rows = await cursor.fetchall()
        messages = [self._row_to_message(row) for row in rows]

        # Return in chronological order (oldest first)
        return list(reversed(messages))

    async def get_since(
        self, channel_id: str, since: datetime
    ) -> List[StoredMessage]:
        """
        Get messages since specific timestamp.

        Args:
            channel_id: Discord channel ID
            since: Get messages after this timestamp

        Returns:
            List of messages, oldest first
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            SELECT * FROM messages
            WHERE channel_id = ?
            AND timestamp > ?
            ORDER BY timestamp ASC
            """,
            (channel_id, since.isoformat()),
        )

        rows = await cursor.fetchall()
        return [self._row_to_message(row) for row in rows]

    async def get_channel_stats(self, channel_id: str) -> Dict:
        """
        Get statistics about channel.

        Args:
            channel_id: Discord channel ID

        Returns:
            Dictionary with message count, unique users, etc.
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        # Message count
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE channel_id = ?", (channel_id,)
        )
        total_messages = (await cursor.fetchone())[0]

        # Unique users
        cursor = await self._db.execute(
            "SELECT COUNT(DISTINCT author_id) FROM messages WHERE channel_id = ?",
            (channel_id,),
        )
        unique_users = (await cursor.fetchone())[0]

        # First and last message times
        cursor = await self._db.execute(
            """
            SELECT MIN(timestamp), MAX(timestamp)
            FROM messages
            WHERE channel_id = ?
            """,
            (channel_id,),
        )
        first_msg, last_msg = await cursor.fetchone()

        return {
            "total_messages": total_messages,
            "unique_users": unique_users,
            "first_message": first_msg,
            "last_message": last_msg,
        }

    async def cleanup_old(self, days: int = 90):
        """
        Archive/delete messages older than N days.

        Args:
            days: Delete messages older than this many days
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        cutoff = datetime.utcnow() - timedelta(days=days)

        cursor = await self._db.execute(
            "DELETE FROM messages WHERE timestamp < ?", (cutoff.isoformat(),)
        )
        await self._db.commit()

        deleted = cursor.rowcount
        logger.info(f"Cleaned up {deleted} messages older than {days} days")

    async def get_active_servers(self) -> List[str]:
        """
        Get list of unique server/guild IDs from message history.

        Returns:
            List of guild IDs that have messages in database
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            "SELECT DISTINCT guild_id FROM messages ORDER BY guild_id"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [row[0] for row in rows]

    async def get_server_for_channel(self, channel_id: str) -> Optional[str]:
        """
        Get server/guild ID for a given channel.

        Args:
            channel_id: Discord channel ID

        Returns:
            Guild ID if found, None if channel not in database
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            "SELECT guild_id FROM messages WHERE channel_id = ? LIMIT 1",
            (channel_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row[0] if row else None

    async def check_user_activity(self, user_id: str, hours: int = 24) -> bool:
        """
        Check if user has been active in the last N hours.

        Args:
            user_id: Discord user ID
            hours: Number of hours to check (default 24)

        Returns:
            True if user has posted messages within timeframe, False otherwise
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        cutoff = datetime.now() - timedelta(hours=hours)
        cursor = await self._db.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE author_id = ? AND timestamp > ?
            """,
            (user_id, cutoff.isoformat())
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row[0] > 0 if row else False

    async def search_messages(
        self,
        query: str,
        channel_id: Optional[str] = None,
        author_id: Optional[str] = None,
        limit: int = 20
    ) -> List[StoredMessage]:
        """
        Full-text search of message content using FTS5.

        Args:
            query: Search query (supports FTS5 syntax)
            channel_id: Optional channel ID to filter results
            author_id: Optional author ID to filter results
            limit: Maximum results to return (default 20)

        Returns:
            List of matching messages, ordered by relevance
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        # Build query with optional filters
        filters = []
        params = [query]

        if channel_id:
            filters.append("m.channel_id = ?")
            params.append(channel_id)

        if author_id:
            filters.append("m.author_id = ?")
            params.append(author_id)

        where_clause = " AND ".join(filters) if filters else "1=1"
        params.append(limit)

        # Execute FTS5 search
        cursor = await self._db.execute(
            f"""
            SELECT m.*
            FROM messages_fts
            JOIN messages m ON messages_fts.rowid = m.id
            WHERE messages_fts MATCH ? AND {where_clause}
            ORDER BY rank
            LIMIT ?
            """,
            tuple(params),
        )

        rows = await cursor.fetchall()
        return [self._row_to_message(row) for row in rows]

    def _row_to_message(self, row: aiosqlite.Row) -> StoredMessage:
        """
        Convert database row to StoredMessage.

        Args:
            row: Database row from query

        Returns:
            StoredMessage instance
        """
        # Parse timestamp
        timestamp = datetime.fromisoformat(row["timestamp"])

        # Parse mentions JSON
        mentions_json = row["mentions"]
        mentions = json.loads(mentions_json) if mentions_json else []

        return StoredMessage(
            message_id=row["message_id"],
            channel_id=row["channel_id"],
            guild_id=row["guild_id"],
            author_id=row["author_id"],
            author_name=row["author_name"],
            content=row["content"],
            timestamp=timestamp,
            is_bot=bool(row["is_bot"]),
            has_attachments=bool(row["has_attachments"]),
            mentions=mentions,
        )
