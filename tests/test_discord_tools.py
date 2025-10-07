"""
Minimal tests for Discord tools and user cache
"""

import pytest
import tempfile
import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.message_memory import MessageMemory, StoredMessage
from core.user_cache import UserCache, CachedUser
from tools.discord_tools import DiscordToolExecutor, get_discord_tools


# Mock Discord user for testing
class MockUser:
    def __init__(self, user_id, name, display_name, is_bot=False):
        self.id = user_id
        self.name = name
        self.display_name = display_name
        self.bot = is_bot
        self.avatar = None
        self.discriminator = "0"


@pytest.mark.asyncio
async def test_fts5_search():
    """Test FTS5 full-text search on messages"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_messages.db"
        memory = MessageMemory(db_path)
        await memory.initialize()

        # Insert test messages directly
        test_messages = [
            ("1", "123", "server1", "user1", "Alice", "Hello world", datetime.utcnow().isoformat()),
            ("2", "123", "server1", "user2", "Bob", "Testing phase 4 features", datetime.utcnow().isoformat()),
            ("3", "123", "server1", "user1", "Alice", "FTS5 search is awesome", datetime.utcnow().isoformat()),
        ]

        for msg_id, chan_id, guild_id, author_id, author_name, content, timestamp in test_messages:
            await memory._db.execute(
                """
                INSERT INTO messages (
                    message_id, channel_id, guild_id,
                    author_id, author_name, content,
                    timestamp, is_bot, has_attachments, mentions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (msg_id, chan_id, guild_id, author_id, author_name, content, timestamp, False, False, "[]")
            )
        await memory._db.commit()

        # Test search
        results = await memory.search_messages("phase 4")
        assert len(results) == 1
        assert results[0].content == "Testing phase 4 features"

        results = await memory.search_messages("Alice")
        assert len(results) == 2

        results = await memory.search_messages("FTS5")
        assert len(results) == 1

        await memory.close()
        print("✓ FTS5 search test passed")


@pytest.mark.asyncio
async def test_user_cache():
    """Test user cache operations"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_users.db"
        cache = UserCache(db_path)
        await cache.initialize()

        # Create mock users
        user1 = MockUser(12345, "alice", "Alice Wonder")
        user2 = MockUser(67890, "bob", "Bob Builder", is_bot=True)

        # Update cache
        await cache.update_user(user1, increment_messages=True)
        await cache.update_user(user2, increment_messages=True)

        # Retrieve users
        cached_user1 = await cache.get_user("12345")
        assert cached_user1 is not None
        assert cached_user1.username == "alice"
        assert cached_user1.message_count == 1

        # Update same user again
        await cache.update_user(user1, increment_messages=True)
        cached_user1 = await cache.get_user("12345")
        assert cached_user1.message_count == 2

        # Search users
        results = await cache.search_users("alice")
        assert len(results) >= 1
        assert any(u.username == "alice" for u in results)

        # Get stats
        stats = await cache.get_stats()
        assert stats["total_users"] == 2
        assert stats["bot_count"] == 1
        assert stats["human_count"] == 1

        await cache.close()
        print("✓ User cache test passed")


@pytest.mark.asyncio
async def test_discord_tool_executor():
    """Test Discord tool executor"""
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
        user = MockUser(11111, "testuser", "Test User")
        await cache.update_user(user, increment_messages=True)

        await memory._db.execute(
            """
            INSERT INTO messages (
                message_id, channel_id, guild_id,
                author_id, author_name, content,
                timestamp, is_bot, has_attachments, mentions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("999", "chan1", "guild1", "11111", "Test User",
             "Discord tools are great", datetime.utcnow().isoformat(), False, False, "[]")
        )
        await memory._db.commit()

        # Test search_messages command
        result = await executor.execute({
            "command": "search_messages",
            "query": "Discord tools"
        })
        assert "Discord tools are great" in result

        # Test get_user_info command
        result = await executor.execute({
            "command": "get_user_info",
            "user_id": "11111"
        })
        assert "testuser" in result
        assert "Message Count: 1" in result

        # Test get_channel_info command
        result = await executor.execute({
            "command": "get_channel_info",
            "channel_id": "chan1"
        })
        assert "Total Messages: 1" in result

        await memory.close()
        await cache.close()
        print("✓ Discord tool executor test passed")


def test_get_discord_tools():
    """Test Discord tools definition"""
    tools = get_discord_tools()

    assert len(tools) == 1
    assert tools[0]["name"] == "discord_tools"
    assert "description" in tools[0]
    assert "input_schema" in tools[0]
    assert "type" not in tools[0]  # Custom tools should NOT have type field
    assert "search_messages" in str(tools[0])
    assert "get_user_info" in str(tools[0])
    assert "get_channel_info" in str(tools[0])

    print("✓ Discord tools definition test passed")


if __name__ == "__main__":
    # Run tests
    asyncio.run(test_fts5_search())
    asyncio.run(test_user_cache())
    asyncio.run(test_discord_tool_executor())
    test_get_discord_tools()
    print("\n✅ All Discord tools tests passed!")
