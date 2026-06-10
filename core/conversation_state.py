"""
Conversation State - Persistent Per-Channel Context

Tracks the rolling message window, attachment annotations, and active skills
for a single channel. Persisted across restarts via ConversationStateManager.

v0.6.0: client-side token budgeting deleted (see REDESIGN.md §2). The only
guard is a temporary hard message cap until episodic sessions land (Phase 3).
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from core.internal_constants import TOOL_STUB_TEXT, TOOL_STUB_MIN_CHARS

logger = logging.getLogger(__name__)


class ConversationState:
    """
    Persistent conversation state for a single channel.

    Holds the linear message list sent to the Claude API, with attachment IDs
    annotated directly on their message (no index-keyed side tables).
    """

    def __init__(self, channel_id: str, max_messages: int):
        """
        Args:
            channel_id: Discord channel ID
            max_messages: Hard cap on Discord messages in the rolling window
        """
        self.channel_id = channel_id
        self.max_messages = max_messages

        # Messages array for Claude API (user/assistant role alternation)
        self.messages: List[Dict[str, Any]] = []

        # Statistics
        self.messages_removed = 0
        self.last_updated = datetime.utcnow()

        # Active skills tracking (v0.5.0 Progressive Disclosure)
        self.active_skills: List[str] = []

        # Session metadata (v0.6.0 episodic sessions)
        self.session_started_at = datetime.utcnow()
        self.session_input_tokens = 0  # usage watermark from response.usage
        self.seed_epoch = 0  # bumped on reseed; guards in-flight usage recording

        logger.debug(f"ConversationState initialized for channel {channel_id} (max_messages={max_messages})")

    def add_message(
        self,
        role: str,
        content: Any,
        attachment_ids: Optional[List[str]] = None
    ) -> None:
        """
        Add message to conversation state.

        Args:
            role: 'user' or 'assistant'
            content: Message content (string or content blocks array)
            attachment_ids: Optional attachment IDs, annotated on the message
        """
        message = {
            "role": role,
            "content": content,
            "message_type": f"discord_{role}"  # Tag as Discord message for message cap filtering
        }

        if attachment_ids:
            message["attachment_ids"] = attachment_ids

            # Sanity check: more attachment blocks than annotations means the
            # annotation drifted (fewer is fine - assistant turns embed text
            # placeholders instead of real blocks)
            if isinstance(content, list):
                attachment_block_count = sum(
                    1 for block in content
                    if block.get("type") in ("document", "container_upload", "image")
                )
                if attachment_block_count > len(attachment_ids):
                    logger.warning(
                        f"Attachment count mismatch in add_message(): "
                        f"{len(attachment_ids)} attachment_ids != {attachment_block_count} attachment blocks"
                    )

        self.messages.append(message)
        self.last_updated = datetime.utcnow()
        logger.debug(f"Added {role} message (total: {len(self.messages)} messages)")

    def add_tool_use_and_results(
        self,
        assistant_content: List[Dict[str, Any]],
        tool_results: List[Dict[str, Any]]
    ) -> None:
        """
        Add assistant message with tool_use blocks and user message with
        tool_result blocks, persisting tool interactions in the transcript.
        """
        self.messages.append({
            "role": "assistant",
            "content": assistant_content,
            "message_type": "tool_use"
        })
        self.messages.append({
            "role": "user",
            "content": tool_results,
            "message_type": "tool_result"
        })

        self.last_updated = datetime.utcnow()
        logger.debug(
            f"Added tool use and {len(tool_results)} tool results to conversation state "
            f"(total: {len(self.messages)} messages)"
        )

    def trim_leading_non_user(self) -> int:
        """
        Drop leading messages until the array starts with a plain user turn.

        The API requires the first message to be role 'user', and a leading
        tool_result (also user role) would orphan its tool_use. Cap removal
        and DB seeding can both violate this; reseed() guards it separately.

        Returns:
            Number of messages dropped
        """
        dropped = 0
        while self.messages and (
            self.messages[0]["role"] != "user"
            or self.messages[0].get("message_type") == "tool_result"
        ):
            removed = self.messages.pop(0)
            dropped += 1
            self.messages_removed += 1
            logger.debug(
                f"Dropped leading {removed.get('message_type', removed['role'])} message "
                f"(first message must be a user turn)"
            )
        return dropped

    def enforce_message_cap(self) -> int:
        """
        Remove oldest Discord messages while over the message cap.

        Only Discord messages count toward the cap; tool_use/tool_result
        messages ride along until episodic sessions replace this guard.

        Returns:
            Number of messages removed
        """
        removed_count = 0

        while True:
            discord_indices = [
                i for i, msg in enumerate(self.messages)
                if msg.get("message_type", "discord_user").startswith("discord_")
            ]
            if len(discord_indices) <= self.max_messages:
                break

            removed_message = self.messages.pop(discord_indices[0])
            removed_count += 1
            self.messages_removed += 1
            logger.debug(
                f"Removed oldest Discord message due to message cap "
                f"(role={removed_message['role']}, message_type={removed_message.get('message_type', 'unknown')})"
            )

        # Popping the head can leave tool messages or an assistant turn first
        removed_count += self.trim_leading_non_user()

        if removed_count > 0:
            discord_count = len([
                msg for msg in self.messages
                if msg.get("message_type", "discord_user").startswith("discord_")
            ])
            logger.info(
                f"Enforced message cap: removed {removed_count} Discord messages "
                f"(now {discord_count}/{self.max_messages} Discord messages, {len(self.messages)} total)"
            )

        return removed_count

    def record_usage(self, input_tokens: int, seed_epoch: Optional[int] = None) -> None:
        """
        Record the session usage watermark from response.usage.input_tokens.

        Callers that captured seed_epoch before their API call pass it back so
        usage measured against a pre-reseed context can't re-poison a freshly
        reseeded session (which would re-trigger episodization every turn).
        """
        if seed_epoch is not None and seed_epoch != self.seed_epoch:
            logger.debug("Ignoring stale usage watermark from before a reseed")
            return
        if input_tokens > self.session_input_tokens:
            self.session_input_tokens = input_tokens

    def reseed(self, keep_last_discord: int) -> None:
        """
        Start a fresh session after the previous one was episodized.

        Keeps the last N Discord messages (drops tool messages and any leading
        assistant messages so the array starts with a user turn) and resets
        session metadata.
        """
        discord_msgs = [
            m for m in self.messages
            if m.get("message_type", "discord_user").startswith("discord_")
        ]
        tail = discord_msgs[-keep_last_discord:] if keep_last_discord > 0 else []
        while tail and tail[0]["role"] != "user":
            tail.pop(0)

        self.messages = tail
        self.session_started_at = datetime.utcnow()
        self.session_input_tokens = 0
        self.seed_epoch += 1
        self.last_updated = datetime.utcnow()
        logger.info(
            f"Session reseeded for channel {self.channel_id}: "
            f"{len(self.messages)} messages kept"
        )

    def stub_old_tool_results(self, keep_turns: int) -> int:
        """
        Replace heavy tool results in turns older than the last keep_turns with
        a one-line stub (REDESIGN: turn-scoped working memory). Idempotent.

        Only client tool_result blocks are stubbed - server tool blocks are
        never persisted (see serialize_assistant_blocks in reactive_engine).

        Returns number of blocks stubbed.
        """
        assistant_indices = [
            i for i, m in enumerate(self.messages)
            if m.get("message_type") == "discord_assistant"
        ]
        if len(assistant_indices) <= keep_turns:
            return 0
        # Stub through the end of the turn keep_turns+1 turns ago; the last
        # keep_turns turns keep their tool results in full
        cutoff = assistant_indices[-(keep_turns + 1)] + 1

        def _stub_block(block) -> bool:
            content = block.get("content")
            if content == TOOL_STUB_TEXT:
                return False  # already stubbed
            if isinstance(content, str) and len(content) < TOOL_STUB_MIN_CHARS:
                return False  # small results stay
            block["content"] = TOOL_STUB_TEXT
            return True

        stubbed = 0
        for msg in self.messages[:cutoff]:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            if msg.get("message_type") != "tool_result":
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    stubbed += _stub_block(block)

        if stubbed:
            logger.info(f"Stubbed {stubbed} old tool result block(s) in channel {self.channel_id}")
        return stubbed

    def swap_file_id(self, old_file_id: str, new_file_id: Optional[str]) -> int:
        """
        Replace a stale Files API id embedded in persisted messages (document /
        container_upload blocks). With new_file_id=None the offending blocks are
        dropped instead - a dead file_id 404s the WHOLE API request, bricking
        the channel until the block rolls out of the cap.

        Returns number of blocks touched.
        """
        touched = 0
        for msg in self.messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            kept = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("file_id") == old_file_id:
                        if new_file_id:
                            block["file_id"] = new_file_id
                        else:
                            touched += 1
                            continue  # drop block
                        touched += 1
                    source = block.get("source")
                    if isinstance(source, dict) and source.get("file_id") == old_file_id:
                        if new_file_id:
                            source["file_id"] = new_file_id
                            touched += 1
                        else:
                            touched += 1
                            continue  # drop block
                kept.append(block)
            msg["content"] = kept
        if touched:
            # Dropping blocks can leave empty messages, which the API rejects
            self.messages = [
                m for m in self.messages
                if not (isinstance(m.get("content"), list) and not m["content"])
            ]
        return touched

    def get_messages_for_api(self) -> List[Dict[str, Any]]:
        """
        Get messages array ready for Claude API.

        Strips internal-only fields (message_type, attachment_ids). Server
        tool blocks never reach persisted state (stripped at serialization
        and healed at deserialization), so no block sanitization is needed.

        Returns:
            Copy of messages array without internal fields
        """
        internal_fields = {"message_type", "attachment_ids"}

        # Boundary self-heal: skip anything before the first plain user turn so
        # legacy/edge-case states (assistant-first, orphaned tool pair) still
        # render a valid transcript without mutating persisted state.
        start = 0
        while start < len(self.messages) and (
            self.messages[start]["role"] != "user"
            or self.messages[start].get("message_type") == "tool_result"
        ):
            start += 1
        if start:
            logger.debug(f"get_messages_for_api skipping {start} leading non-user message(s)")

        return [
            {k: v for k, v in msg.items() if k not in internal_fields}
            for msg in self.messages[start:]
        ]

    def set_active_skills(self, skill_names: List[str], max_skills: int = 2) -> None:
        """Set currently active skills for this conversation."""
        self.active_skills = skill_names[:max_skills]
        self.last_updated = datetime.utcnow()
        logger.debug(f"Active skills set: {self.active_skills}")

    def add_active_skill(self, skill_name: str, max_skills: int = 2) -> bool:
        """
        Add a skill to active skills list.

        Returns:
            True if added (or already active), False if at capacity
        """
        if skill_name in self.active_skills:
            return True

        if len(self.active_skills) >= max_skills:
            return False

        self.active_skills.append(skill_name)
        self.last_updated = datetime.utcnow()
        logger.debug(f"Added skill: {skill_name}. Active: {self.active_skills}")
        return True

    def replace_active_skill(self, old_skill: str, new_skill: str) -> bool:
        """
        Replace an active skill with a new one.

        Returns:
            True if replaced, False if old_skill not found
        """
        if old_skill not in self.active_skills:
            return False

        idx = self.active_skills.index(old_skill)
        self.active_skills[idx] = new_skill
        self.last_updated = datetime.utcnow()
        logger.debug(f"Replaced skill {old_skill} with {new_skill}. Active: {self.active_skills}")
        return True

    def get_active_skills(self) -> List[str]:
        """Get list of currently active skill names."""
        return self.active_skills.copy()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for database storage."""
        return {
            "channel_id": self.channel_id,
            "max_messages": self.max_messages,
            "messages": self.messages,
            "active_skills": self.active_skills,
            "messages_removed": self.messages_removed,
            "last_updated": self.last_updated.isoformat(),
            "session_started_at": self.session_started_at.isoformat(),
            "session_input_tokens": self.session_input_tokens,
        }

    # Block types older releases persisted but which can't be replayed (their
    # content was stringified, an invalid shape for these types) - stripped on
    # load so existing rows heal themselves
    _LEGACY_SERVER_BLOCK_TYPES = frozenset({
        "server_tool_use",
        "bash_code_execution",
        "text_editor_code_execution",
        "code_execution_result",
        "code_execution_tool_result",
        "bash_code_execution_tool_result",
        "text_editor_code_execution_tool_result",
        "web_search_tool_result",
        "web_fetch_tool_result",
    })

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationState":
        """Deserialize from dictionary (tolerates pre-v0.6.0 rows)."""
        state = cls(
            channel_id=data["channel_id"],
            max_messages=data["max_messages"]
        )

        state.messages = data["messages"]

        # Backward compatibility: add message_type to legacy messages without it
        for msg in state.messages:
            if "message_type" not in msg:
                msg["message_type"] = f"discord_{msg['role']}"

        # Heal rows written before server blocks were excluded from persistence
        stripped = 0
        for msg in state.messages:
            content = msg.get("content")
            if isinstance(content, list):
                kept = [
                    b for b in content
                    if not (isinstance(b, dict) and b.get("type") in cls._LEGACY_SERVER_BLOCK_TYPES)
                ]
                stripped += len(content) - len(kept)
                msg["content"] = kept
        if stripped:
            # Stripping can leave empty messages, which the API rejects
            state.messages = [
                m for m in state.messages
                if not (isinstance(m.get("content"), list) and not m["content"])
            ]
            logger.info(
                f"Stripped {stripped} legacy server tool block(s) from persisted "
                f"state for channel {state.channel_id}"
            )

        state.active_skills = data.get("active_skills", [])
        state.messages_removed = data.get("messages_removed", 0)

        if "last_updated" in data:
            state.last_updated = datetime.fromisoformat(data["last_updated"])

        if "session_started_at" in data:
            state.session_started_at = datetime.fromisoformat(data["session_started_at"])
        state.session_input_tokens = data.get("session_input_tokens", 0)

        return state

    def __repr__(self) -> str:
        return (
            f"ConversationState(channel={self.channel_id}, "
            f"messages={len(self.messages)}/{self.max_messages})"
        )
