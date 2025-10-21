"""
User Cache - Discord user information storage

SQLite-based cache for quick user lookups. Reduces API calls.
"""

import aiosqlite
import discord
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CachedUser:
    """Cached user information from Discord"""
    user_id: str
    username: str
    display_name: str
    discriminator: str
    is_bot: bool
    avatar_url: str
    first_seen: datetime
    last_seen: datetime
    message_count: int


class UserCache:
    """
    SQLite-based user cache.

    Stores Discord user information for quick lookups.
    Updates automatically when users post messages.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        display_name TEXT NOT NULL,
        discriminator TEXT,
        is_bot BOOLEAN NOT NULL,
        avatar_url TEXT,
        first_seen DATETIME NOT NULL,
        last_seen DATETIME NOT NULL,
        message_count INTEGER DEFAULT 0,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_username ON users(username);
    CREATE INDEX IF NOT EXISTS idx_last_seen ON users(last_seen DESC);
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

        logger.info(f"User cache initialized: {self.db_path}")

    async def close(self):
        """Close database connection"""
        if self._db:
            await self._db.close()
            logger.info("User cache closed")

    async def update_user(self, user: discord.User, increment_messages: bool = False):
        """Update or insert user in cache"""
        if not self._db:
            raise RuntimeError("UserCache not initialized. Call initialize() first.")

        now = datetime.utcnow().isoformat()
        user_id = str(user.id)
        avatar_url = str(user.avatar.url) if user.avatar else ""
        discriminator = getattr(user, 'discriminator', '0')

        try:
            # Check for existing user
            cursor = await self._db.execute(
                "SELECT first_seen, message_count FROM users WHERE user_id = ?",
                (user_id,)
            )
            existing = await cursor.fetchone()

            if existing:
                # Update existing
                first_seen = existing['first_seen']
                message_count = existing['message_count']
                if increment_messages:
                    message_count += 1

                await self._db.execute(
                    """
                    UPDATE users
                    SET username = ?, display_name = ?, discriminator = ?,
                        is_bot = ?, avatar_url = ?, last_seen = ?,
                        message_count = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (
                        user.name, user.display_name, discriminator,
                        user.bot, avatar_url, now,
                        message_count, now, user_id,
                    ),
                )
            else:
                # Insert new
                await self._db.execute(
                    """
                    INSERT INTO users (
                        user_id, username, display_name, discriminator,
                        is_bot, avatar_url, first_seen, last_seen,
                        message_count, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id, user.name, user.display_name, discriminator,
                        user.bot, avatar_url, now, now,
                        1 if increment_messages else 0, now,
                    ),
                )

            await self._db.commit()
            logger.debug(f"Updated user cache: {user.name}")

        except Exception as e:
            logger.error(f"Error updating user cache: {e}", exc_info=True)

    async def get_user(self, user_id: str) -> Optional[CachedUser]:
        """Get cached user information"""
        if not self._db:
            raise RuntimeError("UserCache not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return CachedUser(
            user_id=row['user_id'],
            username=row['username'],
            display_name=row['display_name'],
            discriminator=row['discriminator'],
            is_bot=bool(row['is_bot']),
            avatar_url=row['avatar_url'],
            first_seen=datetime.fromisoformat(row['first_seen']),
            last_seen=datetime.fromisoformat(row['last_seen']),
            message_count=row['message_count'],
        )

    async def search_users(self, username_query: str, limit: int = 10) -> list[CachedUser]:
        """Search for users by username (partial match)"""
        if not self._db:
            raise RuntimeError("UserCache not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            """
            SELECT * FROM users
            WHERE username LIKE ? OR display_name LIKE ?
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            (f"%{username_query}%", f"%{username_query}%", limit),
        )
        rows = await cursor.fetchall()

        return [
            CachedUser(
                user_id=row['user_id'],
                username=row['username'],
                display_name=row['display_name'],
                discriminator=row['discriminator'],
                is_bot=bool(row['is_bot']),
                avatar_url=row['avatar_url'],
                first_seen=datetime.fromisoformat(row['first_seen']),
                last_seen=datetime.fromisoformat(row['last_seen']),
                message_count=row['message_count'],
            )
            for row in rows
        ]

    async def get_active_users(self, limit: int = 50) -> list[CachedUser]:
        """Get most recently active users"""
        if not self._db:
            raise RuntimeError("UserCache not initialized. Call initialize() first.")

        cursor = await self._db.execute(
            "SELECT * FROM users ORDER BY last_seen DESC LIMIT ?",
            (limit,)
        )
        rows = await cursor.fetchall()

        return [
            CachedUser(
                user_id=row['user_id'],
                username=row['username'],
                display_name=row['display_name'],
                discriminator=row['discriminator'],
                is_bot=bool(row['is_bot']),
                avatar_url=row['avatar_url'],
                first_seen=datetime.fromisoformat(row['first_seen']),
                last_seen=datetime.fromisoformat(row['last_seen']),
                message_count=row['message_count'],
            )
            for row in rows
        ]

    async def get_stats(self) -> Dict:
        """Get cache statistics"""
        if not self._db:
            raise RuntimeError("UserCache not initialized. Call initialize() first.")

        cursor = await self._db.execute("SELECT COUNT(*) FROM users")
        total_users = (await cursor.fetchone())[0]

        cursor = await self._db.execute("SELECT COUNT(*) FROM users WHERE is_bot = 1")
        bot_count = (await cursor.fetchone())[0]

        cursor = await self._db.execute("SELECT SUM(message_count) FROM users")
        total_messages = (await cursor.fetchone())[0] or 0

        return {
            "total_users": total_users,
            "bot_count": bot_count,
            "human_count": total_users - bot_count,
            "total_messages": total_messages,
        }
