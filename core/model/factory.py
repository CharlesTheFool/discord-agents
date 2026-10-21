"""Build the model driver a bot's config asks for."""

import os

from core.internal_constants import MODEL_CALL_SLOTS
from core.model.catalog import spec_for
from core.model.driver import ModelDriver


def _adapter(provider, max_concurrent: int):
    api_key = os.getenv(provider.api_key_env)
    if not api_key:
        # Never let an SDK quietly fall back to some other key variable
        raise RuntimeError(f"{provider.api_key_env} is not set - the model API needs it")
    if provider.wire_api == "anthropic_messages":
        from core.model.adapters.anthropic_messages import AnthropicMessagesDriver
        return AnthropicMessagesDriver(api_key=api_key, base_url=provider.base_url,
                                       max_concurrent=max_concurrent)
    if provider.wire_api == "openai_responses":
        from core.model.adapters.openai_responses import OpenAIResponsesDriver
        return OpenAIResponsesDriver(api_key=api_key, base_url=provider.base_url,
                                     max_concurrent=max_concurrent)
    raise ValueError(f"no driver for wire_api '{provider.wire_api}'")


def build_driver(api_config, max_concurrent: int = MODEL_CALL_SLOTS) -> ModelDriver:
    """A driver for every model the config names: the resident's, and the
    workers' with their own pool (max_concurrent calls in flight). A bot whose
    every role is on Anthropic gets the bare Anthropic driver, exactly as
    before 0.15.1; otherwise a RoutingDriver over one adapter per wire API
    (each with its own pool: rate limits are per provider). Config
    validation has already refused what this release doesn't ship."""
    wires = {spec_for(model).wire_api for model in api_config.role_models().values()}
    anthropic = _adapter(api_config.provider_for("anthropic_messages"), max_concurrent)
    if wires <= {"anthropic_messages"}:
        return anthropic
    from core.model.routing import RoutingDriver
    adapters = {"anthropic_messages": anthropic}
    for wire_api in sorted(wires - {"anthropic_messages"}):
        adapters[wire_api] = _adapter(api_config.provider_for(wire_api), max_concurrent)
    return RoutingDriver(adapters)
