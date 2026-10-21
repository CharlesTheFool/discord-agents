"""
Unified Attachment Manager - Orchestration layer for attachment processing.

Coordinates all attachment operations across storage modes:
- files_api_primary: Upload to Files API, cache compressed images
- local_unlimited: Save locally, on-demand upload later
- metadata_only: Store metadata without file content
"""

import asyncio
import logging
from typing import Optional, Dict, TYPE_CHECKING
import discord
from anthropic import AsyncAnthropic

from core.files_api_client import FilesAPIClient
from core.local_storage_manager import LocalStorageManager
from core.attachment_classifier import AttachmentClassifier
from core.internal_constants import format_size
from core.attachment_database import AttachmentDatabase
from tools.image_processor import ImageProcessor

if TYPE_CHECKING:
    from core.config import BotConfig
    from core.message_memory import MessageMemory

logger = logging.getLogger(__name__)


class UnifiedAttachmentManager:
    """Orchestrates attachment processing based on storage mode."""

    # Size threshold for document blocks (100KB): bigger files would
    # materialize their whole content into the context window; they go to
    # the workshop through get_attachment instead
    DOCUMENT_BLOCK_SIZE_THRESHOLD = 100 * 1024  # 100KB
    # Where the workshop is off, a document asked for with get_attachment is
    # read as one up to this size (0.14.1). The API's own limits (pages,
    # context) still apply; a refusal heals in the tool loop.
    READ_DOCUMENT_MAX_BYTES = 4 * 1024 * 1024

    @classmethod
    def should_use_document_block(cls, filename: str, size_bytes: int, read: bool = False) -> bool:
        """
        Does the model read this file as a document block? A document's whole
        content materializes into context, so only small PDFs and text files
        qualify; everything else is worked on in the workshop (get_attachment
        copies it into /workspace/inbox). read: asked for explicitly where
        there's no workshop to open it in, so a larger one qualifies.
        """
        if not AttachmentClassifier.is_document_block_eligible(filename):
            return False
        limit = cls.READ_DOCUMENT_MAX_BYTES if read else cls.DOCUMENT_BLOCK_SIZE_THRESHOLD
        if size_bytes > limit:
            logger.debug(f"File {filename} ({size_bytes} bytes) is over the document limit ({limit} bytes)")
            return False

        return True

    def __init__(
        self,
        config: "BotConfig",
        anthropic_client: AsyncAnthropic,
        message_memory: "MessageMemory"
    ):
        """
        Initialize unified attachment manager.

        Args:
            config: Bot configuration with attachments settings
            anthropic_client: Anthropic API client for Files API
            message_memory: Message memory for database access
        """
        self.config = config
        self.anthropic_client = anthropic_client  # Store for token counting (Phase 3)

        # Initialize Phase 1 components
        self.files_api_client = FilesAPIClient(anthropic_client)
        self.local_storage = LocalStorageManager(
            config.attachments.local_storage.base_path
        )
        self.image_processor = ImageProcessor()

        # Database
        self.attachment_db = AttachmentDatabase(message_memory._db)

        # Per-attachment locks: concurrent processing of the same attachment
        # must not race the file_id check past each other (duplicate uploads)
        self._upload_locks: Dict[str, asyncio.Lock] = {}

    def _upload_lock_for(self, attachment_id: str) -> asyncio.Lock:
        if attachment_id not in self._upload_locks:
            self._upload_locks[attachment_id] = asyncio.Lock()
        return self._upload_locks[attachment_id]

    async def initialize(self) -> None:
        """Create schema and run migrations."""
        await self.attachment_db.create_schema()
        await self.attachment_db.migrate_messages_table()
        await self.attachment_db.migrate_repository_columns()
        logger.info("UnifiedAttachmentManager initialized with local-first storage")

    async def process_attachment(
        self,
        attachment: discord.Attachment,
        message: discord.Message,
        is_realtime: bool = True
    ) -> Optional[Dict]:
        """
        Process Discord attachment based on storage mode.

        Args:
            attachment: Discord attachment object
            message: Discord message containing attachment
            is_realtime: True if processing during message arrival

        Returns:
            Dict with processing result:
            {
                "attachment_id": str,
                "attachment_type": str,
                "for_api": {
                    "method": "base64" | "file_id",
                    "data": ...
                }
            }
            None if processing fails
        """
        # Classify attachment type
        att_type = AttachmentClassifier.classify(attachment.filename)
        logger.info(
            f"Processing {att_type} attachment: {attachment.filename} "
            f"({attachment.size} bytes)"
        )

        # Download attachment data
        try:
            file_data = await attachment.read()
        except Exception as e:
            logger.error(f"Failed to download attachment {attachment.id}: {e}")
            return None

        # Process with local-first storage
        try:
            return await self._process_attachment(
                attachment, message, file_data, att_type, is_realtime
            )
        except Exception as e:
            logger.error(f"Error processing attachment {attachment.id}: {e}", exc_info=True)
            return None

    async def _process_attachment(
        self,
        attachment: discord.Attachment,
        message: discord.Message,
        file_data: bytes,
        att_type: str,
        is_realtime: bool
    ) -> Optional[Dict]:
        """
        Process attachment with local-first storage.

        Strategy:
        1. Save to local storage (primary)
        2. Store local_path in database
        3. For images: compress to base64 for immediate API use
        4. Files API used only as a cache for documents the model reads
        """
        # Save to local storage
        local_path = await self.local_storage.save(
            file_data=file_data,
            server_id=str(message.guild.id) if message.guild else "DM",
            channel_id=str(message.channel.id),
            message_id=str(message.id),
            filename=attachment.filename
        )

        # Process image to base64 for immediate API use
        processed_base64 = None
        processed_mime = None

        if att_type == "image":
            image_result = await self._process_image(attachment, file_data, att_type)
            if image_result:
                processed_base64 = image_result["source"]["data"]
                processed_mime = image_result["source"]["media_type"]

        # Store attachment record
        await self._store_attachment_record(
            attachment_id=str(attachment.id),
            message_id=str(message.id),
            server_id=str(message.guild.id) if message.guild else "DM",
            channel_id=str(message.channel.id),
            filename=attachment.filename,
            size_bytes=attachment.size,
            attachment_type=att_type,
            discord_url=attachment.url,
            local_path=local_path,
            processed_base64=processed_base64,
            processed_mime=processed_mime,
            content_type=AttachmentClassifier.guess_mime_type(attachment.filename)
        )

        # For images: return base64
        if att_type == "image" and processed_base64:
            return {
                "attachment_id": str(attachment.id),
                "attachment_type": att_type,
                "filename": attachment.filename,
                "size_bytes": attachment.size,  # Phase 5: File size disclosure
                "for_api": {
                    "method": "base64",
                    "data": processed_base64,
                    "media_type": processed_mime,
                    "use_as_document_block": False  # Images use image blocks, not document blocks
                }
            }

        # Only what the model reads as a document goes to the Files API; any
        # other file stays local until get_attachment copies it into a
        # workshop (0.14 - nothing runs server-side any more)
        if not self.should_use_document_block(attachment.filename, attachment.size):
            return {
                "attachment_id": str(attachment.id),
                "attachment_type": att_type,
                "filename": attachment.filename,
                "size_bytes": attachment.size,
                "for_api": {"method": "local", "use_as_document_block": False},
            }

        # Check-then-upload must be atomic per attachment: without the lock,
        # concurrent calls both miss the cached file_id and upload twice
        async with self._upload_lock_for(str(attachment.id)):
            async with self.attachment_db.db.execute(
                "SELECT file_id FROM attachments WHERE attachment_id = ?",
                (str(attachment.id),)
            ) as cursor:
                existing_row = await cursor.fetchone()

            if existing_row and existing_row[0]:
                # File already uploaded - reuse cached file_id
                cached_file_id = existing_row[0]
                logger.info(f"Reusing cached file_id for {attachment.filename}: {cached_file_id} (avoiding re-upload)")

                use_as_doc_block = self.should_use_document_block(attachment.filename, attachment.size)
                return {
                    "attachment_id": str(attachment.id),
                    "attachment_type": att_type,
                    "filename": attachment.filename,
                    "size_bytes": attachment.size,
                    "for_api": {
                        "method": "file_id",
                        "data": cached_file_id,
                        "use_as_document_block": use_as_doc_block
                    }
                }

            # No cached file_id - proceed with upload
            # For non-image files: upload to Files API with correct MIME type
            mime_type = AttachmentClassifier.get_files_api_mime_type(attachment.filename)
            upload_result = await self.files_api_client.upload(
                filename=attachment.filename,
                file_data=file_data,
                mime_type=mime_type
            )

            if upload_result:
                file_id = upload_result["file_id"]

                # Update database with file_id
                await self.attachment_db.db.execute(
                    "UPDATE attachments SET file_id = ?, file_api_uploaded_at = CURRENT_TIMESTAMP WHERE attachment_id = ?",
                    (file_id, str(attachment.id))
                )
                await self.attachment_db.db.commit()

                logger.info(f"Uploaded document to Files API: {attachment.filename} -> {file_id}")

                use_as_doc_block = self.should_use_document_block(attachment.filename, attachment.size)

                return {
                    "attachment_id": str(attachment.id),
                    "attachment_type": att_type,
                    "filename": attachment.filename,
                    "size_bytes": attachment.size,  # Phase 5: File size disclosure
                    "for_api": {
                        "method": "file_id",
                        "data": file_id,
                        "use_as_document_block": use_as_doc_block
                    }
                }

        # Fallback if upload fails
        logger.warning(f"Failed to upload {attachment.filename} to Files API, returning None")
        return {
            "attachment_id": str(attachment.id),
            "attachment_type": att_type,
            "filename": attachment.filename,
            "size_bytes": attachment.size,  # Phase 5: File size disclosure
            "for_api": None,
            "use_as_document_block": False
        }

    async def delete_attachments_for_message(self, message_id: str) -> int:
        """
        Remove all traces of a deleted Discord message's attachments: local
        copies, Files API uploads (best effort), and the database rows.
        Without this, deleted user content stayed retrievable forever.

        Returns number of attachments removed.
        """
        async with self.attachment_db.db.execute(
            "SELECT attachment_id, file_id, local_path, filename FROM attachments WHERE message_id = ?",
            (str(message_id),)
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return 0

        for attachment_id, file_id, local_path, filename in rows:
            if local_path and self.local_storage.exists(local_path):
                try:
                    await self.local_storage.delete(local_path)
                except Exception as e:
                    logger.warning(f"Could not delete local copy of {filename}: {e}")
            if file_id:
                try:
                    await self.files_api_client.delete(file_id)
                except Exception as e:
                    logger.warning(f"Could not delete Files API entry {file_id}: {e}")

        await self.attachment_db.db.execute(
            "DELETE FROM attachments WHERE message_id = ?", (str(message_id),)
        )
        await self.attachment_db.db.commit()

        logger.info(f"Purged {len(rows)} attachment(s) for deleted message {message_id}")
        return len(rows)

    @staticmethod
    def build_content_block(for_api: Optional[Dict], filename: str, role: str = "user",
                            attachment_id: Optional[str] = None) -> Optional[Dict]:
        """
        Content block for a processed attachment, respecting API role rules:
        image and file blocks are only valid in user turns, so assistant
        turns get a text placeholder instead.

        Images persist inline (by reference to image_blobs). Every other
        file persists as an attachment_ref - our own id, never a provider's
        file id - and is resolved per request (resolve_ref, spec 4.6).

        The one source of truth for this mapping (the 0.6 audit found six
        hand-rolled, divergent copies of it).

        Returns None when there is nothing to embed (failed processing).
        """
        if not for_api:
            return None
        if role != "user":
            return {"type": "text", "text": f"\n[Attachment: {filename}]"}
        if for_api["method"] == "base64":
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": for_api["media_type"],
                    "data": for_api["data"],
                },
            }
        if attachment_id:
            return {"type": "attachment_ref", "attachment_id": attachment_id}
        return None

    async def _row(self, attachment_id: str) -> Optional[Dict]:
        async with self.attachment_db.db.execute(
            "SELECT * FROM attachments WHERE attachment_id = ?", (attachment_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def resolve_ref(self, attachment_id: str, read: bool = False) -> Dict:
        """What an attachment_ref becomes in a request (spec 4.6): small
        PDFs and text files (larger ones when read on request) as a document
        through the Files API, used as a cache (uploaded from local storage
        when the id is missing or stale); images inline; anything else a note
        pointing at get_attachment."""
        row = await self._row(attachment_id)
        if row is None:
            return {"type": "text", "text": "\n[An attachment that is no longer available]"}
        filename, size = row["filename"], row.get("size_bytes") or 0
        document = row.get("attachment_type") != "image" and self.should_use_document_block(filename, size, read)
        if document and row.get("file_id"):
            # the cached id as is: checking it costs a GET per document per
            # model call, and a transient failure there would re-upload and
            # move the cached prefix. A truly expired id 404s as
            # StaleFileError, which forgets it and retries (the tool loop).
            return {"type": "document", "source": {"type": "file", "file_id": row["file_id"]}}
        if row.get("attachment_type") == "image" or document:
            data = await self.get_attachment_for_processing(attachment_id)
            if data and data["method"] == "file_id":
                return {"type": "document", "source": {"type": "file", "file_id": data["data"]}}
            if data and data["method"] == "base64":
                return {"type": "image", "source": {"type": "base64", "media_type": data["media_type"],
                                                    "data": data["data"]}}
        return {"type": "text", "text": (f"\n[Attachment: {filename} ({format_size(size)}) - "
                                         f"open it with get_attachment]")}

    async def attachment_for_file_id(self, file_id: str) -> Optional[str]:
        """Which attachment a provider file id belongs to (healing old states)."""
        async with self.attachment_db.db.execute(
            "SELECT attachment_id FROM attachments WHERE file_id = ?", (file_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else None

    async def forget_file_id(self, file_id: str) -> bool:
        """The Files API no longer has this id: drop it, so the next request
        re-uploads from local storage. True if an attachment held it."""
        cursor = await self.attachment_db.db.execute(
            "UPDATE attachments SET file_id = NULL WHERE file_id = ?", (file_id,))
        await self.attachment_db.db.commit()
        return cursor.rowcount > 0

    async def local_bytes(self, attachment_id: str) -> Optional[bytes]:
        """The file's bytes from local storage (what goes into a workshop)."""
        row = await self._row(attachment_id)
        if not row or not row.get("local_path") or not self.local_storage.exists(row["local_path"]):
            return None
        return await self.local_storage.load(row["local_path"])

    async def get_attachment_for_processing(self, attachment_id: str) -> Optional[Dict]:
        """
        Retrieve attachment for retroactive processing.

        Fallback strategy (local-first):
        1. Try local_path (primary storage) → upload to Files API on-demand if needed
        2. Try cached base64 (for images)
        3. Try discord_url (last resort fallback)

        Args:
            attachment_id: ID of attachment to retrieve

        Returns:
            Dict suitable for Claude API or None if unavailable
        """
        # Same per-attachment lock as initial processing: the on-demand and
        # expiration re-uploads below share the check-then-upload race
        async with self._upload_lock_for(attachment_id):
            return await self._get_attachment_for_processing_locked(attachment_id)

    async def _get_attachment_for_processing_locked(self, attachment_id: str) -> Optional[Dict]:
        # Query database for attachment record
        async with self.attachment_db.db.execute(
            "SELECT * FROM attachments WHERE attachment_id = ?",
            (attachment_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            logger.warning(f"Attachment {attachment_id} not found in database")
            return None

        # Extract fields (SQLite row as dict)
        attachment_data = dict(row)
        file_id = attachment_data.get("file_id")
        local_path = attachment_data.get("local_path")
        discord_url = attachment_data.get("discord_url")
        processed_base64 = attachment_data.get("processed_base64")
        processed_mime = attachment_data.get("processed_mime")
        attachment_type = attachment_data.get("attachment_type")
        filename = attachment_data.get("filename")
        size_bytes = attachment_data.get("size_bytes") or 0  # For large file detection

        # Images always present as image blocks - a cached file_id must NOT
        # shortcut them into the document route. Prefer cached
        # base64, then on-demand processing from the local file (repository
        # rows have no upload-time cache).
        if attachment_type == "image":
            if processed_base64:
                logger.info(f"Using cached base64 for attachment {attachment_id}")
                return {
                    "method": "base64",
                    "data": processed_base64,
                    "media_type": processed_mime,
                    "use_as_document_block": False
                }
            if local_path and self.local_storage.exists(local_path):
                try:
                    file_data = await self.local_storage.load(local_path)
                    image_result = await self.image_processor.process_bytes(filename, file_data)
                    if image_result:
                        b64 = image_result["source"]["data"]
                        mime = image_result["source"]["media_type"]
                        await self.attachment_db.db.execute(
                            "UPDATE attachments SET processed_base64 = ?, processed_mime = ? "
                            "WHERE attachment_id = ?",
                            (b64, mime, attachment_id)
                        )
                        await self.attachment_db.db.commit()
                        logger.info(f"Processed image on demand: {attachment_id}")
                        return {
                            "method": "base64",
                            "data": b64,
                            "media_type": mime,
                            "use_as_document_block": False
                        }
                except Exception as e:
                    logger.error(f"On-demand image processing failed: {e}", exc_info=True)
            # No usable local data - fall through to the generic strategies

        # Strategy 1: Use file_id if cached
        if file_id:
            # Check if file still exists in Files API
            metadata = await self.files_api_client.retrieve(file_id)
            if metadata:
                logger.info(f"Using cached file_id for attachment {attachment_id}")
                use_as_doc_block = self.should_use_document_block(filename, size_bytes)
                return {
                    "method": "file_id",
                    "data": file_id,
                    "use_as_document_block": use_as_doc_block
                }
            else:
                # File expired/missing - attempt recovery from local storage
                logger.warning(f"File {file_id} no longer exists in Files API, attempting recovery")
                if local_path:
                    try:
                        use_as_doc_block = self.should_use_document_block(filename, size_bytes)
                        new_file_id = await self._handle_file_expiration(
                            attachment_id, file_id, local_path, filename
                        )
                        if new_file_id:
                            # Recovery successful - return with new file_id
                            logger.info(f"Successfully recovered from expiration: {attachment_id}")
                            return {
                                "method": "file_id",
                                "data": new_file_id,
                                "use_as_document_block": use_as_doc_block
                            }
                    except Exception as e:
                        logger.warning(f"Failed to recover from file expiration: {e}")

                # If recovery failed or no local_path, fall through to Strategy 2
                logger.debug(f"Falling back to Strategy 2 for {attachment_id}")

        # Strategy 2: Load from local storage
        if local_path and self.local_storage.exists(local_path):
            try:
                file_data = await self.local_storage.load(local_path)

                # Upload to Files API on-demand with correct MIME type
                # (images were already handled above and never reach here)
                mime_type = AttachmentClassifier.get_files_api_mime_type(filename)
                upload_result = await self.files_api_client.upload(
                    filename=filename,
                    file_data=file_data,
                    mime_type=mime_type
                )

                if upload_result:
                    # Cache file_id for future use
                    await self.attachment_db.db.execute(
                        "UPDATE attachments SET file_id = ?, file_api_uploaded_at = CURRENT_TIMESTAMP WHERE attachment_id = ?",
                        (upload_result["file_id"], attachment_id)
                    )
                    await self.attachment_db.db.commit()

                    logger.info(f"Uploaded local file to Files API: {attachment_id}")
                    use_as_doc_block = self.should_use_document_block(filename, size_bytes)
                    return {
                        "method": "file_id",
                        "data": upload_result["file_id"],
                        "use_as_document_block": use_as_doc_block
                    }

            except Exception as e:
                logger.error(f"Failed to load from local storage: {e}", exc_info=True)

        # Strategy 3: Try Discord URL (may be expired)
        # (cached-base64 fallback for images now lives at the top of the chain)
        if discord_url:
            logger.warning(
                f"All cached sources failed for {attachment_id}. "
                f"Discord URL available but may be expired: {discord_url}"
            )
            # Note: Downloading from Discord URL requires additional implementation
            # Left as future enhancement

        logger.error(f"Cannot retrieve attachment {attachment_id}: All sources failed")
        return None

    async def _handle_file_expiration(
        self,
        attachment_id: str,
        file_id: str,
        local_path: str,
        filename: str
    ) -> Optional[str]:
        """
        Handle expired file_id by re-uploading from local storage.

        Args:
            attachment_id: Discord attachment ID
            file_id: Expired Files API file_id
            local_path: Path to local copy
            filename: Original filename

        Returns:
            New file_id if re-upload succeeds, None otherwise
        """
        if not self.local_storage.exists(local_path):
            logger.error(f"Cannot recover from expiration: local_path missing {local_path}")
            return None

        try:
            # Load file from disk
            file_data = await self.local_storage.load(local_path)

            # Re-upload to Files API
            mime_type = AttachmentClassifier.get_files_api_mime_type(filename)
            upload_result = await self.files_api_client.upload(
                filename=filename,
                file_data=file_data,
                mime_type=mime_type
            )

            if upload_result:
                new_file_id = upload_result["file_id"]

                # Update database with new file_id
                await self.attachment_db.db.execute(
                    "UPDATE attachments SET file_id = ?, file_api_uploaded_at = CURRENT_TIMESTAMP WHERE attachment_id = ?",
                    (new_file_id, attachment_id)
                )
                await self.attachment_db.db.commit()

                logger.info(f"Successfully recovered from file expiration: {file_id} -> {new_file_id}")
                return new_file_id
            else:
                logger.error(f"Failed to re-upload {filename} after expiration")
                return None

        except Exception as e:
            logger.error(f"Error recovering from file expiration for {attachment_id}: {e}", exc_info=True)
            return None

    async def _process_image(
        self,
        attachment: discord.Attachment,
        file_data: bytes,
        att_type: str
    ) -> Optional[Dict]:
        """
        Use existing ImageProcessor for compression.

        Args:
            attachment: Discord attachment
            file_data: Raw image bytes
            att_type: Attachment type classification

        Returns:
            Dict in Claude API format or None
        """
        if att_type != "image":
            return None

        # ImageProcessor requires Discord attachment object, which we have
        try:
            result = await self.image_processor.process_attachment(attachment)
            if result:
                logger.info(
                    f"Compressed image {attachment.filename}: "
                    f"{attachment.size} bytes -> "
                    f"base64 encoded"
                )
            return result
        except Exception as e:
            logger.error(f"Image processing failed for {attachment.filename}: {e}", exc_info=True)
            return None

    async def _store_attachment_record(
        self,
        attachment_id: str,
        message_id: str,
        server_id: str,
        channel_id: str,
        filename: str,
        size_bytes: int,
        attachment_type: str,
        **kwargs
    ) -> None:
        """
        Insert attachment record into database.

        Args:
            attachment_id: Discord attachment ID
            message_id: Discord message ID
            server_id: Discord server/guild ID
            channel_id: Discord channel ID
            filename: Original filename
            size_bytes: File size in bytes
            attachment_type: Classified type (image, document, etc.)
            **kwargs: Optional fields (discord_url, local_path, file_id, etc.)
        """
        discord_url = kwargs.get("discord_url")
        local_path = kwargs.get("local_path")
        file_id = kwargs.get("file_id")
        processed_base64 = kwargs.get("processed_base64")
        processed_mime = kwargs.get("processed_mime")
        content_type = kwargs.get("content_type")

        try:
            await self.attachment_db.db.execute(
                """
                INSERT OR IGNORE INTO attachments (
                    attachment_id, message_id, server_id, channel_id,
                    filename, size_bytes, content_type, attachment_type,
                    discord_url, local_path, file_id,
                    processed_base64, processed_mime
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attachment_id, message_id, server_id, channel_id,
                    filename, size_bytes, content_type, attachment_type,
                    discord_url, local_path, file_id,
                    processed_base64, processed_mime
                )
            )
            await self.attachment_db.db.commit()

            logger.info(
                f"Stored attachment record: {filename} "
                f"(type={attachment_type}, size={format_size(size_bytes)})"
            )

        except Exception as e:
            logger.error(f"Failed to store attachment record {attachment_id}: {e}", exc_info=True)
            raise
