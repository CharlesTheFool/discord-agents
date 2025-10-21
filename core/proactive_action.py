"""
Proactive Action - Data class for agentic behaviors

Represents autonomous actions: follow-ups, proactive engagement, maintenance.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ProactiveAction:
    """
    Represents a proactive action the bot should take.

    Used by AgenticEngine to schedule and execute autonomous behaviors.
    """

    type: str  # "followup" | "proactive" | "maintenance"
    priority: str  # "high" | "medium" | "low"
    server_id: str
    channel_id: str
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    message: Optional[str] = None
    context: Optional[str] = None
    delivery_method: str = "standalone"  # "standalone" | "woven" | "deferred"
    followup_id: Optional[str] = None
    followup_event: Optional[str] = None

    def __post_init__(self):
        """Validate action fields"""
        valid_types = ["followup", "proactive", "maintenance"]
        if self.type not in valid_types:
            raise ValueError(f"Invalid type: {self.type}. Must be {valid_types}")

        valid_priorities = ["high", "medium", "low"]
        if self.priority not in valid_priorities:
            raise ValueError(f"Invalid priority: {self.priority}. Must be {valid_priorities}")

        valid_delivery = ["standalone", "woven", "deferred"]
        if self.delivery_method not in valid_delivery:
            raise ValueError(f"Invalid delivery_method: {self.delivery_method}. Must be {valid_delivery}")

    def should_execute_now(self, channel_active: bool) -> bool:
        """
        Determine if action should execute now based on delivery method.

        standalone: Execute when channel is idle
        woven: Execute when channel is active (weave into conversation)
        deferred: Never execute immediately
        """
        if self.delivery_method == "standalone":
            return not channel_active
        elif self.delivery_method == "woven":
            return channel_active
        elif self.delivery_method == "deferred":
            return False

        return False
