"""
Web Search Integration - Quota Management

Anthropic's web_search and web_fetch tools are built into the Claude API.
This module manages daily quotas and tracks usage.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class WebSearchManager:
    """
    Manages web search quotas and tracking.

    The actual web search is performed by Anthropic's API tools.
    This class only tracks usage against configured limits.
    """

    def __init__(self, stats_file: Path, max_daily: int = 300, reset_hour: int = 0):
        self.stats_file = stats_file
        self.max_daily = max_daily
        self.reset_hour = reset_hour  # UTC hour for quota reset

        self.stats = self._load_stats()
        self._check_reset()

        logger.info(f"WebSearchManager initialized (max_daily={max_daily})")

    def _load_stats(self) -> Dict:
        """Load stats from file or initialize fresh"""
        if self.stats_file.exists():
            try:
                with open(self.stats_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load web search stats: {e}")

        return {
            "last_reset": datetime.utcnow().isoformat(),
            "searches_today": 0,
            "total_searches": 0
        }

    def _save_stats(self):
        """Persist stats to disk"""
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.stats_file, 'w') as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save web search stats: {e}")

    def _check_reset(self):
        """Reset daily counter if past configured reset time"""
        last_reset = datetime.fromisoformat(self.stats["last_reset"])
        now = datetime.utcnow()

        # Calculate when next reset should occur
        next_reset = last_reset.replace(hour=self.reset_hour, minute=0, second=0, microsecond=0)
        if now >= last_reset:
            next_reset += timedelta(days=1)

        # Perform reset if we've crossed the threshold
        if now >= next_reset:
            logger.info(f"Daily web search quota reset (was: {self.stats['searches_today']})")
            self.stats["searches_today"] = 0
            self.stats["last_reset"] = now.isoformat()
            self._save_stats()

    def can_search(self) -> tuple[bool, Optional[str]]:
        """
        Check if web search is allowed under quota.

        Returns:
            (allowed, reason_if_blocked)
        """
        self._check_reset()

        if self.stats["searches_today"] >= self.max_daily:
            return False, f"Daily quota exceeded ({self.max_daily} searches/day)"

        return True, None

    def record_search(self):
        """Increment usage counters after successful search"""
        self.stats["searches_today"] += 1
        self.stats["total_searches"] += 1
        self._save_stats()

        logger.info(f"Web search recorded: {self.stats['searches_today']}/{self.max_daily} today")

    def get_stats(self) -> Dict:
        """
        Get current usage statistics.

        Returns:
            {
                "searches_today": int,
                "searches_remaining": int,
                "total_searches": int,
                "last_reset": str (ISO format)
            }
        """
        self._check_reset()

        return {
            "searches_today": self.stats["searches_today"],
            "searches_remaining": max(0, self.max_daily - self.stats["searches_today"]),
            "total_searches": self.stats["total_searches"],
            "last_reset": self.stats["last_reset"]
        }


def get_web_search_tools(max_uses: int = 3, citations_enabled: bool = True) -> list:
    """
    Generate web search tool definitions for Claude API.

    These are Anthropic's built-in tools, not custom implementations.

    Args:
        max_uses: Maximum searches per request (prevents runaway usage)
        citations_enabled: Enable citations for web_fetch (required for end-user apps)
    """
    return [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": max_uses
        },
        {
            "type": "web_fetch_20250910",
            "name": "web_fetch",
            "max_uses": max_uses,
            "citations": {"enabled": citations_enabled}
        }
    ]
