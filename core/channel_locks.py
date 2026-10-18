"""
Per-channel turn locks (0.13.1).

Turns in one channel run one at a time; turns in different channels run
side by side. Whatever writes a channel's conversation state - a reply, a
proactive message, a relay note, an episode reseed - takes that channel's
lock for the whole span from reading the state to saving it, so nothing
lands between a turn's persisted tool pairs and its reply.

How many model calls run at once bot-wide is the driver's business (its
own slot limit), not this module's.
"""

import asyncio
from typing import Dict


class ChannelLocks:
    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}

    def lock(self, channel_id) -> asyncio.Lock:
        """The channel's lock, created on first use."""
        return self._locks.setdefault(str(channel_id), asyncio.Lock())

    def busy(self, channel_id) -> bool:
        """True while a turn holds the channel."""
        lock = self._locks.get(str(channel_id))
        return lock is not None and lock.locked()
