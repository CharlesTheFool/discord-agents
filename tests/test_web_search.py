"""
Minimal test for web search quota management
"""

import pytest
import tempfile
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tools.web_search import WebSearchManager, get_web_search_tools


def test_quota_tracking():
    """Test web search quota tracking"""
    # Create temporary stats file
    with tempfile.TemporaryDirectory() as tmpdir:
        stats_file = Path(tmpdir) / "web_search_stats.json"
        manager = WebSearchManager(stats_file, max_daily=5)  # Small quota for testing

        # Should be able to search initially
        can_search, reason = manager.can_search()
        assert can_search
        assert reason is None

        # Record 5 searches
        for i in range(5):
            manager.record_search()

        # Check stats
        stats = manager.get_stats()
        assert stats["searches_today"] == 5
        assert stats["searches_remaining"] == 0

        # Should be blocked now
        can_search, reason = manager.can_search()
        assert not can_search
        assert "quota exceeded" in reason.lower()

        print("✓ Quota tracking test passed")
        print(f"  Searches today: {stats['searches_today']}")
        print(f"  Searches remaining: {stats['searches_remaining']}")


def test_get_web_search_tools():
    """Test that web search tools are correctly defined"""
    tools = get_web_search_tools(max_uses=3)

    assert len(tools) == 2

    # Check web_search tool
    web_search = next((t for t in tools if t["name"] == "web_search"), None)
    assert web_search is not None
    assert web_search["type"] == "web_search_20250305"
    assert web_search["max_uses"] == 3

    # Check web_fetch tool
    web_fetch = next((t for t in tools if t["name"] == "web_fetch"), None)
    assert web_fetch is not None
    assert web_fetch["type"] == "web_fetch_20250910"
    assert web_fetch["max_uses"] == 3

    print("✓ Web search tools test passed")
    print(f"  web_search: {web_search['type']}")
    print(f"  web_fetch: {web_fetch['type']}")


if __name__ == "__main__":
    test_quota_tracking()
    test_get_web_search_tools()
    print("\n✅ All web search tests passed!")
