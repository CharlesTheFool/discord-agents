"""The routing driver (0.15.1): one adapter per wire API, each request handed,
untouched, to the adapter its model's catalog spec names. Only bots with a
role on another provider get one; an all-Claude bot keeps the bare
Anthropic driver."""

from typing import Dict, List, Tuple

from core.model.catalog import ModelSpec, spec_for
from core.model.driver import ModelRequest, ModelResponse


class RoutingDriver:
    def __init__(self, adapters: Dict[str, object]):
        self.adapters = adapters

    def spec_for(self, model: str) -> ModelSpec:
        return spec_for(model)

    def _for(self, model: str):
        return self.adapters[spec_for(model).wire_api]

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return await self._for(request.model).generate(request)

    async def batch(self, items: List[Tuple[str, ModelRequest]]) -> dict:
        wires = {spec_for(request.model).wire_api for _, request in items}
        if len(wires) > 1:
            raise ValueError(f"one batch, one wire API: got {sorted(wires)}")
        return await self._for(items[0][1].model).batch(items)

    @property
    def client(self):
        """The Anthropic client, for the managers outside the driver (Files API)."""
        return self.adapters["anthropic_messages"].client

    @property
    def resolve_attachment(self):
        return self.adapters["anthropic_messages"].resolve_attachment

    @resolve_attachment.setter
    def resolve_attachment(self, resolver) -> None:
        for adapter in self.adapters.values():
            adapter.resolve_attachment = resolver
