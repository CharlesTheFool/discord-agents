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

    # ========== MANIFEST ==========

    MANIFEST_CAP = 30

    async def render_manifest(self, server_id: str, in_context_ids: Set[str]) -> str:
        """
        <repository> section for the volatile tail. Scans first (disk is
        truth), renders newest-modified first, capped at MANIFEST_CAP.
        Empty string when the repo is empty (keeps the tail lean).
        """
        await self.scan(server_id)

        async with self.db.execute(
            "SELECT attachment_id, filename, size_bytes, attachment_type, local_path "
            "FROM attachments WHERE channel_id = ? AND server_id = ? "
            "ORDER BY disk_mtime DESC LIMIT ?",
            (self.SENTINEL, server_id, self.MANIFEST_CAP),
        ) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            return ""

        async with self.db.execute(
            "SELECT COUNT(*) AS n FROM attachments WHERE channel_id = ? AND server_id = ?",
            (self.SENTINEL, server_id),
        ) as cursor:
            total = (await cursor.fetchone())["n"]

        lines = [
            "<repository>",
            "Your persistent file repository for this server (a local folder the user can also edit directly):",
        ]
        for row in rows:
            marker = "in context" if row["attachment_id"] in in_context_ids else "not in context"
            rel = self._relpath(server_id, row["local_path"])
            lines.append(
                f"- {rel} | {format_size(row['size_bytes'])} | {row['attachment_type']} "
                f"| {row['attachment_id']} | {marker}"
            )
        if total > len(rows):
            lines.append(f"... showing {len(rows)} of {total} total - use the repository tool's list action for the full tree.")
        lines += [
            "",
            "Retrieve any file with the discord tool: get_attachment + attachment_id.",
            "Manage files with the repository tool: save_file, save_attachment, save_output, delete, rename, list.",
            "</repository>",
        ]
        return "\n".join(lines)

    # ========== TOOL ACTIONS ==========

    async def execute(self, tool_input: dict, current_server_id: str) -> str:
        """
        Execute a repository tool action. Always returns a string for the
        tool_result; never raises. Scans first so views match the disk.
        """
        action = tool_input.get("action", "")
        try:
            await self.scan(current_server_id)

            if action == "list":
                return await self._list(current_server_id)
            elif action == "save_file":
                return await self._save_file(current_server_id, tool_input)
            elif action == "save_attachment":
                return await self._save_attachment(current_server_id, tool_input)
            elif action == "save_output":
                return await self._save_output(current_server_id, tool_input)
            elif action == "delete":
                return await self._delete(current_server_id, tool_input)
            elif action == "rename":
                return await self._rename(current_server_id, tool_input)
            else:
                return f"Error: Unknown repository action '{action}'"

        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error(f"Repository action '{action}' failed: {e}", exc_info=True)
            return f"Error executing repository action '{action}': {e}"

    async def _row_for_path(self, server_id: str, full_path: Path):
        async with self.db.execute(
            "SELECT * FROM attachments WHERE channel_id = ? AND server_id = ? AND local_path = ?",
            (self.SENTINEL, server_id, str(full_path.resolve())),
        ) as cursor:
            return await cursor.fetchone()

    async def _list(self, server_id: str) -> str:
        async with self.db.execute(
            "SELECT attachment_id, filename, size_bytes, attachment_type, local_path "
            "FROM attachments WHERE channel_id = ? AND server_id = ? ORDER BY local_path",
            (self.SENTINEL, server_id),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return ("Repository is empty. Save files with save_file, save_attachment "
                    "or save_output - or the user can drop files into the folder directly.")

        lines = [f"Repository contents ({len(rows)} file(s)):"]
        for row in rows:
            rel = self._relpath(server_id, row["local_path"])
            lines.append(
                f"- {rel} | {format_size(row['size_bytes'])} | "
                f"{row['attachment_type']} | {row['attachment_id']}"
            )
        lines.append("\nRetrieve any file with the discord tool: get_attachment + attachment_id.")
        return "\n".join(lines)

    async def _save_file(self, server_id: str, tool_input: dict) -> str:
        rel_path = tool_input.get("path", "")
        content = tool_input.get("content")
        if not rel_path or content is None:
            return "Error: save_file requires 'path' and 'content'"

        full = self._resolve(server_id, rel_path)
        full.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(full, "w", encoding="utf-8") as f:
            await f.write(content)

        await self.scan(server_id)  # registers new / refreshes existing row
        row = await self._row_for_path(server_id, full)
        rel = self._relpath(server_id, str(full))
        return (f"Saved {rel} ({format_size(len(content.encode('utf-8')))}) "
                f"to your repository. Attachment ID: {row['attachment_id']}")

    async def _save_attachment(self, server_id: str, tool_input: dict) -> str:
        source_id = tool_input.get("attachment_id", "")
        if not source_id:
            return "Error: save_attachment requires 'attachment_id'"

        async with self.db.execute(
            "SELECT filename, local_path FROM attachments WHERE attachment_id = ?",
            (source_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return f"Error: Attachment {source_id} not found"
        if not row["local_path"] or not Path(row["local_path"]).exists():
            return (f"Error: Attachment {source_id} has no local copy to import "
                    f"(try get_attachment first, or the file is metadata-only)")

        rel_path = tool_input.get("path") or Path(row["filename"]).name
        full = self._resolve(server_id, rel_path)
        full.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(row["local_path"], "rb") as src:
            data = await src.read()
        async with aiofiles.open(full, "wb") as dst:
            await dst.write(data)

        await self.scan(server_id)
        new_row = await self._row_for_path(server_id, full)
        rel = self._relpath(server_id, str(full))
        return (f"Copied {row['filename']} into your repository as {rel} "
                f"({format_size(len(data))}). Attachment ID: {new_row['attachment_id']}")

    async def _save_output(self, server_id: str, tool_input: dict) -> str:
        file_id = tool_input.get("file_id", "")
        if not file_id:
            return "Error: save_output requires 'file_id'"

        meta = await self.files_api.retrieve(file_id)
        data = await self.files_api.content(file_id)
        if not data:
            return (f"Error: Could not download {file_id}. Only files created by "
                    f"code execution or skills are downloadable from the Files API.")

        default_name = (meta or {}).get("filename") or f"{file_id}.bin"
        rel_path = tool_input.get("path") or Path(default_name).name
        full = self._resolve(server_id, rel_path)
        full.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(full, "wb") as f:
            await f.write(data)

        await self.scan(server_id)
        new_row = await self._row_for_path(server_id, full)
        rel = self._relpath(server_id, str(full))
        return (f"Saved container output to your repository as {rel} "
                f"({format_size(len(data))}). Attachment ID: {new_row['attachment_id']}")

    async def _delete(self, server_id: str, tool_input: dict) -> str:
        rel_path = tool_input.get("path", "")
        if not rel_path:
            return "Error: delete requires 'path'"

        full = self._resolve(server_id, rel_path)
        if not full.is_file():
            return f"Error: No repository file at {rel_path!r}"

        row = await self._row_for_path(server_id, full)
        full.unlink()
        if row:
            await self._deregister(row["attachment_id"], row["file_id"])
            await self.db.commit()
        rel = self._relpath(server_id, str(full))
        return f"Deleted {rel} from your repository."

    async def _rename(self, server_id: str, tool_input: dict) -> str:
        old_rel = tool_input.get("old_path", "")
        new_rel = tool_input.get("new_path", "")
        if not old_rel or not new_rel:
            return "Error: rename requires 'old_path' and 'new_path'"

        old_full = self._resolve(server_id, old_rel)
        new_full = self._resolve(server_id, new_rel)
        if not old_full.is_file():
            return f"Error: No repository file at {old_rel!r}"
        if new_full.exists():
            return f"Error: Destination already exists: {new_rel!r}"

        row = await self._row_for_path(server_id, old_full)
        new_full.parent.mkdir(parents=True, exist_ok=True)
        old_full.rename(new_full)

        if row:  # identity survives a bot-initiated rename
            await self.db.execute(
                "UPDATE attachments SET local_path = ?, filename = ? WHERE attachment_id = ?",
                (str(new_full.resolve()), new_full.name, row["attachment_id"]),
            )
            await self.db.commit()
        return f"Renamed {old_rel} to {self._relpath(server_id, str(new_full))}."
