"""
Rate Limiter - Preserved Algorithm

This is SimpleRateLimiter from the original bot implementation.
Ported exactly from slh.py lines 119-164.

DO NOT MODIFY: This algorithm is battle-tested and tuned for small servers.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict
import logging

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Two-window rate limiting with engagement-based adaptation.

    Windows:
    - Short window: 5 minutes, max 20 responses
    - Long window: 1 hour, max 200 responses

    Adaptation:
    - Track ignored messages (no reaction/reply within 30s)
    - After 5 consecutive ignores, bot goes silent
    - Engagement (reaction, reply) reduces ignore count

    This is tuned for small servers (<50 users).
    """

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize rate limiter.

        Args:
            config: Optional configuration dict with:
                - short_window_minutes (default: 5)
                - short_window_max (default: 20)
                - long_window_minutes (default: 60)
                - long_window_max (default: 200)
                - ignore_threshold (default: 5)
        """
        # Track response timestamps per channel
        self.response_times: Dict[str, list] = defaultdict(list)

        # Track consecutive ignores per channel
        self.ignored_count: Dict[str, int] = defaultdict(int)

        # Configuration (with defaults)
        config = config or {}
        self.short_window_minutes = config.get("short_window_minutes", 5)
        self.short_window_max = config.get("short_window_max", 20)

        self.long_window_minutes = config.get("long_window_minutes", 60)
        self.long_window_max = config.get("long_window_max", 200)

        self.ignore_threshold = config.get("ignore_threshold", 5)

    def can_respond(self, channel_id: str) -> Tuple[bool, Optional[str]]:
        """
        Check if bot can respond in channel.

        Args:
            channel_id: Discord channel ID

        Returns:
            (can_respond, reason_if_blocked)

        Reasons:
            - None: Can respond
            - "rate_limit_short": Hit 5-minute limit
            - "rate_limit_long": Hit 1-hour limit
            - "ignored_threshold": Too many consecutive ignores
        """
        now = datetime.now()
        times = self.response_times[channel_id]

        # Clean up old responses outside long window
        cutoff = now - timedelta(minutes=self.long_window_minutes)
        times = [t for t in times if t > cutoff]
        self.response_times[channel_id] = times

        # Check short window (5 minutes, max 20)
        short_cutoff = now - timedelta(minutes=self.short_window_minutes)
        short_window_responses = [t for t in times if t > short_cutoff]

        if len(short_window_responses) >= self.short_window_max:
            logger.debug(
                f"Channel {channel_id}: Rate limit (short window) - "
                f"{len(short_window_responses)}/{self.short_window_max}"
            )
            return False, "rate_limit_short"

        # Check long window (1 hour, max 200)
        if len(times) >= self.long_window_max:
            logger.debug(
                f"Channel {channel_id}: Rate limit (long window) - "
                f"{len(times)}/{self.long_window_max}"
            )
            return False, "rate_limit_long"

        # Check ignore threshold
        if self.ignored_count[channel_id] >= self.ignore_threshold:
            logger.debug(
                f"Channel {channel_id}: Silenced (ignored threshold) - "
                f"{self.ignored_count[channel_id]}/{self.ignore_threshold}"
            )
            return False, "ignored_threshold"

        return True, None

    def record_response(self, channel_id: str):
        """
        Record that bot sent a message.

        Args:
            channel_id: Discord channel ID
        """
        self.response_times[channel_id].append(datetime.now())
        logger.debug(
            f"Channel {channel_id}: Response recorded "
            f"(total in window: {len(self.response_times[channel_id])})"
        )

    def record_ignored(self, channel_id: str):
        """
        Record that bot was ignored (no engagement within 30s).
        Increments ignore counter.

        Args:
            channel_id: Discord channel ID
        """
        self.ignored_count[channel_id] += 1

        count = self.ignored_count[channel_id]
        logger.debug(
            f"Channel {channel_id}: Ignored count now {count}/{self.ignore_threshold}"
        )

        if count >= self.ignore_threshold:
            logger.info(
                f"Channel {channel_id}: Reached ignore threshold - bot will go silent"
            )

    def record_engagement(self, channel_id: str):
        """
        Record engagement (reaction, reply to bot message).
        Reduces ignore counter, rewards responsiveness.

        Args:
            channel_id: Discord channel ID
        """
        # Reduce ignore count, but don't go negative
        self.ignored_count[channel_id] = max(0, self.ignored_count[channel_id] - 1)

        count = self.ignored_count[channel_id]
        logger.debug(
            f"Channel {channel_id}: Engagement! Ignore count now {count}"
        )

    def get_stats(self, channel_id: str) -> Dict:
        """
        Get current rate limit stats for channel.
        Useful for debugging and monitoring.

        Returns:
            {
                "responses_5min": int,
                "responses_1hr": int,
                "ignored_count": int,
                "is_silenced": bool,
                "limits": {
                    "short_window": str,
                    "long_window": str
                }
            }
        """
        now = datetime.now()
        times = self.response_times[channel_id]

        # Count responses in windows
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
        """
        Reset all limits for channel.
        Useful for testing or manual intervention.

        Args:
            channel_id: Discord channel ID
        """
        self.response_times[channel_id].clear()
        self.ignored_count[channel_id] = 0
        logger.info(f"Channel {channel_id}: Rate limits reset")
