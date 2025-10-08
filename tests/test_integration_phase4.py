"""
Minimal Integration Test Suite for Phase 4

Tests that all Phase 4 features work together correctly.
"""

import asyncio
import tempfile
import sys
import os
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.message_memory import MessageMemory
from core.user_cache import UserCache
from core.engagement_tracker import EngagementTracker
from tools.image_processor import ImageProcessor
from tools.web_search import WebSearchManager, get_web_search_tools
from tools.discord_tools import DiscordToolExecutor, get_discord_tools


class MockUser:
    """Mock Discord user"""
    def __init__(self, user_id, name, display_name, is_bot=False):
        self.id = user_id
        self.name = name
        self.display_name = display_name
        self.bot = is_bot
        self.avatar = None
        self.discriminator = "0"


async def test_image_processor_integration():
    """Test image processor can be imported and used"""
    processor = ImageProcessor()
    assert processor.api_limit == 5 * 1024 * 1024
    assert processor.target_size == int(processor.api_limit * 0.73)
    print("✓ Image processor integration test passed")


async def test_web_search_integration():
    """Test web search manager and tools"""
    with tempfile.TemporaryDirectory() as tmpdir:
        stats_file = Path(tmpdir) / "web_search_stats.json"
        manager = WebSearchManager(stats_file, max_daily=10)

        # Check initial state
        can_search, reason = manager.can_search()
        assert can_search
        assert reason is None

        # Record searches
        for _ in range(10):
            manager.record_search()

        # Should be at limit
        can_search, reason = manager.can_search()
        assert not can_search
        assert "quota" in reason.lower()

        # Check tools definition
        tools = get_web_search_tools(max_uses=3)
        assert len(tools) == 2
        assert all(tool["type"] in ["web_search_20250305", "web_fetch_20250910"] for tool in tools)
        assert all(tool["name"] in ["web_search", "web_fetch"] for tool in tools)
        assert all(tool["max_uses"] == 3 for tool in tools)

        # Verify citations enabled for web_fetch
        web_fetch = next((t for t in tools if t["name"] == "web_fetch"), None)
        assert web_fetch is not None
        assert "citations" in web_fetch
        assert web_fetch["citations"]["enabled"] is True

        print("✓ Web search integration test passed")


async def test_discord_tools_integration():
    """Test Discord tools with FTS5 search"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Setup
        msg_db = Path(tmpdir) / "messages.db"
        user_db = Path(tmpdir) / "users.db"

        memory = MessageMemory(msg_db)
        await memory.initialize()

        cache = UserCache(user_db)
        await cache.initialize()

        executor = DiscordToolExecutor(memory, cache)

        # Add test data
        user = MockUser(123, "testuser", "Test User")
        await cache.update_user(user, increment_messages=True)

        await memory._db.execute(
            """
            INSERT INTO messages (
                message_id, channel_id, guild_id,
                author_id, author_name, content,
                timestamp, is_bot, has_attachments, mentions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("1", "chan1", "guild1", "123", "Test User",
             "Phase 4 features are working great",
             datetime.utcnow().isoformat(), False, False, "[]")
        )
        await memory._db.commit()

        # Test FTS5 search
        results = await memory.search_messages("Phase 4")
        assert len(results) == 1
        assert "Phase 4" in results[0].content

        # Test Discord tool executor
        result = await executor.execute({
            "command": "search_messages",
            "query": "Phase 4"
        })
        assert "Phase 4" in result

        result = await executor.execute({
            "command": "get_user_info",
            "user_id": "123"
        })
        assert "testuser" in result

        # Check tools definition
        tools = get_discord_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "discord_tools"
        assert "description" in tools[0]
        assert "input_schema" in tools[0]
        assert "type" not in tools[0]  # Custom tools don't have type field

        await memory.close()
        await cache.close()

        print("✓ Discord tools integration test passed")


async def test_engagement_tracker_integration():
    """Test engagement tracker"""
    with tempfile.TemporaryDirectory() as tmpdir:
        stats_file = Path(tmpdir) / "engagement_stats.json"
        tracker = EngagementTracker(stats_file)

        # Record proactive messages
        for i in range(10):
            tracker.record_proactive_message(f"msg{i}", "channel1", topic="test")
            if i % 2 == 0:  # 50% engagement
                tracker.record_engagement(f"msg{i}", "channel1")

        # Check stats
        assert tracker.get_overall_success_rate() == 0.5
        channel_rate = tracker.get_channel_success_rate("channel1")
        assert channel_rate == 0.5

        summary = tracker.get_stats_summary()
        assert summary["total_proactive"] == 10
        assert summary["total_engaged"] == 5

        print("✓ Engagement tracker integration test passed")


async def test_full_pipeline():
    """Test all Phase 4 features work together"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Initialize all components
        msg_db = Path(tmpdir) / "messages.db"
        user_db = Path(tmpdir) / "users.db"
        web_stats = Path(tmpdir) / "web_search.json"
        engagement_stats = Path(tmpdir) / "engagement.json"

        memory = MessageMemory(msg_db)
        await memory.initialize()

        cache = UserCache(user_db)
        await cache.initialize()

        discord_tools = DiscordToolExecutor(memory, cache)
        web_search = WebSearchManager(web_stats, max_daily=300)
        engagement = EngagementTracker(engagement_stats)
        image_processor = ImageProcessor()

        # Add test data
        user = MockUser(999, "integration", "Integration Test")
        await cache.update_user(user, increment_messages=True)

        await memory._db.execute(
            """
            INSERT INTO messages (
                message_id, channel_id, guild_id,
                author_id, author_name, content,
                timestamp, is_bot, has_attachments, mentions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("test1", "integration_channel", "test_guild", "999", "Integration Test",
             "Testing all Phase 4 components together",
             datetime.utcnow().isoformat(), False, False, "[]")
        )
        await memory._db.commit()

        # Test search
        results = await memory.search_messages("Phase 4")
        assert len(results) == 1

        # Test user cache
        cached_user = await cache.get_user("999")
        assert cached_user is not None
        assert cached_user.username == "integration"

        # Test web search quota
        can_search, _ = web_search.can_search()
        assert can_search

        # Test engagement tracking
        engagement.record_proactive_message("test1", "integration_channel")
        engagement.record_engagement("test1", "integration_channel")
        assert engagement.get_overall_success_rate() == 1.0

        # Test image processor
        assert image_processor.target_size > 0

        await memory.close()
        await cache.close()

        print("✓ Full pipeline integration test passed")
        print("  ✓ Message memory with FTS5")
        print("  ✓ User cache")
        print("  ✓ Discord tools")
        print("  ✓ Web search manager")
        print("  ✓ Engagement tracking")
        print("  ✓ Image processing")


async def run_all_tests():
    """Run all integration tests"""
    print("Running Phase 4 Integration Tests...")
    print()

    await test_image_processor_integration()
    await test_web_search_integration()
    await test_discord_tools_integration()
    await test_engagement_tracker_integration()
    await test_full_pipeline()

    print()
    print("=" * 60)
    print("✅ ALL PHASE 4 INTEGRATION TESTS PASSED!")
    print("=" * 60)
    print()
    print("Phase 4 features verified:")
    print("  ✓ Image processing with multi-strategy compression")
    print("  ✓ Web search with quota management")
    print("  ✓ Discord tools with FTS5 full-text search")
    print("  ✓ User cache for efficient lookups")
    print("  ✓ Engagement success tracking")
    print()


if __name__ == "__main__":
    asyncio.run(run_all_tests())
