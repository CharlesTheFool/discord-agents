"""
Rate Limiter - Two-window rate limiting with engagement adaptation

Ported from original bot implementation (slh.py lines 119-164).
DO NOT MODIFY: Battle-tested algorithm tuned for small servers.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict
import logging

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Two-window rate limiting with engagement-based adaptation.

    Short window: 5 minutes, max 20 responses
    Long window: 1 hour, max 200 responses

    Adaptation: After 5 consecutive ignores (no reaction/reply within 30s),
    bot goes silent. Engagement reduces ignore count.
    """

    def __init__(self, config: Optional[Dict] = None):
        # Track response timestamps per channel
        self.response_times: Dict[str, list] = defaultdict(list)

        # Track consecutive ignores per channel
        self.ignored_count: Dict[str, int] = defaultdict(int)

        # Configuration with defaults
        config = config or {}
        self.short_window_minutes = config.get("short_window_minutes", 5)
        self.short_window_max = config.get("short_window_max", 20)
        self.long_window_minutes = config.get("long_window_minutes", 60)
        self.long_window_max = config.get("long_window_max", 200)
        self.ignore_threshold = config.get("ignore_threshold", 5)

    def can_respond(self, channel_id: str) -> Tuple[bool, Optional[str]]:
        """
        Check if bot can respond in channel.

        Returns: (can_respond, reason_if_blocked)
        Reasons: None, "rate_limit_short", "rate_limit_long", "ignored_threshold"
        """
        now = datetime.now()
        times = self.response_times[channel_id]

        # Clean up old responses outside long window
        cutoff = now - timedelta(minutes=self.long_window_minutes)
        times = [t for t in times if t > cutoff]
        self.response_times[channel_id] = times

        # Check short window
        short_cutoff = now - timedelta(minutes=self.short_window_minutes)
        short_window_responses = [t for t in times if t > short_cutoff]

        if len(short_window_responses) >= self.short_window_max:
            logger.debug(
                f"Channel {channel_id}: Rate limit (short) - "
                f"{len(short_window_responses)}/{self.short_window_max}"
            )
            return False, "rate_limit_short"

        # Check long window
        if len(times) >= self.long_window_max:
            logger.debug(
                f"Channel {channel_id}: Rate limit (long) - "
                f"{len(times)}/{self.long_window_max}"
            )
            return False, "rate_limit_long"

        # Check ignore threshold
        if self.ignored_count[channel_id] >= self.ignore_threshold:
            logger.debug(
                f"Channel {channel_id}: Silenced - "
                f"{self.ignored_count[channel_id]}/{self.ignore_threshold}"
            )
            return False, "ignored_threshold"

        return True, None

    def record_response(self, channel_id: str):
        """Record that bot sent a message"""
        self.response_times[channel_id].append(datetime.now())
        logger.debug(
            f"Channel {channel_id}: Response recorded "
            f"(total: {len(self.response_times[channel_id])})"
        )

    def record_ignored(self, channel_id: str):
        """Record that bot was ignored (no engagement within 30s)"""
        self.ignored_count[channel_id] += 1

        count = self.ignored_count[channel_id]
        logger.debug(
            f"Channel {channel_id}: Ignored count {count}/{self.ignore_threshold}"
        )

        if count >= self.ignore_threshold:
            logger.info(f"Channel {channel_id}: Silence threshold reached")

    def record_engagement(self, channel_id: str):
        """Record engagement (reaction/reply). Reduces ignore counter."""
        # Don't go negative
        self.ignored_count[channel_id] = max(0, self.ignored_count[channel_id] - 1)

        logger.debug(
            f"Channel {channel_id}: Engagement! "
            f"Ignore count now {self.ignored_count[channel_id]}"
        )

    def get_stats(self, channel_id: str) -> Dict:
        """Get current rate limit stats for debugging/monitoring"""
        now = datetime.now()
        times = self.response_times[channel_id]

        short_cutoff = now - timedelta(minutes=self.short_window_minutes)
        long_cutoff = now - timedelta(minutes=self.long_window_minutes)

        responses_5min = len([t for t in times if t > short_cutoff])
        responses_1hr = len([t for t in times if t > long_cutoff])
        ignored = self.ignored_count[channel_id]

        return {
            "responses_5min": responses_5min,
            "responses_1hr": responses_1hr,
            "ignored_count": ignored,
            "is_silenced": ignored >= self.ignore_threshold,
            "limits": {
                "short_window": f"{responses_5min}/{self.short_window_max}",
                "long_window": f"{responses_1hr}/{self.long_window_max}",
            },
        }

    def reset_channel(self, channel_id: str):
        """Reset all limits for channel (testing/manual intervention)"""
        self.response_times[channel_id].clear()
        self.ignored_count[channel_id] = 0
        logger.info(f"Channel {channel_id}: Rate limits reset")
