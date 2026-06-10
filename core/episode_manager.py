"""
Episode Manager - Episodic Session Boundaries and Distillation (v0.6.0)

Boundaries are properties of the channel's message timeline (idle gaps, span
mass), computable from the message store at any time - never of the bot
process being up. One code path serves live triggers, startup catch-up, and
(future) retroactive runs. See REDESIGN.md section 2.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from core.internal_constants import (
    EPISODE_IDLE_GAP_HOURS,
    EPISODE_MASS_TOKEN_LIMIT,
    EPISODE_MIN_MESSAGES,
    EPISODE_BOOTSTRAP_DAYS,
    EPISODE_SEED_TAIL_MESSAGES,
    EPISODE_INDEX_SEED_TAIL,
    EPISODE_DISTILL_MODEL,
    EPISODE_DISTILL_MAX_TOKENS,
)

logger = logging.getLogger(__name__)


def segment_open_span(
    messages: list,
    now: datetime,
    idle_gap: timedelta,
    mass_token_limit: int,
    min_messages: int,
    force: bool = False,
) -> Tuple[List[list], list]:
    """
    Find episode boundaries in an open span of messages (chronological).

    A boundary is an idle gap >= idle_gap between consecutive messages, or
    accumulated estimated span mass (chars/4) >= mass_token_limit. The final
    run of messages stays open as the live tail unless it is itself stale
    (now - last message >= idle_gap) or force=True.

    Segments smaller than min_messages merge forward into the next segment
    (a tiny final segment still closes - the watermark must advance).

    Returns:
        (closed_segments, open_tail)
    """
    if not messages:
        return [], []

    # Pass 1: split on idle gaps and mass
    raw_segments: List[list] = []
    current: list = [messages[0]]
    current_mass = len(messages[0].content or "") // 4

    for prev, msg in zip(messages, messages[1:]):
        gap = msg.timestamp - prev.timestamp
        if gap >= idle_gap or current_mass >= mass_token_limit:
            raw_segments.append(current)
            current = []
            current_mass = 0
        current.append(msg)
        current_mass += len(msg.content or "") // 4

    # The final run: close it if stale or forced, else it is the open tail
    tail: list = []
    if force or (now - current[-1].timestamp) >= idle_gap:
        raw_segments.append(current)
    else:
        tail = current

    # Pass 2: merge tiny segments forward
    segments: List[list] = []
    carry: list = []
    for seg in raw_segments:
        seg = carry + seg
        carry = []
        if len(seg) < min_messages:
            carry = seg
        else:
            segments.append(seg)
    if carry:
        # tiny leftover: merge backward if possible, else close as-is
        if segments:
            segments[-1].extend(carry)
        else:
            segments.append(carry)

    return segments, tail
