"""
Standing watches (v0.9) - the Prime's promises to bring an answer back.

A watch is registered when an approved ask_prime request wants the response
relayed: the agentic loop checks the target channel for new messages, a
cheap model call judges whether the question got answered, and the answer
is injected back into the originating channel ("relayed via Prime").

File: memories/{bot_id}/global/watches.json
Entry: {id, question, target_server_id, target_channel_id,
        origin_server_id, origin_channel_id, created_at, expires_at,
        last_checked_message_id}
All timestamps are timezone-aware ISO-8601 UTC.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from .internal_constants import PRIME_MAX_CONCURRENT_WATCHES, PRIME_WATCH_EXPIRY_HOURS

logger = logging.getLogger(__name__)


class WatchManager:
    """Sync file-backed watch registry; the agentic loop is the only caller."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._watches: List[dict] = []
        if self.path.exists():
            try:
                self._watches = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.error(f"Unreadable watches file ({e}) - starting fresh")
                self._watches = []

    def register(self, question: str, target_server_id: str, target_channel_id: str,
                 origin_server_id: str, origin_channel_id: str) -> Optional[dict]:
        """Add a watch; None when the concurrent cap is reached."""
        if len(self.active()) >= PRIME_MAX_CONCURRENT_WATCHES:
            logger.warning("Watch cap reached - registration refused")
            return None
        now = datetime.now(timezone.utc)
        watch = {
            "id": uuid.uuid4().hex[:12],
            "question": question,
            "target_server_id": str(target_server_id),
            "target_channel_id": str(target_channel_id),
            "origin_server_id": str(origin_server_id),
            "origin_channel_id": str(origin_channel_id),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=PRIME_WATCH_EXPIRY_HOURS)).isoformat(),
            "last_checked_message_id": None,
        }
        self._watches.append(watch)
        self._save()
        logger.info(f"Watch registered: {watch['id']} on channel {target_channel_id}")
        return watch

    def active(self) -> List[dict]:
        """Unexpired watches."""
        now = datetime.now(timezone.utc)
        return [w for w in self._watches
                if datetime.fromisoformat(w["expires_at"]) > now]

    def pop_expired(self) -> List[dict]:
        """Remove and return expired watches (caller injects no-answer notes)."""
        now = datetime.now(timezone.utc)
        expired = [w for w in self._watches
                   if datetime.fromisoformat(w["expires_at"]) <= now]
        if expired:
            self._watches = [w for w in self._watches if w not in expired]
            self._save()
        return expired

    def resolve(self, watch_id: str) -> None:
        """Remove an answered watch."""
        self._watches = [w for w in self._watches if w["id"] != watch_id]
        self._save()

    def mark_checked(self, watch_id: str, last_message_id: str) -> None:
        """Advance a watch's cursor past already-evaluated messages."""
        for w in self._watches:
            if w["id"] == watch_id:
                w["last_checked_message_id"] = str(last_message_id)
                self._save()
                return

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._watches, indent=2, ensure_ascii=False),
            encoding="utf-8")
