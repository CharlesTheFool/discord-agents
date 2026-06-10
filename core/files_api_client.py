"""
Files API Client - Anthropic Files API wrapper for Discord bot framework.

Provides async methods for uploading, retrieving, deleting, and listing files
via the Anthropic Files API (beta). Used for attaching documents to Claude messages.
"""

import logging
from typing import Dict, List, Optional
from anthropic import AsyncAnthropic, NotFoundError, PermissionDeniedError

logger = logging.getLogger(__name__)


class FilesAPIClient:
    """
    Wrapper for Anthropic Files API operations.

    Handles file uploads, downloads, deletion, and listing with proper error handling
    and logging. All operations are async and compatible with the Discord bot's
    async architecture.
    """

    def __init__(self, anthropic_client: AsyncAnthropic):
        """
        Initialize Files API client.

        Args:
            anthropic_client: Configured AsyncAnthropic client instance
        """
        self.anthropic_client = anthropic_client

    async def upload(self, filename: str, file_data: bytes, mime_type: Optional[str] = None) -> Optional[Dict]:
        """
        Upload file to Anthropic Files API.

        Args:
            filename: Name of file (with extension)
            file_data: File content as bytes
            mime_type: Optional MIME type override (e.g., "text/csv")
                      If not provided, API will auto-detect from filename

        Returns:
            Dict with keys: file_id, filename, size_bytes, mime_type
            None if upload fails
        """
        try:
            # Pass MIME type explicitly if provided for better compatibility
            if mime_type:
                response = await self.anthropic_client.beta.files.upload(
                    file=(filename, file_data, mime_type)
                )
            else:
                response = await self.anthropic_client.beta.files.upload(
                    file=(filename, file_data)
                )

            result = {
                "file_id": response.id,
                "filename": response.filename,
                "size_bytes": response.size_bytes,
                "mime_type": response.mime_type
            }

            logger.info(
                f"Uploaded file: {filename} ({result['size_bytes']} bytes) -> {result['file_id']}"
            )

            return result

        except Exception as e:
            logger.error(f"Failed to upload {filename}: {e}")
            return None

    async def delete(self, file_id: str) -> bool:
        """
        Delete file from Anthropic Files API.

        Args:
            file_id: ID of file to delete

        Returns:
            True if deletion successful, False otherwise
        """
        try:
            await self.anthropic_client.beta.files.delete(file_id)
            logger.info(f"Deleted file: {file_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete file {file_id}: {e}")
            return False

    async def retrieve(self, file_id: str) -> Optional[Dict]:
        """
        Retrieve file metadata from Anthropic Files API.

        Args:
            file_id: ID of file to retrieve metadata for

        Returns:
            Dict with file metadata (id, filename, size_bytes, mime_type, created_at)
            None if retrieval fails
        """
        try:
            response = await self.anthropic_client.beta.files.retrieve_metadata(file_id)

            return {
                "file_id": response.id,
                "filename": response.filename,
                "size_bytes": response.size_bytes,
                "mime_type": response.mime_type,
                "created_at": response.created_at
            }

        except NotFoundError as e:
            # File expired, deleted, or never existed (404)
            logger.warning(f"File {file_id} not found (expired/deleted): {e}")
            return None
        except PermissionDeniedError as e:
            # Access denied to file (403)
            logger.error(f"Permission denied for file {file_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to retrieve file {file_id}: {e}")
            return None

    async def content(self, file_id: str) -> Optional[bytes]:
        """
        Download file content from Anthropic Files API.

        Args:
            file_id: ID of file to download

        Returns:
            File content as bytes
            None if download fails
        """
        try:
            response = await self.anthropic_client.beta.files.download(file_id)
            data = await response.read()
            logger.info(f"Downloaded content for file: {file_id}")
            return data

        except Exception as e:
            logger.error(f"Failed to download content for file {file_id}: {e}")
            return None
