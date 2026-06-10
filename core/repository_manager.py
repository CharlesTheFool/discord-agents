"""
Repository Manager - the bot's persistent per-server file drive (v0.6.1).

A plain local folder (repository/{bot_id}/{server_id}/) the user edits by hand
and the bot manages through explicit `repository` tool actions. Files ride the
existing attachments table (sentinel channel_id='repository') so retrieval
reuses the attachment pipeline - classification, lazy Files API upload, image
cache, stale-file recovery - unchanged. Disk is the source of truth: scan()
reconciles before every manifest render and tool action.
"""

import os
import logging
import secrets
from pathlib import Path
from typing import Optional, Set

import aiofiles

from .attachment_classifier import AttachmentClassifier
from .internal_constants import format_size

logger = logging.getLogger(__name__)


class RepositoryManager:
    """Per-server repository folders backed by the attachments table."""

    SENTINEL = "repository"  # channel_id/message_id marker for repo rows

    def __init__(self, bot_id: str, attachment_manager):
        self.bot_id = bot_id
        self.attachment_manager = attachment_manager
        self.root = Path("repository") / bot_id
        self.root.mkdir(parents=True, exist_ok=True)
        logger.info(f"RepositoryManager initialized at {self.root}")

    @property
    def db(self):
        return self.attachment_manager.attachment_db.db

    @property
    def files_api(self):
        return self.attachment_manager.files_api_client

    def server_root(self, server_id: str) -> Path:
        path = self.root / server_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _resolve(self, server_id: str, rel_path: str) -> Path:
        """Resolve a tool-supplied path inside the jail. Raises ValueError on escape."""
        if not rel_path:
            raise ValueError("Empty repository path")
        candidate = Path(rel_path)
        if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
            raise ValueError(f"Invalid repository path: {rel_path!r}")

        root = self.server_root(server_id).resolve()
        full = (root / candidate).resolve()
        try:
            full.relative_to(root)
        except ValueError:
            raise ValueError(f"Path escapes repository: {rel_path!r}")
        return full

    def _relpath(self, server_id: str, full_path: str) -> str:
        """Path relative to the server root, for display. Falls back to the input."""
        try:
            rel = Path(full_path).resolve().relative_to(self.server_root(server_id).resolve())
            return rel.as_posix()
        except ValueError:
            return str(full_path)

    # ========== SCAN/DIFF (disk is the source of truth) ==========

    async def scan(self, server_id: str) -> None:
        """Reconcile disk vs DB for one server. Disk wins."""
        root = self.server_root(server_id)

        on_disk = {}
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                full = Path(dirpath) / name
                try:
                    stat = full.stat()
                except OSError:
                    continue  # mid-write or locked; settles on a later scan
                on_disk[str(full.resolve())] = (stat.st_size, stat.st_mtime)

        async with self.db.execute(
            "SELECT attachment_id, local_path, size_bytes, disk_mtime, file_id "
            "FROM attachments WHERE channel_id = ? AND server_id = ?",
            (self.SENTINEL, server_id),
        ) as cursor:
            rows = await cursor.fetchall()

        known = set()
        for row in rows:
            key = str(Path(row["local_path"]).resolve())
            if key not in on_disk:
                await self._deregister(row["attachment_id"], row["file_id"])
                continue
            known.add(key)
            size, mtime = on_disk[key]
            if size != row["size_bytes"] or mtime != (row["disk_mtime"] or 0):
                await self._mark_changed(row["attachment_id"], row["file_id"], size, mtime)

        for key, (size, mtime) in on_disk.items():
            if key not in known:
                await self._register(server_id, key, size, mtime)

        await self.db.commit()

    async def _register(self, server_id: str, full_path: str, size: int, mtime: float) -> str:
        attachment_id = "repo_" + secrets.token_hex(5)
        filename = Path(full_path).name
        await self.db.execute(
            """
            INSERT INTO attachments (
                attachment_id, message_id, server_id, channel_id,
                filename, size_bytes, content_type, attachment_type,
                discord_url, local_path, file_id,
                processed_base64, processed_mime, disk_mtime
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, NULL, ?)
            """,
            (
                attachment_id, self.SENTINEL, server_id, self.SENTINEL,
                filename, size,
                AttachmentClassifier.guess_mime_type(filename),
                AttachmentClassifier.classify(filename),
                full_path, mtime,
            ),
        )
        logger.info(f"Repository file registered: {self._relpath(server_id, full_path)} ({attachment_id})")
        return attachment_id

    async def _mark_changed(self, attachment_id: str, file_id: Optional[str],
                            size: int, mtime: float) -> None:
        await self._delete_files_api_copy(file_id)
        await self.db.execute(
            "UPDATE attachments SET size_bytes = ?, disk_mtime = ?, file_id = NULL, "
            "processed_base64 = NULL, processed_mime = NULL, file_api_uploaded_at = NULL "
            "WHERE attachment_id = ?",
            (size, mtime, attachment_id),
        )
        logger.info(f"Repository file changed on disk, caches invalidated: {attachment_id}")

    async def _deregister(self, attachment_id: str, file_id: Optional[str]) -> None:
        await self._delete_files_api_copy(file_id)
        await self.db.execute(
            "DELETE FROM attachments WHERE attachment_id = ?", (attachment_id,)
        )
        logger.info(f"Repository file deregistered: {attachment_id}")

    async def _delete_files_api_copy(self, file_id: Optional[str]) -> None:
        if not file_id:
            return
        try:
            await self.files_api.delete(file_id)
        except Exception as e:
            logger.warning(f"Could not delete stale Files API copy {file_id}: {e}")
