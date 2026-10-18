"""Build the model driver a bot's config asks for."""

import os

from core.model.driver import ModelDriver


def build_driver(api_config) -> ModelDriver:
    """One driver per bot process, from the api.provider block (default:
    Anthropic with ANTHROPIC_API_KEY). Config validation has already
    rejected wire APIs this release doesn't ship."""
    provider = api_config.provider
    wire_api = provider.wire_api if provider else "anthropic_messages"
    key_env = provider.api_key_env if provider else "ANTHROPIC_API_KEY"
    base_url = provider.base_url if provider else None
    if wire_api == "anthropic_messages":
        from core.model.adapters.anthropic_messages import AnthropicMessagesDriver
        return AnthropicMessagesDriver(api_key=os.getenv(key_env), base_url=base_url)
    raise ValueError(f"no driver for wire_api '{wire_api}'")
