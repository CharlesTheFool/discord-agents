"""
Engagement Success Tracking - Analytics for Proactive Messages

Tracks success rates of proactive and periodic messages to help inform
the adaptive learning system about what works.
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from collections import defaultdict

logger = logging.getLogger(__name__)


class EngagementTracker:
    """
    Tracks engagement metrics for proactive and periodic messages.

    Stores:
    - Success rates by channel
    - Success rates by time of day
    - Success rates by topic (manual tagging)
    - Overall success trends
    """

    def __init__(self, stats_file: Path):
        """
        Initialize engagement tracker.

        Args:
            stats_file: Path to JSON file for storing stats
        """
        self.stats_file = stats_file
        self.stats_file.parent.mkdir(parents=True, exist_ok=True)

        # Load or initialize stats
        self.stats = self._load_stats()
        logger.info(f"EngagementTracker initialized: {stats_file}")

    def _load_stats(self) -> Dict:
        """Load stats from file or create new"""
        if self.stats_file.exists():
            try:
                with open(self.stats_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load engagement stats: {e}")

        # Initialize new stats
        return {
            "total_proactive": 0,
            "total_engaged": 0,
            "by_channel": {},  # channel_id -> {sent, engaged}
            "by_hour": {str(h): {"sent": 0, "engaged": 0} for h in range(24)},
            "by_topic": {},  # topic -> {sent, engaged}
            "recent_messages": [],  # Last 100 messages for trend analysis
            "last_updated": datetime.utcnow().isoformat()
        }

    def _save_stats(self):
        """Save stats to file"""
        try:
            self.stats["last_updated"] = datetime.utcnow().isoformat()
            with open(self.stats_file, 'w') as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save engagement stats: {e}")

    def record_proactive_message(
        self,
        message_id: str,
        channel_id: str,
        topic: Optional[str] = None
    ):
        """
        Record that a proactive message was sent.

        Args:
            message_id: Discord message ID
            channel_id: Discord channel ID
            topic: Optional topic tag for categorization
        """
        # Update totals
        self.stats["total_proactive"] += 1

        # Update by channel
        if channel_id not in self.stats["by_channel"]:
            self.stats["by_channel"][channel_id] = {"sent": 0, "engaged": 0}
        self.stats["by_channel"][channel_id]["sent"] += 1

        # Update by hour
        hour = str(datetime.utcnow().hour)
        self.stats["by_hour"][hour]["sent"] += 1

        # Update by topic if provided
        if topic:
            if topic not in self.stats["by_topic"]:
                self.stats["by_topic"][topic] = {"sent": 0, "engaged": 0}
            self.stats["by_topic"][topic]["sent"] += 1

        # Add to recent messages
        self.stats["recent_messages"].append({
            "message_id": message_id,
            "channel_id": channel_id,
            "topic": topic,
            "timestamp": datetime.utcnow().isoformat(),
            "engaged": False
        })

        # Keep only last 100
        if len(self.stats["recent_messages"]) > 100:
            self.stats["recent_messages"] = self.stats["recent_messages"][-100:]

        self._save_stats()
        logger.debug(f"Recorded proactive message {message_id} in {channel_id}")

    def record_engagement(
        self,
        message_id: str,
        channel_id: str,
        engagement_type: str = "reply"
    ):
        """
        Record that a message received engagement.

        Args:
            message_id: Discord message ID
            channel_id: Discord channel ID
            engagement_type: Type of engagement (reply, reaction, etc.)
        """
        # Update totals
        self.stats["total_engaged"] += 1

        # Update by channel
        if channel_id in self.stats["by_channel"]:
            self.stats["by_channel"][channel_id]["engaged"] += 1

        # Find message in recent_messages to update
        for msg in self.stats["recent_messages"]:
            if msg["message_id"] == message_id:
                msg["engaged"] = True

                # Update by hour
                timestamp = datetime.fromisoformat(msg["timestamp"])
                hour = str(timestamp.hour)
                self.stats["by_hour"][hour]["engaged"] += 1

                # Update by topic
                if msg.get("topic"):
                    topic = msg["topic"]
                    if topic in self.stats["by_topic"]:
                        self.stats["by_topic"][topic]["engaged"] += 1

                break

        self._save_stats()
        logger.debug(f"Recorded engagement for message {message_id}: {engagement_type}")

    def get_channel_success_rate(self, channel_id: str) -> Optional[float]:
        """
        Get success rate for a specific channel.

        Args:
            channel_id: Discord channel ID

        Returns:
            Success rate (0.0 to 1.0) or None if no data
        """
        if channel_id not in self.stats["by_channel"]:
            return None

        sent = self.stats["by_channel"][channel_id]["sent"]
        engaged = self.stats["by_channel"][channel_id]["engaged"]

        if sent == 0:
            return None

        return engaged / sent

    def get_hour_success_rate(self, hour: int) -> float:
        """
        Get success rate for a specific hour of day.

        Args:
            hour: Hour (0-23)

        Returns:
            Success rate (0.0 to 1.0)
        """
        hour_str = str(hour)
        sent = self.stats["by_hour"][hour_str]["sent"]
        engaged = self.stats["by_hour"][hour_str]["engaged"]

        if sent == 0:
            return 0.0

        return engaged / sent

    def get_topic_success_rate(self, topic: str) -> Optional[float]:
        """
        Get success rate for a specific topic.

        Args:
            topic: Topic tag

        Returns:
            Success rate (0.0 to 1.0) or None if no data
        """
        if topic not in self.stats["by_topic"]:
            return None

        sent = self.stats["by_topic"][topic]["sent"]
        engaged = self.stats["by_topic"][topic]["engaged"]

        if sent == 0:
            return None

        return engaged / sent

    def get_overall_success_rate(self) -> float:
        """
        Get overall success rate across all proactive messages.

        Returns:
            Success rate (0.0 to 1.0)
        """
        if self.stats["total_proactive"] == 0:
            return 0.0

        return self.stats["total_engaged"] / self.stats["total_proactive"]

    def get_recent_trend(self, days: int = 7) -> Dict:
        """
        Get trend analysis for recent messages.

        Args:
            days: Number of days to analyze

        Returns:
            Dictionary with trend statistics
        """
        cutoff = datetime.utcnow() - timedelta(days=days)

        recent = [
            msg for msg in self.stats["recent_messages"]
            if datetime.fromisoformat(msg["timestamp"]) > cutoff
        ]

        if not recent:
            return {
                "messages": 0,
                "engaged": 0,
                "success_rate": 0.0,
                "trend": "insufficient_data"
            }

        total = len(recent)
        engaged = sum(1 for msg in recent if msg["engaged"])
        success_rate = engaged / total

        # Compare to overall rate to determine trend
        overall_rate = self.get_overall_success_rate()

        if success_rate > overall_rate * 1.1:
            trend = "improving"
        elif success_rate < overall_rate * 0.9:
            trend = "declining"
        else:
            trend = "stable"

        return {
            "messages": total,
            "engaged": engaged,
            "success_rate": success_rate,
            "trend": trend
        }

    def get_best_hours(self, top_n: int = 5) -> List[tuple[int, float]]:
        """
        Get the most successful hours of day.

        Args:
            top_n: Number of hours to return

        Returns:
            List of (hour, success_rate) tuples, sorted by success rate
        """
        hours_with_rates = []

        for hour in range(24):
            rate = self.get_hour_success_rate(hour)
            sent = self.stats["by_hour"][str(hour)]["sent"]

            # Only include hours with at least 5 messages
            if sent >= 5:
                hours_with_rates.append((hour, rate))

        # Sort by success rate descending
        hours_with_rates.sort(key=lambda x: x[1], reverse=True)

        return hours_with_rates[:top_n]

    def get_stats_summary(self) -> Dict:
        """
        Get comprehensive stats summary.

        Returns:
            Dictionary with all stats
        """
        return {
            "total_proactive": self.stats["total_proactive"],
            "total_engaged": self.stats["total_engaged"],
            "overall_success_rate": self.get_overall_success_rate(),
            "channel_count": len(self.stats["by_channel"]),
            "topic_count": len(self.stats["by_topic"]),
            "recent_trend": self.get_recent_trend(),
            "best_hours": self.get_best_hours(),
            "last_updated": self.stats["last_updated"]
        }
