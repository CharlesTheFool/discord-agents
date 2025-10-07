"""
Proactive Action - Data class for agentic behaviors

Represents actions the bot can take proactively:
- Follow-ups
- Proactive engagement
- Memory maintenance
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
    followup_id: Optional[str] = None  # ID of followup (for completion tracking)
    followup_event: Optional[str] = None  # Event description (for message generation)

    def __post_init__(self):
        """Validate action fields"""
        valid_types = ["followup", "proactive", "maintenance"]
        if self.type not in valid_types:
            raise ValueError(f"Invalid action type: {self.type}. Must be one of {valid_types}")

        valid_priorities = ["high", "medium", "low"]
        if self.priority not in valid_priorities:
            raise ValueError(f"Invalid priority: {self.priority}. Must be one of {valid_priorities}")

        valid_delivery_methods = ["standalone", "woven", "deferred"]
        if self.delivery_method not in valid_delivery_methods:
            raise ValueError(
                f"Invalid delivery_method: {self.delivery_method}. Must be one of {valid_delivery_methods}"
            )

    def should_execute_now(self, channel_active: bool) -> bool:
        """
        Determine if action should execute now based on delivery method.

        Args:
            channel_active: Whether channel has recent activity

        Returns:
            True if should execute immediately
        """
        if self.delivery_method == "standalone":
            # Execute if channel is idle
            return not channel_active

        elif self.delivery_method == "woven":
            # Execute if channel is active (weave into conversation)
            return channel_active

        elif self.delivery_method == "deferred":
            # Never execute immediately, wait for better opportunity
            return False

        return False
