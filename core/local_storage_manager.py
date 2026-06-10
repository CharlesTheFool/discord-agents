"""
Local Storage Manager - Filesystem storage for Discord attachments.

Manages local file storage with organized directory structure, path generation,
and async file I/O operations.
"""

import os
import logging
from pathlib import Path
import aiofiles

logger = logging.getLogger(__name__)


class LocalStorageManager:
    """Manages local filesystem storage for Discord attachments."""

    def __init__(self, base_path: str = "persistence/attachments"):
        """Initialize storage manager.

        Args:
            base_path: Base directory for attachment storage
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"LocalStorageManager initialized with base_path: {self.base_path}")

    def get_path(self, server_id: str, channel_id: str, message_id: str, filename: str) -> str:
        """Generate storage path for an attachment.

        Args:
            server_id: Discord server ID
            channel_id: Discord channel ID
            message_id: Discord message ID
            filename: Original filename

        Returns:
            Full file path as string
        """
        # Sanitize filename to prevent directory traversal
        safe_filename = Path(filename).name

        file_path = self.base_path / server_id / channel_id / f"{message_id}_{safe_filename}"
        return str(file_path)

    async def save(self, file_data: bytes, server_id: str, channel_id: str, message_id: str, filename: str) -> str:
        """Save file data to disk.

        Args:
            file_data: File content as bytes
            server_id: Discord server ID
            channel_id: Discord channel ID
            message_id: Discord message ID
            filename: Original filename

        Returns:
            Full file path where data was saved

        Raises:
            OSError: If file save fails
        """
        file_path = self.get_path(server_id, channel_id, message_id, filename)

        try:
            # Create directory structure
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)

            # Write file
            async with aiofiles.open(file_path, 'wb') as f:
                await f.write(file_data)

            file_size = len(file_data)
            logger.info(f"Saved attachment: {filename} ({file_size} bytes) -> {file_path}")
            return file_path

        except Exception as e:
            logger.error(f"Failed to save {filename}: {e}")
            raise

    async def load(self, file_path: str) -> bytes:
        """Load file data from disk.

        Args:
            file_path: Full path to file

        Returns:
            File content as bytes

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        # Validate path is within base_path to prevent directory traversal
        try:
            resolved_path = Path(file_path).resolve()
            resolved_base = self.base_path.resolve()
            if not str(resolved_path).startswith(str(resolved_base)):
                raise ValueError(f"Invalid file path: {file_path}")
        except Exception as e:
            logger.error(f"Path validation failed for {file_path}: {e}")
            raise

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        try:
            async with aiofiles.open(file_path, 'rb') as f:
                data = await f.read()

            logger.info(f"Loaded attachment from: {file_path}")
            return data

        except Exception as e:
            logger.error(f"Failed to load {file_path}: {e}")
            raise

    def exists(self, file_path: str) -> bool:
        """Check if file exists on disk.

        Args:
            file_path: Full path to file

        Returns:
            True if file exists, False otherwise
        """
        return os.path.exists(file_path)

    async def delete(self, file_path: str) -> bool:
        """Delete file from disk.

        Args:
            file_path: Full path to file

        Returns:
            True if successful, False otherwise
        """
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Deleted attachment: {file_path}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete {file_path}: {e}")
            return False

    async def get_size(self, file_path: str) -> int:
        """Get file size in bytes.

        Args:
            file_path: Full path to file

        Returns:
            File size in bytes

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        return os.path.getsize(file_path)
