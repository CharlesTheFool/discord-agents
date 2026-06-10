"""
Attachment Database - Schema and migrations for attachment tracking system.

Handles multi-source storage (Discord CDN, local files, Files API) with quota tracking.
"""

import logging
from typing import Dict
import aiosqlite

logger = logging.getLogger(__name__)


class AttachmentDatabase:
    """Manages attachment database schema and migrations."""

    def __init__(self, db_connection: aiosqlite.Connection):
        """
        Initialize attachment database manager.

        Args:
            db_connection: Active aiosqlite connection to bot's message database
        """
        self.db = db_connection

    async def create_schema(self) -> None:
        """
        Create attachments table, indexes, and quota tracking view.
        Idempotent - safe to run multiple times.
        """
        try:
            # Main attachments table
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS attachments (
                    attachment_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,

                    -- File metadata
                    filename TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    content_type TEXT,
                    attachment_type TEXT NOT NULL,

                    -- Multi-source storage
                    discord_url TEXT,
                    local_path TEXT,
                    file_id TEXT,

                    -- Image-specific cache
                    processed_base64 TEXT,
                    processed_mime TEXT,

                    -- Timestamps
                    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    file_api_uploaded_at TIMESTAMP,

                    FOREIGN KEY (message_id) REFERENCES messages(message_id) ON DELETE CASCADE
                )
            """)

            # Indexes for efficient queries
            await self.db.execute("""
                CREATE INDEX IF NOT EXISTS idx_attachments_message
                ON attachments(message_id)
            """)

            await self.db.execute("""
                CREATE INDEX IF NOT EXISTS idx_attachments_server_channel
                ON attachments(server_id, channel_id)
            """)

            await self.db.execute("""
                CREATE INDEX IF NOT EXISTS idx_attachments_file_id
                ON attachments(file_id)
            """)

            await self.db.execute("""
                CREATE INDEX IF NOT EXISTS idx_attachments_type
                ON attachments(attachment_type)
            """)

            # Quota tracking view
            await self.db.execute("""
                CREATE VIEW IF NOT EXISTS files_api_usage AS
                SELECT
                    COUNT(*) as total_files,
                    SUM(size_bytes) as bytes_used,
                    107374182400 - COALESCE(SUM(size_bytes), 0) as bytes_remaining,
                    (COALESCE(SUM(size_bytes), 0) * 100.0 / 107374182400) as percent_used
                FROM attachments
                WHERE file_id IS NOT NULL
            """)

            await self.db.commit()
            logger.info("Attachment schema created successfully")

        except Exception as e:
            logger.error(f"Error creating attachment schema: {e}")
            raise

    async def migrate_messages_table(self) -> None:
        """
        Add attachment_count column to messages table.
        Handles case where column already exists gracefully.
        """
        try:
            await self.db.execute("""
                ALTER TABLE messages
                ADD COLUMN attachment_count INTEGER DEFAULT 0
            """)
            await self.db.commit()
            logger.info("Added attachment_count column to messages table")

        except aiosqlite.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                logger.debug("attachment_count column already exists, skipping migration")
            else:
                logger.error(f"Error migrating messages table: {e}")
                raise
        except Exception as e:
            logger.error(f"Unexpected error during messages table migration: {e}")
            raise

    async def migrate_repository_columns(self) -> None:
        """
        v0.6.1: add disk_mtime for repository-file change detection.
        Nullable REAL, used only by rows with channel_id='repository'.
        """
        try:
            await self.db.execute("""
                ALTER TABLE attachments
                ADD COLUMN disk_mtime REAL
            """)
            await self.db.commit()
            logger.info("Added disk_mtime column to attachments table")

        except aiosqlite.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                logger.debug("disk_mtime column already exists, skipping migration")
            else:
                logger.error(f"Error migrating attachments table: {e}")
                raise

