"""
Minimal test for engagement tracker
"""

import tempfile
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.engagement_tracker import EngagementTracker


def test_basic_tracking():
    """Test basic engagement tracking"""
    with tempfile.TemporaryDirectory() as tmpdir:
        stats_file = Path(tmpdir) / "engagement_stats.json"
        tracker = EngagementTracker(stats_file)

        # Record some proactive messages
        tracker.record_proactive_message("msg1", "channel1", topic="greeting")
        tracker.record_proactive_message("msg2", "channel1", topic="question")
        tracker.record_proactive_message("msg3", "channel2", topic="greeting")

        # Record engagement on some messages
        tracker.record_engagement("msg1", "channel1")
        tracker.record_engagement("msg3", "channel2")

        # Check stats
        assert tracker.get_overall_success_rate() == pytest.approx(2/3, 0.01)

        channel1_rate = tracker.get_channel_success_rate("channel1")
        assert channel1_rate == pytest.approx(0.5, 0.01)

        channel2_rate = tracker.get_channel_success_rate("channel2")
        assert channel2_rate == 1.0

        greeting_rate = tracker.get_topic_success_rate("greeting")
        assert greeting_rate == 1.0

        question_rate = tracker.get_topic_success_rate("question")
        assert question_rate == 0.0

        print("✓ Basic tracking test passed")
        print(f"  Overall success rate: {tracker.get_overall_success_rate():.2%}")
        print(f"  Channel1 rate: {channel1_rate:.2%}")
        print(f"  Channel2 rate: {channel2_rate:.2%}")


def test_stats_persistence():
    """Test that stats persist across instances"""
    with tempfile.TemporaryDirectory() as tmpdir:
        stats_file = Path(tmpdir) / "engagement_stats.json"

        # First instance
        tracker1 = EngagementTracker(stats_file)
        tracker1.record_proactive_message("msg1", "channel1")
        tracker1.record_engagement("msg1", "channel1")

        # Second instance (reload from file)
        tracker2 = EngagementTracker(stats_file)
        assert tracker2.get_overall_success_rate() == 1.0
        assert tracker2.stats["total_proactive"] == 1
        assert tracker2.stats["total_engaged"] == 1

        print("✓ Stats persistence test passed")


def test_hour_tracking():
    """Test hour-of-day tracking"""
    with tempfile.TemporaryDirectory() as tmpdir:
        stats_file = Path(tmpdir) / "engagement_stats.json"
        tracker = EngagementTracker(stats_file)

        # Record messages and check hour stats exist
        tracker.record_proactive_message("msg1", "channel1")

        # Should have hour stats for current hour
        from datetime import datetime
        current_hour = datetime.utcnow().hour
        hour_rate = tracker.get_hour_success_rate(current_hour)
        assert hour_rate == 0.0  # No engagement yet

        tracker.record_engagement("msg1", "channel1")
        hour_rate = tracker.get_hour_success_rate(current_hour)
        assert hour_rate == 1.0

        print("✓ Hour tracking test passed")


def test_stats_summary():
    """Test comprehensive stats summary"""
    with tempfile.TemporaryDirectory() as tmpdir:
        stats_file = Path(tmpdir) / "engagement_stats.json"
        tracker = EngagementTracker(stats_file)

        # Add some data
        for i in range(10):
            tracker.record_proactive_message(f"msg{i}", "channel1")
            if i % 2 == 0:  # 50% engagement
                tracker.record_engagement(f"msg{i}", "channel1")

        summary = tracker.get_stats_summary()
        assert summary["total_proactive"] == 10
        assert summary["total_engaged"] == 5
        assert summary["overall_success_rate"] == 0.5
        assert summary["channel_count"] == 1

        print("✓ Stats summary test passed")
        print(f"  Summary: {summary}")


if __name__ == "__main__":
    import pytest
    test_basic_tracking()
    test_stats_persistence()
    test_hour_tracking()
    test_stats_summary()
    print("\n✅ All engagement tracker tests passed!")
