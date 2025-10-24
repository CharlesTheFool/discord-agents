"""
Message Memory - SQLite Storage

Persistent message history with FTS5 full-text search.
Handles Discord message storage, updates, and retrieval.
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
    """Message representation in storage"""
    message_id: str
    channel_id: str
    guild_id: str
    author_id: str
    author_name: str
    content: str
    timestamp: datetime
    is_bot: bool
    is_system: bool
    has_attachments: bool
    mentions: List[str]


class MessageMemory:
    """
    SQLite message storage with full-text search.

    Provides persistent, queryable message history across bot restarts.
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
        is_system BOOLEAN NOT NULL DEFAULT 0,
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

    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
        message_id UNINDEXED,
        content,
        author_name,
        content='messages',
        content_rowid='id'
    );

    CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, message_id, content, author_name)
        VALUES (new.id, new.message_id, new.content, new.author_name);
    END;

    CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
        DELETE FROM messages_fts WHERE rowid = old.id;
    END;

    CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
        UPDATE messages_fts SET content = new.content, author_name = new.author_name
        WHERE rowid = old.id;
    END;
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        """Initialize database connection and create tables"""
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row

        await self._db.executescript(self.SCHEMA)
        await self._db.commit()

        # Run migrations for existing databases
        await self._run_migrations()

        logger.info(f"Message memory initialized: {self.db_path}")

    async def _run_migrations(self):
        """Run database migrations for schema updates"""
        if not self._db:
            return

        # Migration: Add is_system column if it doesn't exist
        try:
            cursor = await self._db.execute("PRAGMA table_info(messages)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            if "is_system" not in column_names:
                logger.info("Running migration: Adding is_system column to messages table")
                await self._db.execute("ALTER TABLE messages ADD COLUMN is_system BOOLEAN NOT NULL DEFAULT 0")
                await self._db.commit()
                logger.info("Migration complete: is_system column added")
            else:
                logger.debug("is_system column already exists, skipping migration")

        except Exception as e:
            logger.error(f"Error running migrations: {e}", exc_info=True)

    async def close(self):
        """Close database connection"""
        if self._db:
            await self._db.close()
            logger.info("Message memory closed")

    async def add_message(self, message: discord.Message):
        """
        Store Discord message in database.

        Handles forwarded messages and embeds.
        Updates content if message already exists (UPSERT pattern).
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        mentions = [str(user.id) for user in message.mentions]
        mentions_json = json.dumps(mentions)

        # Extract content from message and embeds
        content_parts = []
        if message.content:
            content_parts.append(message.content)

        # Check for forwarded messages (Discord API limitation)
        if message.reference:
            ref_type = getattr(message.reference, 'type', None)
            if ref_type is not None:
                from discord import MessageReferenceType
                if ref_type == MessageReferenceType.forward:
                    # Discord doesn't provide forwarded content via API
                    content_parts.append("[Forwarded message - content not accessible]")
                    logger.debug(f"Message {message.id} is a forwarded message")

        # Extract embed content
        if message.embeds:
            logger.info(f"[EMBED] Message {message.id} has {len(message.embeds)} embeds")
            for idx, embed in enumerate(message.embeds):
                has_title = bool(embed.title)
                has_desc = bool(embed.description)
                logger.info(f"  [EMBED] Embed {idx}: type={embed.type}, title={has_title}, desc={has_desc}, fields={len(embed.fields)}")

                if has_title:
                    logger.info(f"    Title: {embed.title[:100]}")
                if has_desc:
                    logger.info(f"    Description: {embed.description[:100]}")

                if embed.description:
                    content_parts.append(embed.description)
                if embed.title:
                    content_parts.append(embed.title)
                for field in embed.fields:
                    if field.value:
                        content_parts.append(field.value)

        full_content = "\n".join(content_parts)
        if message.embeds and not message.content:
            logger.info(f"[EMBED] Message {message.id} is embed-only, extracted content length: {len(full_content)}")
        elif not message.content and not content_parts:
            logger.warning(f"[EMPTY] Message {message.id} has NO content (no text, no embeds with content)")

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
                    full_content,
                    message.created_at.isoformat(),
                    message.author.bot,
                    len(message.attachments) > 0,
                    mentions_json,
                ),
            )
            await self._db.commit()
            logger.debug(f"Stored message {message.id} from {message.author.name}")

        except aiosqlite.IntegrityError:
            # Message exists - check if content changed before updating
            cursor = await self._db.execute(
                "SELECT content FROM messages WHERE message_id = ?",
                (str(message.id),)
            )
            row = await cursor.fetchone()
            existing_content = row[0] if row else None

            # Only update if content actually changed
            if existing_content != full_content:
                logger.info(f"[UPSERT] Message {message.id} content CHANGED during backfill")
                logger.info(f"[UPSERT] OLD: {existing_content[:100]}...")
                logger.info(f"[UPSERT] NEW: {full_content[:100]}...")
                await self._db.execute(
                    """
                    UPDATE messages
                    SET content = ?, has_attachments = ?, mentions = ?, author_name = ?
                    WHERE message_id = ?
                    """,
                    (
                        full_content,
                        len(message.attachments) > 0,
                        mentions_json,
                        message.author.display_name,
                        str(message.id),
                    ),
                )
                await self._db.commit()
                logger.info(f"[UPSERT] Successfully updated message {message.id}")
            else:
                logger.debug(f"Message {message.id} unchanged, skipping update")

    async def update_message(self, message: discord.Message):
        """
        Update message content when edited.

        If message doesn't exist (e.g., edited message older than backfill window),
        insert it instead (UPSERT pattern).
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        logger.info(f"[EDIT] Updating message {message.id} from {message.author.name}")

        mentions = [str(user.id) for user in message.mentions]
        mentions_json = json.dumps(mentions)

        # Extract content
        content_parts = []
        if message.content:
            logger.info(f"[EDIT] Original content: {message.content[:200]}")
            content_parts.append(message.content)

        if message.embeds:
            logger.info(f"[EMBED UPDATE] Message {message.id}: has {len(message.embeds)} embeds")
            for idx, embed in enumerate(message.embeds):
                logger.info(f"  [EMBED UPDATE] Embed {idx}: type={embed.type}, title={bool(embed.title)}, desc={bool(embed.description)}, fields={len(embed.fields)}")
                if embed.description:
                    content_parts.append(embed.description)
                if embed.title:
                    content_parts.append(embed.title)
                for field in embed.fields:
                    if field.value:
                        content_parts.append(field.value)

        full_content = "\n".join(content_parts)
        logger.info(f"[EDIT] Full extracted content ({len(full_content)} chars): {full_content[:200]}")

        if message.embeds and not message.content:
            logger.info(f"[EMBED UPDATE] Message {message.id} is embed-only, extracted content length: {len(full_content)}")

        # Try UPDATE first
        logger.info(f"[EDIT] Attempting UPDATE for message {message.id}")
        cursor = await self._db.execute(
            """
            UPDATE messages
            SET content = ?, has_attachments = ?, mentions = ?
            WHERE message_id = ?
            """,
            (
                full_content,
                len(message.attachments) > 0,
                mentions_json,
                str(message.id),
            ),
        )

        rows_updated = cursor.rowcount
        logger.info(f"[EDIT] UPDATE affected {rows_updated} row(s)")

        # If no rows updated, INSERT instead (UPSERT pattern)
        if rows_updated == 0:
            logger.info(f"[UPSERT] Message {message.id} not in database, inserting (probably older than backfill window)")

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
                        full_content,
                        message.created_at.isoformat(),
                        message.author.bot,
                        len(message.attachments) > 0,
                        mentions_json,
                    ),
                )
                logger.info(f"[UPSERT] Successfully inserted message {message.id} into database")
            except aiosqlite.IntegrityError:
                # Race condition: message inserted between UPDATE and INSERT
                logger.warning(f"[UPSERT] Message {message.id} already exists (race condition during UPSERT)")
        else:
            logger.info(f"[EDIT] Successfully updated existing message {message.id} in database")

        await self._db.commit()
        logger.info(f"[EDIT] Database committed for message {message.id}")

    async def delete_message(self, message_id: int):
        """Delete message from storage"""
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        await self._db.execute(
            "DELETE FROM messages WHERE message_id = ?",
            (str(message_id),)
        )
        await self._db.commit()
        logger.debug(f"Deleted message {message_id}")

    async def insert_system_message(
        self, content: str, channel_id: str, guild_id: str, timestamp: datetime
    ):
        """
        Insert a system message (lifecycle event) into the database.

        System messages appear in message history but are marked with is_system=True.
        They roll out of context naturally with regular messages.

        Args:
            content: System message content (e.g., "[YOU CAME ONLINE]")
            channel_id: Channel ID (use "SYSTEM" for bot-wide events)
            guild_id: Guild ID
            timestamp: Timestamp of the event
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        # Generate unique message ID for system message
        message_id = f"system_{timestamp.timestamp()}"

        try:
            await self._db.execute(
                """
                INSERT INTO messages (
                    message_id, channel_id, guild_id,
                    author_id, author_name, content,
                    timestamp, is_bot, is_system, has_attachments, mentions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    channel_id,
                    guild_id,
                    "SYSTEM",  # author_id
                    "System",  # author_name
                    content,
                    timestamp.isoformat(),
                    False,  # is_bot
                    True,   # is_system
                    False,  # has_attachments
                    "[]",   # mentions (empty)
                ),
            )
            await self._db.commit()
            logger.info(f"Inserted system message: {content} at {timestamp}")

        except aiosqlite.IntegrityError:
            # System message already exists (e.g., duplicate startup)
            logger.debug(f"System message already exists: {message_id}")

    async def get_recent(
        self, channel_id: str, limit: int = 20, exclude_message_ids: List[int] = None
    ) -> List[StoredMessage]:
        """
        Get recent messages from channel, ordered chronologically.

        Optionally exclude specific message IDs (e.g., to filter in-flight messages).
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        # Build query with optional exclusion filter
        if exclude_message_ids and len(exclude_message_ids) > 0:
            excluded_ids_str = [str(mid) for mid in exclude_message_ids]
            placeholders = ",".join("?" * len(excluded_ids_str))

            cursor = await self._db.execute(
                f"""
                SELECT * FROM messages
                WHERE channel_id = ?
                AND message_id NOT IN ({placeholders})
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (channel_id, *excluded_ids_str, limit),
            )
        else:
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

    async def get_first_messages(
        self, channel_id: str, limit: int = 20
    ) -> List[StoredMessage]:
        """Get first (oldest) messages from channel for understanding channel history"""
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            SELECT * FROM messages
            WHERE channel_id = ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (channel_id, limit),
        )

        rows = await cursor.fetchall()
        return [self._row_to_message(row) for row in rows]

    async def get_since(
        self, channel_id: str, since: datetime
    ) -> List[StoredMessage]:
        """Get messages since specific timestamp"""
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
        """Get channel statistics (message count, unique users, time range)"""
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE channel_id = ?", (channel_id,)
        )
        total_messages = (await cursor.fetchone())[0]

        cursor = await self._db.execute(
            "SELECT COUNT(DISTINCT author_id) FROM messages WHERE channel_id = ?",
            (channel_id,),
        )
        unique_users = (await cursor.fetchone())[0]

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

    async def get_user_message_count(self, user_id: str, server_id: str = None) -> int:
        """Get total message count for user"""
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE author_id = ?", (user_id,)
        )

        return (await cursor.fetchone())[0]

    async def cleanup_old(self, days: int = 90):
        """Delete messages older than N days"""
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
        """Get list of unique server/guild IDs from message history"""
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            "SELECT DISTINCT guild_id FROM messages ORDER BY guild_id"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [row[0] for row in rows]

    async def get_server_for_channel(self, channel_id: str) -> Optional[str]:
        """Get server/guild ID for a given channel"""
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
        """Check if user has posted messages within timeframe"""
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

    async def get_message_context(
        self,
        message_id: str,
        channel_id: str,
        before: int = 2,
        after: int = 2
    ) -> Dict[str, List[StoredMessage]]:
        """Get messages surrounding a specific message for context"""
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        # Get target message to know its timestamp
        cursor = await self._db.execute(
            "SELECT * FROM messages WHERE message_id = ? AND channel_id = ?",
            (message_id, channel_id)
        )
        target_row = await cursor.fetchone()

        if not target_row:
            return {"before": [], "match": None, "after": []}

        target_msg = self._row_to_message(target_row)
        target_timestamp = target_msg.timestamp

        # Get messages before
        cursor = await self._db.execute(
            """
            SELECT * FROM messages
            WHERE channel_id = ? AND timestamp < ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (channel_id, target_timestamp.isoformat(), before)
        )
        before_rows = await cursor.fetchall()
        before_messages = [self._row_to_message(row) for row in reversed(before_rows)]

        # Get messages after
        cursor = await self._db.execute(
            """
            SELECT * FROM messages
            WHERE channel_id = ? AND timestamp > ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (channel_id, target_timestamp.isoformat(), after)
        )
        after_rows = await cursor.fetchall()
        after_messages = [self._row_to_message(row) for row in after_rows]

        return {
            "before": before_messages,
            "match": target_msg,
            "after": after_messages
        }

    async def search_messages(
        self,
        query: str,
        channel_id: Optional[str] = None,
        author_id: Optional[str] = None,
        limit: int = 20
    ) -> List[StoredMessage]:
        """
        Full-text search using FTS5.

        Query is sanitized to prevent FTS5 syntax errors.
        """
        if not self._db:
            raise RuntimeError("MessageMemory not initialized. Call initialize() first.")

        # Sanitize query for FTS5: escape double quotes and wrap in quotes
        # Treats query as literal phrase search, avoiding syntax errors
        sanitized_query = f'"{query.replace('"', '""')}"'
        filters = []
        params = [sanitized_query]

        if channel_id:
            filters.append("m.channel_id = ?")
            params.append(channel_id)

        if author_id:
            filters.append("m.author_id = ?")
            params.append(author_id)

        where_clause = " AND ".join(filters) if filters else "1=1"
        params.append(limit)

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
        """Convert database row to StoredMessage"""
        timestamp = datetime.fromisoformat(row["timestamp"])
        # Strip timezone to ensure all timestamps are naive UTC
        if timestamp.tzinfo is not None:
            timestamp = timestamp.replace(tzinfo=None)

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
            is_system=bool(row["is_system"]) if "is_system" in row.keys() else False,
            has_attachments=bool(row["has_attachments"]),
            mentions=mentions,
        )
