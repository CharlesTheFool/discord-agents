"""
Server induction (v0.8.0): reconsolidation at t = 0.

A deliberate operator action that distills a server's stored backlog into
channel digests, lean global user profiles, and server culture - explicitly
marked as observations from reading the backlog, not lived memory. Reads the
messages DB only (never Discord); requires backfill to have run; refuses to
run while the bot is live (watermark races).
"""

import logging
from typing import Optional

from core.internal_constants import (
    INDUCTION_CHUNK_TOKENS,
    INDUCTION_OUTPUT_RATIO,
    MODEL_BATCH_PRICES,
)

logger = logging.getLogger(__name__)

ARCHAEOLOGY_HEADER = "*(from reading the backlog before I was here - observations, not lived memory)*"


def estimate_cost(model: str, input_tokens: int) -> Optional[float]:
    """Batch-rate dollar estimate; None for unpriced models."""
    for marker, (in_price, out_price) in MODEL_BATCH_PRICES.items():
        if marker in model:
            output_tokens = input_tokens * INDUCTION_OUTPUT_RATIO
            return (input_tokens * in_price + output_tokens * out_price) / 1_000_000
    return None
