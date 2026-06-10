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
