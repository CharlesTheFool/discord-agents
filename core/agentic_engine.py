"""
Agentic Engine - Autonomous Behaviors

Handles proactive bot behaviors:
- Follow-up system (track and check in on user events)
- Proactive engagement (initiate conversations naturally)
- Adaptive learning (learn what works per channel)
- Memory maintenance (keep profiles current)
"""

import asyncio
import logging
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import BotConfig
    from .message_memory import MessageMemory
    from .memory_manager import MemoryManager
    from anthropic import AsyncAnthropic

from .proactive_action import ProactiveAction

logger = logging.getLogger(__name__)


class AgenticEngine:
    """
    Autonomous behavior engine.

    Runs background tasks:
    - Hourly follow-up checks
    - Proactive engagement opportunities
    - Memory maintenance
    - Engagement analytics
    """

    def __init__(
        self,
        config: "BotConfig",
        memory_manager: "MemoryManager",
        message_memory: "MessageMemory",
        anthropic_client: "AsyncAnthropic",
    ):
        """
        Initialize agentic engine.

        Args:
            config: Bot configuration
            memory_manager: Memory tool manager
            message_memory: Message storage
            anthropic_client: Anthropic API client
        """
        self.config = config
        self.memory = memory_manager
        self.message_memory = message_memory
        self.anthropic = anthropic_client
        self.discord_client = None  # Set after Discord client initialization

        # Track background task
        self._task = None
        self._running = False

        # Rate limit tracking (resets daily)
        self._proactive_counts_global = 0
        self._proactive_counts_per_channel = {}  # {channel_id: count}
        self._rate_limit_reset_date = datetime.now().date()

        logger.info(f"AgenticEngine initialized for bot '{config.bot_id}'")

    def set_discord_client(self, discord_client):
        """
        Set Discord client reference for sending messages.

        Args:
            discord_client: DiscordClient instance
        """
        self.discord_client = discord_client
        logger.info("Discord client reference set on AgenticEngine")

    async def agentic_loop(self):
        """
        Main agentic loop - runs hourly.

        Checks for:
        - Due follow-ups
        - Proactive engagement opportunities
        - Memory maintenance needs
        """
        self._running = True
        logger.info("Agentic loop started")

        while self._running:
            try:
                logger.debug("Agentic loop iteration starting...")

                # Get all servers bot is in
                # (We'll get this from message_memory - channels we've seen)
                servers = await self._get_active_servers()

                actions = []

                # Check follow-ups for each server
                if self.config.agentic.followups.enabled:
                    for server_id in servers:
                        server_actions = await self.check_followups(server_id)
                        actions.extend(server_actions)

                # Check proactive engagement opportunities
                if self.config.agentic.proactive.enabled:
                    engagement_actions = await self.check_engagement_opportunities()
                    actions.extend(engagement_actions)

                # Execute actions
                for action in actions:
                    try:
                        await self._execute_action(action)
                    except Exception as e:
                        logger.error(f"Error executing action {action.type}: {e}", exc_info=True)

                # Memory maintenance (daily)
                current_hour = datetime.now().hour
                if current_hour == 3:  # 3am maintenance
                    await self.maintain_memories()

                logger.debug("Agentic loop iteration complete")

            except Exception as e:
                logger.error(f"Error in agentic loop: {e}", exc_info=True)

            # Wait for next interval
            interval_seconds = self.config.agentic.check_interval_hours * 3600
            await asyncio.sleep(interval_seconds)

    # ========== FOLLOW-UP SYSTEM ==========

    async def check_followups(self, server_id: str) -> List[ProactiveAction]:
        """
        Check for due follow-ups in server.

        Args:
            server_id: Discord server/guild ID

        Returns:
            List of follow-up actions to execute
        """
        logger.debug(f"Checking follow-ups for server {server_id}")

        followups_data = await self.memory.get_followups(server_id)
        if not followups_data:
            return []

        actions = []
        now = datetime.now()

        for followup in followups_data.get("pending", []):
            # Check if due
            follow_up_after = datetime.fromisoformat(followup["follow_up_after"])
            if now < follow_up_after:
                continue  # Not due yet

            # Check priority threshold
            priority_levels = {"low": 0, "medium": 1, "high": 2}
            threshold_level = priority_levels.get(self.config.agentic.followups.priority_threshold, 1)
            followup_level = priority_levels.get(followup["priority"], 1)

            if followup_level < threshold_level:
                continue  # Priority too low

            # Check if user active recently
            user_active = await self.is_user_active_recently(followup["user_id"], hours=24)
            if not user_active:
                logger.debug(f"User {followup['user_id']} not active recently, deferring follow-up")
                continue

            # Create action
            action = ProactiveAction(
                type="followup",
                priority=followup["priority"],
                server_id=server_id,
                channel_id=followup["channel_id"],
                user_id=followup["user_id"],
                user_name=followup["user_name"],
                message=f"Hey {followup['user_name']}, how did {followup['event']} go?",
                context=followup["context"],
                delivery_method=await self.decide_followup_delivery(followup),
                followup_id=followup.get("id"),  # Track ID for completion
            )
            actions.append(action)

        logger.info(f"Found {len(actions)} due follow-ups for server {server_id}")
        return actions

    async def decide_followup_delivery(self, followup: dict) -> str:
        """
        Decide how to deliver follow-up.

        Args:
            followup: Follow-up dict

        Returns:
            Delivery method: "standalone" | "woven" | "deferred"
        """
        channel_id = followup["channel_id"]

        # Get channel idle time
        idle_time = await self.get_channel_idle_time(channel_id)

        # If channel idle, standalone is fine
        if idle_time > 10:  # 10 minutes idle
            return "standalone"

        # If high priority, send now even if active
        if followup["priority"] == "high":
            return "standalone"

        # Check if user is in current conversation
        recent_messages = await self.message_memory.get_recent(channel_id, limit=5)
        user_active_in_channel = any(
            msg.author_id == followup["user_id"] for msg in recent_messages
        )

        if user_active_in_channel:
            return "woven"  # Weave into conversation

        # Defer to next natural opportunity
        return "deferred"

    # ========== PROACTIVE ENGAGEMENT ==========

    async def check_engagement_opportunities(self) -> List[ProactiveAction]:
        """
        Check for proactive engagement opportunities.

        Returns:
            List of proactive engagement actions
        """
        logger.debug("Checking for proactive engagement opportunities")

        # Get engagement stats for allowed channels
        actions = []

        for channel_id in self.config.agentic.proactive.allowed_channels:
            # Get channel info
            server_id = await self._get_server_for_channel(channel_id)
            if not server_id:
                continue

            # Check if should engage
            should_engage = await self.should_engage_proactively(server_id, channel_id)
            if not should_engage:
                continue

            # Check rate limits
            if not await self._check_proactive_rate_limits(server_id, channel_id):
                logger.debug(f"Proactive rate limit reached for channel {channel_id}")
                continue

            # Create proactive action
            action = ProactiveAction(
                type="proactive",
                priority="low",
                server_id=server_id,
                channel_id=channel_id,
                message=None,  # Will be generated by Claude
                context="Proactive engagement opportunity",
                delivery_method="standalone",
            )
            actions.append(action)

        logger.info(f"Found {len(actions)} proactive engagement opportunities")
        return actions

    async def should_engage_proactively(self, server_id: str, channel_id: str) -> bool:
        """
        Decide if bot should engage proactively in channel.

        Args:
            server_id: Discord server ID
            channel_id: Discord channel ID

        Returns:
            True if should engage
        """
        # Get channel idle time
        idle_time = await self.get_channel_idle_time(channel_id)

        # Check idle time bounds
        if idle_time < self.config.agentic.proactive.min_idle_hours:
            return False  # Too active
        if idle_time > self.config.agentic.proactive.max_idle_hours:
            return False  # Too dead

        # Check quiet hours
        current_hour = datetime.now().hour
        if current_hour in self.config.agentic.proactive.quiet_hours:
            return False

        # Check engagement success rate
        stats = await self.get_engagement_stats(server_id, channel_id)
        if stats["success_rate"] < self.config.agentic.proactive.engagement_threshold:
            logger.debug(f"Channel {channel_id} success rate too low: {stats['success_rate']:.1%}")
            return False

        return True

    async def get_engagement_stats(self, server_id: str, channel_id: str) -> dict:
        """
        Get engagement statistics for channel.

        Args:
            server_id: Discord server ID
            channel_id: Discord channel ID

        Returns:
            Stats dict with success_rate, total_attempts, successful_attempts
        """
        return await self.memory.get_engagement_stats(server_id, channel_id)

    # ========== MEMORY MAINTENANCE ==========

    async def maintain_memories(self):
        """
        Perform memory maintenance tasks.

        - Cleanup old follow-ups
        - Archive completed items
        - Update engagement statistics
        """
        logger.info("Starting memory maintenance...")

        servers = await self._get_active_servers()

        for server_id in servers:
            try:
                await self.cleanup_old_followups(server_id)
            except Exception as e:
                logger.error(f"Error cleaning up follow-ups for server {server_id}: {e}")

        logger.info("Memory maintenance complete")

    async def cleanup_old_followups(self, server_id: str):
        """
        Remove old follow-ups from server and archive completed items.

        Args:
            server_id: Discord server ID
        """
        max_age_days = self.config.agentic.followups.max_age_days

        followups_data = await self.memory.get_followups(server_id)
        if not followups_data:
            return

        now = datetime.now()
        changes_made = False

        # Filter out stale pending items
        pending = followups_data.get("pending", [])
        filtered_pending = []

        for followup in pending:
            created = datetime.fromisoformat(followup["mentioned_date"])
            age_days = (now - created).days

            if age_days < max_age_days:
                filtered_pending.append(followup)
            else:
                logger.debug(f"Removing stale pending follow-up: {followup['id']} (age: {age_days} days)")
                changes_made = True

        # Archive old completed items (remove completed items older than max_age_days)
        completed = followups_data.get("completed", [])
        filtered_completed = []

        for followup in completed:
            completed_date = datetime.fromisoformat(followup.get("completed_date", followup["mentioned_date"]))
            age_days = (now - completed_date).days

            if age_days < max_age_days:
                filtered_completed.append(followup)
            else:
                logger.debug(f"Archiving old completed follow-up: {followup['id']} (age: {age_days} days)")
                changes_made = True

        # Write back if changes were made
        if changes_made:
            followups_data["pending"] = filtered_pending
            followups_data["completed"] = filtered_completed
            await self.memory.write_followups(server_id, followups_data)
            logger.info(f"Cleaned up {len(pending) - len(filtered_pending)} pending and archived {len(completed) - len(filtered_completed)} completed follow-ups for server {server_id}")

    # ========== UTILITY METHODS ==========

    async def is_user_active_recently(self, user_id: str, hours: int = 24) -> bool:
        """
        Check if user has been active recently.

        Args:
            user_id: Discord user ID
            hours: Lookback window in hours

        Returns:
            True if user was active
        """
        return await self.message_memory.check_user_activity(user_id, hours)

    async def get_channel_idle_time(self, channel_id: str) -> float:
        """
        Get time since last message in channel (in hours).

        Args:
            channel_id: Discord channel ID

        Returns:
            Hours since last message
        """
        recent = await self.message_memory.get_recent(channel_id, limit=1)

        if not recent:
            return 999.0  # Very idle

        last_message = recent[0]
        now = datetime.now()
        delta = now - last_message.timestamp
        hours = delta.total_seconds() / 3600

        return hours

    async def _execute_action(self, action: ProactiveAction):
        """
        Execute a proactive action.

        Args:
            action: Action to execute
        """
        logger.info(f"Executing {action.type} action in channel {action.channel_id}")

        if action.type == "followup":
            await self._execute_followup(action)
        elif action.type == "proactive":
            await self._execute_proactive_message(action)
        elif action.type == "maintenance":
            # Already handled in maintain_memories()
            pass

    async def _execute_followup(self, action: ProactiveAction):
        """
        Execute follow-up action.

        Args:
            action: Follow-up action
        """
        # Check delivery method
        channel_active = await self.get_channel_idle_time(action.channel_id) < 0.5  # Active if <30min

        if not action.should_execute_now(channel_active):
            logger.debug(f"Deferring follow-up for channel {action.channel_id}")
            return

        # Send follow-up message via Discord
        if not self.discord_client:
            logger.error("Cannot send follow-up: Discord client not set")
            return

        try:
            channel = self.discord_client.get_channel(int(action.channel_id))
            if not channel:
                logger.warning(f"Channel {action.channel_id} not found")
                return

            await channel.send(action.message)
            logger.info(f"Sent follow-up message to channel {action.channel_id}")

            # Mark followup as complete and write back
            if action.followup_id:
                await self._mark_followup_complete(action.server_id, action.followup_id)

        except Exception as e:
            logger.error(f"Error sending follow-up message: {e}", exc_info=True)

    async def _mark_followup_complete(self, server_id: str, followup_id: str):
        """
        Mark a followup as complete and write back to file.

        Args:
            server_id: Discord server ID
            followup_id: ID of the completed followup
        """
        try:
            # Get current followups data
            followups_data = await self.memory.get_followups(server_id)

            # Find and remove from pending
            pending = followups_data.get("pending", [])
            completed = followups_data.get("completed", [])

            followup_found = None
            for i, followup in enumerate(pending):
                if followup.get("id") == followup_id:
                    followup_found = pending.pop(i)
                    break

            if not followup_found:
                logger.warning(f"Followup {followup_id} not found in pending list")
                return

            # Add completion timestamp and move to completed
            followup_found["completed_date"] = datetime.now().isoformat()
            completed.append(followup_found)

            # Write back to file
            followups_data["pending"] = pending
            followups_data["completed"] = completed
            await self.memory.write_followups(server_id, followups_data)

            logger.info(f"Marked followup {followup_id} as complete")

        except Exception as e:
            logger.error(f"Error marking followup complete: {e}", exc_info=True)

    async def _execute_proactive_message(self, action: ProactiveAction):
        """
        Execute proactive engagement.

        Args:
            action: Proactive action
        """
        if not self.discord_client:
            logger.error("Cannot send proactive message: Discord client not set")
            return

        try:
            # Get channel
            channel = self.discord_client.get_channel(int(action.channel_id))
            if not channel:
                logger.warning(f"Channel {action.channel_id} not found")
                return

            # Get recent context from channel
            recent_messages = await self.message_memory.get_recent(action.channel_id, limit=10)

            # Build context for Claude
            context_parts = []
            context_parts.append("You are initiating a natural conversation in a Discord channel.")
            context_parts.append(f"Channel idle time: {await self.get_channel_idle_time(action.channel_id):.1f} hours")

            if action.context:
                context_parts.append(f"\nAdditional context: {action.context}")

            if recent_messages:
                context_parts.append("\n\nRecent messages:")
                for msg in recent_messages[-5:]:  # Last 5 messages
                    context_parts.append(f"- {msg.author_name}: {msg.content}")

            context_parts.append("\n\nGenerate a brief, natural conversation starter (1-2 sentences). Be friendly and relevant to recent topics.")

            prompt = "\n".join(context_parts)

            # Call Claude to generate message
            logger.info(f"Generating proactive message for channel {action.channel_id}")
            response = await self.anthropic.messages.create(
                model=self.config.api.model,
                max_tokens=150,
                temperature=0.9,
                messages=[{"role": "user", "content": prompt}]
            )

            generated_message = response.content[0].text.strip()

            # Send the message
            sent_message = await channel.send(generated_message)
            logger.info(f"Sent proactive message to channel {action.channel_id}: {generated_message[:50]}...")

            # Increment rate limit counter
            self._increment_proactive_counter(action.channel_id)

            # Update engagement stats (increment total attempts)
            await self._record_proactive_attempt(action.server_id, action.channel_id)

        except Exception as e:
            logger.error(f"Error sending proactive message: {e}", exc_info=True)

    async def _record_proactive_attempt(self, server_id: str, channel_id: str):
        """
        Record a proactive engagement attempt in stats.

        Args:
            server_id: Discord server ID
            channel_id: Discord channel ID
        """
        try:
            # Get current stats
            stats = await self.memory.get_engagement_stats(server_id, channel_id)

            # Increment total attempts
            stats["total_attempts"] = stats.get("total_attempts", 0) + 1

            # Write back
            await self.memory.write_engagement_stats(server_id, channel_id, stats)
            logger.debug(f"Recorded proactive attempt for channel {channel_id} (total: {stats['total_attempts']})")

        except Exception as e:
            logger.error(f"Error recording proactive attempt: {e}", exc_info=True)

    async def _get_active_servers(self) -> List[str]:
        """
        Get list of active server IDs.

        Returns:
            List of server IDs
        """
        return await self.message_memory.get_active_servers()

    async def _get_server_for_channel(self, channel_id: str) -> Optional[str]:
        """
        Get server ID for channel.

        Args:
            channel_id: Discord channel ID

        Returns:
            Server ID or None
        """
        return await self.message_memory.get_server_for_channel(channel_id)

    async def _check_proactive_rate_limits(self, server_id: str, channel_id: str) -> bool:
        """
        Check if proactive engagement rate limits allow action.

        Args:
            server_id: Discord server ID
            channel_id: Discord channel ID

        Returns:
            True if within limits
        """
        # Check if we need to reset daily counters
        current_date = datetime.now().date()
        if current_date > self._rate_limit_reset_date:
            self._reset_rate_limits()

        # Check global daily limit
        if self._proactive_counts_global >= self.config.agentic.proactive.max_per_day_global:
            logger.debug(f"Global daily limit reached: {self._proactive_counts_global}/{self.config.agentic.proactive.max_per_day_global}")
            return False

        # Check per-channel daily limit
        channel_count = self._proactive_counts_per_channel.get(channel_id, 0)
        if channel_count >= self.config.agentic.proactive.max_per_day_per_channel:
            logger.debug(f"Per-channel daily limit reached for {channel_id}: {channel_count}/{self.config.agentic.proactive.max_per_day_per_channel}")
            return False

        return True

    def _reset_rate_limits(self):
        """Reset daily rate limit counters."""
        logger.info("Resetting daily rate limit counters")
        self._proactive_counts_global = 0
        self._proactive_counts_per_channel = {}
        self._rate_limit_reset_date = datetime.now().date()

    def _increment_proactive_counter(self, channel_id: str):
        """Increment proactive message counters for rate limiting."""
        self._proactive_counts_global += 1
        self._proactive_counts_per_channel[channel_id] = self._proactive_counts_per_channel.get(channel_id, 0) + 1
        logger.debug(f"Proactive counters: global={self._proactive_counts_global}, channel {channel_id}={self._proactive_counts_per_channel[channel_id]}")

    async def shutdown(self):
        """Stop agentic loop"""
        logger.info("Shutting down agentic engine...")
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("Agentic engine shutdown complete")
