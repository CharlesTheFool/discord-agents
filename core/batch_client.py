"""
Batches API wrapper (v0.7.0).

Latency-tolerant distillation at 50% cost, on rate limits separate from live
traffic. Submit -> poll processing_status to "ended" -> map results by
custom_id. The caller owns retries and stamps; a timeout raises and leaves
no side effects.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List

from core.internal_constants import (
    CONSOLIDATION_BATCH_POLL_SECONDS,
    CONSOLIDATION_BATCH_TIMEOUT_HOURS,
)

logger = logging.getLogger(__name__)


class BatchTimeoutError(Exception):
    """Batch did not reach 'ended' within the timeout."""


class BatchClient:
    def __init__(self, anthropic_client,
                 poll_interval_seconds: float = CONSOLIDATION_BATCH_POLL_SECONDS,
                 timeout_hours: float = CONSOLIDATION_BATCH_TIMEOUT_HOURS):
        self.anthropic = anthropic_client
        self.poll_interval = poll_interval_seconds
        self.timeout_seconds = timeout_hours * 3600

    async def run(self, requests: List[Dict]) -> Dict[str, Any]:
        """Submit one batch and return {custom_id: result}.

        Each result has .type ("succeeded" -> .message, "errored" -> .error).
        """
        batch = await self.anthropic.beta.messages.batches.create(requests=requests)
        logger.info(f"Batch {batch.id} submitted ({len(requests)} requests)")

        deadline = time.monotonic() + self.timeout_seconds
        # "canceling" resolves to "ended"; any unknown future status is bounded
        # by the timeout rather than treated as terminal
        while batch.processing_status != "ended":
            if time.monotonic() > deadline:
                raise BatchTimeoutError(
                    f"Batch {batch.id} still {batch.processing_status} after "
                    f"{self.timeout_seconds / 3600:.0f}h"
                )
            await asyncio.sleep(self.poll_interval)
            batch = await self.anthropic.beta.messages.batches.retrieve(batch.id)

        results: Dict[str, Any] = {}
        decoder = await self.anthropic.beta.messages.batches.results(batch.id)
        async for item in decoder:
            results[item.custom_id] = item.result
        succeeded = sum(1 for r in results.values() if r.type == "succeeded")
        logger.info(f"Batch {batch.id} ended: {succeeded}/{len(results)} succeeded")
        return results
