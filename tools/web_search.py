"""
Web Search Integration

Anthropic's web_search and web_fetch tools are built into the Claude API.
A per-request max_uses cap is the cost guard (the old client-side daily
quota manager was never wired into any engine and has been removed).
"""

from core.internal_constants import WEB_SEARCH_MAX_USES


def get_web_search_tools(citations_enabled: bool = True) -> list:
    """
    Generate web search tool definitions for Claude API.

    These are Anthropic's built-in tools, not custom implementations.

    Args:
        citations_enabled: Enable citations for web_fetch (required for end-user apps)
    """
    return [
        {
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": WEB_SEARCH_MAX_USES,
        },
        {
            "type": "web_fetch_20260209",
            "name": "web_fetch",
            "citations": {"enabled": citations_enabled},
            "max_uses": WEB_SEARCH_MAX_USES,
        }
    ]
