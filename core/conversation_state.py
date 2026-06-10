"""
Conversation State - Persistent Per-Channel Context

Tracks messages, attachments, and token counts across turns.
Enforces dual-cap system (message count + token budget).
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass
import json
import base64
import copy
from pathlib import Path

from core.file_text_extractor import FileTextExtractor

logger = logging.getLogger(__name__)


@dataclass
class ContextItem:
    """
    Represents a token-consuming item in conversation context.

    Enables unified tracking and stripping of documents, tool results, and images
    regardless of whether they're client-side or server-side managed.
    """
    type: str  # "document", "tool_result", "image"
    message_index: int  # Which message contains this item
    estimated_tokens: int  # Token cost estimate
    item_id: str  # Unique identifier (attachment_id, tool_use_id, etc.)
    is_excluded_tool: bool = False  # If tool result, is it from excluded tool?
    metadata: Optional[Dict[str, Any]] = None  # Additional data (tool name, filename, etc.)


class ConversationState:
    """
    Persistent conversation state for a single channel.

    Manages rolling window of messages with dual-cap enforcement:
    - Message count cap (prevents schizophrenia)
    - Token budget cap (prevents inefficiency)

    Tracks attachments and documents with lifecycle management.
    """

    def __init__(
        self,
        channel_id: str,
        max_messages: int,
        max_tokens: int
    ):
        """
        Initialize conversation state.

        Args:
            channel_id: Discord channel ID
            max_messages: Maximum messages in rolling window
            max_tokens: Maximum total tokens (conversation content only)
        """
        self.channel_id = channel_id
        self.max_messages = max_messages
        self.max_tokens = max_tokens

        # Messages array for Claude API (user/assistant role alternation)
        self.messages: List[Dict[str, Any]] = []

        # Token tracking
        self.conversation_tokens = 0  # Current conversation content tokens
        self.last_token_count_time = None

        # Document tracking (for lifecycle management)
        self.document_references: Dict[str, List[str]] = {}  # message_index -> [attachment_ids]

        # Unified context item tracking (v0.5.0 Phase 1)
        # Tracks ALL token-consuming items: documents, tool results, images
        # Enables coordinated stripping across client-side and server-side systems
        self.context_items: List[ContextItem] = []

        # Statistics
        self.messages_removed = 0
        self.documents_stripped = 0
        self.last_updated = datetime.utcnow()

        # Active skills tracking (v0.5.0 Progressive Disclosure)
        # Tracks which skills are currently loaded for this conversation
        # Claude can request skill changes via request_skill tool
        self.active_skills: List[str] = []

        logger.debug(f"ConversationState initialized for channel {channel_id} (max_messages={max_messages}, max_tokens={max_tokens})")

    def add_message(
        self,
        role: str,
        content: Any,
        attachment_ids: Optional[List[str]] = None,
        attachment_metadata: Optional[Dict[str, Dict]] = None
    ) -> None:
        """
        Add message to conversation state.

        Args:
            role: 'user' or 'assistant'
            content: Message content (string or content blocks array)
            attachment_ids: Optional list of attachment IDs for this message
            attachment_metadata: Optional dict mapping attachment_id -> metadata dict
                                 (includes estimated_tokens, size_bytes, filename)
        """
        message = {
            "role": role,
            "content": content,
            "message_type": f"discord_{role}"  # Tag as Discord message for message cap filtering
        }

        self.messages.append(message)
        message_index = len(self.messages) - 1

        # Track document references
        if attachment_ids:
            self.document_references[str(message_index)] = attachment_ids

            # Mitigation 1: Validate attachment_ids count matches attachment blocks (prevents Bug #2 Risk R3)
            # Bug #8 fix: Count "document", "container_upload", and "image" blocks
            if isinstance(content, list):
                attachment_block_count = sum(
                    1 for block in content
                    if block.get("type") in ("document", "container_upload", "image")
                )
                if len(attachment_ids) != attachment_block_count:
                    logger.error(
                        f"CRITICAL: Attachment count mismatch in add_message(). "
                        f"attachment_ids={len(attachment_ids)}, attachment_blocks={attachment_block_count}, "
                        f"message_index={message_index}"
                    )
                    raise ValueError(
                        f"Attachment count mismatch in message {message_index}: "
                        f"{len(attachment_ids)} attachment_ids != {attachment_block_count} attachment blocks"
                    )

            # Track ALL attachments as context items (v0.5.0 Phase 8)
            # Unified tracking: documents, container_uploads, AND images
            if isinstance(content, list):
                attachment_idx = 0
                for block in content:
                    block_type = block.get("type")
                    if block_type in ("document", "container_upload", "image"):
                        # Use attachment_id from the list
                        if attachment_idx < len(attachment_ids):
                            attachment_id = attachment_ids[attachment_idx]
                            attachment_idx += 1

                            # Phase 5: Use provided metadata for file size disclosure
                            metadata = {}
                            estimated_tokens = 1000  # Conservative default, updated by token counting

                            if attachment_metadata and attachment_id in attachment_metadata:
                                att_meta = attachment_metadata[attachment_id]
                                # Store file size and filename
                                if "size_bytes" in att_meta:
                                    metadata["size_bytes"] = att_meta["size_bytes"]
                                if "filename" in att_meta:
                                    metadata["filename"] = att_meta["filename"]
                                logger.debug(f"Found metadata for attachment {attachment_id}: {att_meta}")
                            else:
                                logger.warning(f"No metadata found for attachment_id {attachment_id}. Available keys: {list(attachment_metadata.keys()) if attachment_metadata else 'None'}")

                            # Phase 1.2: Track source type - document_block or container_upload
                            metadata["source"] = block_type

                            # Phase 1.2: Use actual block type (document or container_upload) instead of hardcoded "document"
                            self.add_context_item(
                                item_type=block_type,  # Use detected block type
                                message_index=message_index,
                                estimated_tokens=estimated_tokens,
                                item_id=attachment_id,
                                is_excluded_tool=False,
                                metadata=metadata
                            )

        # Invalidate token cache to force recount on next check (v0.5.0)
        self.invalidate_token_cache()

        self.last_updated = datetime.utcnow()
        logger.debug(f"Added {role} message (total: {len(self.messages)} messages)")

    def add_tool_use_and_results(
        self,
        assistant_content: List[Dict[str, Any]],
        tool_results: List[Dict[str, Any]],
        excluded_tools: Optional[List[str]] = None
    ) -> None:
        """
        Add assistant message with tool_use blocks and user message with tool_result blocks.

        Used during tool execution loop to persist tool interactions in conversation state.
        Tracks tool results as context items for unified management.

        Args:
            assistant_content: Assistant message content blocks (includes tool_use blocks)
            tool_results: Tool result blocks to add as user message
            excluded_tools: List of tool names excluded from context editing (e.g., ["memory"])
        """
        excluded_tools = excluded_tools or []

        # Add assistant message with tool_use blocks
        self.messages.append({
            "role": "assistant",
            "content": assistant_content,
            "message_type": "tool_use"  # Tag as tool call for message cap filtering
        })
        assistant_index = len(self.messages) - 1

        # Add user message with tool_result blocks
        self.messages.append({
            "role": "user",
            "content": tool_results,
            "message_type": "tool_result"  # Tag as tool result for message cap filtering
        })
        user_index = len(self.messages) - 1

        # Track tool results as context items (v0.5.0 Phase 1)
        for tool_result in tool_results:
            if tool_result.get("type") == "tool_result":
                tool_use_id = tool_result.get("tool_use_id", "unknown")

                # Determine tool name from assistant content
                tool_name = None
                for block in assistant_content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") == tool_use_id:
                        tool_name = block.get("name", "unknown")
                        break

                # Check if this tool is excluded from context editing
                is_excluded = tool_name in excluded_tools if tool_name else False

                # Estimate tokens (rough estimate based on content length)
                content = tool_result.get("content", "")
                if isinstance(content, str):
                    estimated_tokens = len(content) // 4  # 4 chars per token estimate
                else:
                    estimated_tokens = 100  # Conservative default

                # Add to unified tracking
                self.add_context_item(
                    item_type="tool_result",
                    message_index=user_index,
                    estimated_tokens=estimated_tokens,
                    item_id=tool_use_id,
                    is_excluded_tool=is_excluded,
                    metadata={"tool_name": tool_name} if tool_name else {}
                )

        # Invalidate token cache
        self.invalidate_token_cache()
        self.last_updated = datetime.utcnow()

        logger.debug(
            f"Added tool use and {len(tool_results)} tool results to conversation state "
            f"(total: {len(self.messages)} messages)"
        )

    def enforce_message_cap(self) -> int:
        """
        Remove oldest Discord messages if over message cap.

        Note: Only counts messages with message_type starting with "discord_".
        Tool use/result messages are managed separately by token budget.

        Returns:
            Number of messages removed
        """
        removed_count = 0

        # Count only Discord messages (exclude tool_use/tool_result)
        discord_messages = [
            (i, msg) for i, msg in enumerate(self.messages)
            if msg.get("message_type", "discord_user").startswith("discord_")
        ]

        while len(discord_messages) > self.max_messages:
            # Find oldest Discord message
            oldest_idx, oldest_msg = discord_messages[0]

            # Remove the oldest Discord message
            removed_message = self.messages.pop(oldest_idx)
            removed_count += 1
            self.messages_removed += 1

            # Clean up document references for removed message
            if str(oldest_idx) in self.document_references:
                del self.document_references[str(oldest_idx)]

            # Shift all document reference indices down by 1 for messages after removed index
            new_refs = {}
            for idx_str, att_ids in self.document_references.items():
                old_idx = int(idx_str)
                if old_idx > oldest_idx:
                    new_refs[str(old_idx - 1)] = att_ids
                elif old_idx < oldest_idx:
                    new_refs[idx_str] = att_ids
            self.document_references = new_refs

            # Reindex unified context items (v0.5.0 Phase 1)
            self._reindex_context_items_after_removal(removed_index=oldest_idx)

            logger.debug(
                f"Removed oldest Discord message due to message cap "
                f"(role={removed_message['role']}, message_type={removed_message.get('message_type', 'unknown')})"
            )

            # Recalculate discord_messages list after removal
            discord_messages = [
                (i, msg) for i, msg in enumerate(self.messages)
                if msg.get("message_type", "discord_user").startswith("discord_")
            ]

        if removed_count > 0:
            discord_count = len([msg for msg in self.messages if msg.get("message_type", "discord_user").startswith("discord_")])
            logger.info(
                f"Enforced message cap: removed {removed_count} Discord messages "
                f"(now {discord_count}/{self.max_messages} Discord messages, {len(self.messages)} total)"
            )

        return removed_count

    def remove_oldest_message(self) -> bool:
        """
        Remove oldest message for token budget management.

        Unlike enforce_message_cap(), this removes one message regardless of cap.
        Used when token budget is exceeded but message count is under cap.

        IMPORTANT: Preserves tool_use/tool_result pairs - if removing a message with
        tool_use blocks, also removes the following message with tool_result blocks.

        Returns:
            True if message removed, False if no messages to remove
        """
        if len(self.messages) == 0:
            return False

        # Check if oldest message is part of a tool_use/result pair
        first_message = self.messages[0]
        is_tool_use = first_message.get("message_type") == "tool_use"
        is_tool_result = first_message.get("message_type") == "tool_result"

        # Remove oldest message
        removed_message = self.messages.pop(0)
        self.messages_removed += 1
        messages_removed_count = 1

        # Clean up document references for removed message
        if "0" in self.document_references:
            del self.document_references["0"]

        # Reindex unified context items (v0.5.0 Phase 1)
        self._reindex_context_items_after_removal(removed_index=0)

        # If this was a tool_use message, also remove the following tool_result message
        # to prevent orphaned tool_result blocks (API requirement)
        if is_tool_use and len(self.messages) > 0:
            next_message = self.messages[0]
            if next_message.get("message_type") == "tool_result":
                self.messages.pop(0)
                self.messages_removed += 1
                messages_removed_count += 1

                # Clean up document references for second removed message
                if "0" in self.document_references:
                    del self.document_references["0"]

                # Reindex again after second removal
                self._reindex_context_items_after_removal(removed_index=0)

                logger.debug(
                    f"Removed tool_use/tool_result pair due to token budget "
                    f"(preserved API constraint: no orphaned tool_result blocks)"
                )

        # If oldest message was an orphaned tool_result (shouldn't happen but handle gracefully)
        if is_tool_result:
            logger.warning(
                f"Removed orphaned tool_result message (this shouldn't happen - "
                f"tool_use was likely already removed)"
            )

        # Shift all document reference indices down by number of removed messages
        new_refs = {}
        for idx_str, att_ids in self.document_references.items():
            old_idx = int(idx_str)
            if old_idx >= messages_removed_count:
                new_refs[str(old_idx - messages_removed_count)] = att_ids
        self.document_references = new_refs

        # Log if removed message contained tool results
        if isinstance(removed_message.get("content"), list):
            for block in removed_message["content"]:
                if block.get("type") == "tool_result":
                    tool_name = block.get("tool_name", "unknown")
                    logger.info(
                        f"Removed message with {tool_name} tool result due to token budget. "
                        f"Current: {len(self.messages)} messages"
                    )

        logger.debug(f"Removed oldest message for token budget (role={removed_message['role']}, removed_count={messages_removed_count})")
        return True

    def strip_documents_from_oldest(self, count: int = 1) -> List[Tuple[int, List[str]]]:
        """
        Strip documents from oldest N messages with attachments.

        Message text remains with metadata indicating attachment was stripped.

        Args:
            count: Number of messages to strip documents from

        Returns:
            List of (message_index, attachment_ids) tuples that were stripped
        """
        stripped = []

        # Find oldest messages with documents
        for i in range(min(count, len(self.messages))):
            message_idx_str = str(i)

            if message_idx_str in self.document_references:
                message = self.messages[i]
                att_ids = self.document_references[message_idx_str]

                # Strip document content blocks from message
                if isinstance(message["content"], list):
                    original_length = len(message["content"])

                    # v0.5.0: Strip ALL attachment types uniformly (FIFO, no special treatment)
                    # Images, documents, and container_uploads all follow the same stripping rules
                    message["content"] = [
                        block for block in message["content"]
                        if block.get("type") not in ("document", "container_upload", "image")
                    ]

                    # Check if something was actually removed BEFORE string conversion
                    was_stripped = len(message["content"]) < original_length

                    # Convert back to string if only text remains
                    if len(message["content"]) == 1 and message["content"][0].get("type") == "text":
                        message["content"] = message["content"][0]["text"]

                    if was_stripped:
                        stripped.append((i, att_ids))
                        self.documents_stripped += 1
                        logger.debug(f"Stripped documents from message {i} (attachments: {att_ids})")

        if stripped:
            logger.info(f"Stripped documents from {len(stripped)} messages")

        return stripped

    def strip_documents_from_current(self) -> Optional[Tuple[int, List[str]]]:
        """
        Strip documents from the most recent (current) message.

        Used when the current message alone exceeds the token budget.
        This prevents the "infinite loop" bug where removing old messages
        doesn't help because the current message is too large.

        Returns:
            Tuple of (message_index, attachment_ids) if stripped, None otherwise
        """
        if not self.messages:
            return None

        # Get the most recent message index
        current_idx = len(self.messages) - 1
        message_idx_str = str(current_idx)
        message = self.messages[current_idx]

        # Check if current message has documents
        if message_idx_str not in self.document_references:
            logger.debug(f"Current message {current_idx} has no document references to strip")
            return None

        att_ids = self.document_references[message_idx_str]

        # Strip document content blocks from current message
        if isinstance(message["content"], list):
            original_length = len(message["content"])

            # v0.5.0: Strip ALL attachment types uniformly (FIFO, no special treatment)
            # Images, documents, and container_uploads all follow the same stripping rules
            message["content"] = [
                block for block in message["content"]
                if block.get("type") not in ("document", "container_upload", "image")
            ]

            was_stripped = len(message["content"]) < original_length

            # If all content was removed, add a placeholder
            if not message["content"]:
                message["content"] = [{
                    "type": "text",
                    "text": "[Attachments removed: file too large for token budget]"
                }]

            # Convert back to string if only text remains
            if len(message["content"]) == 1 and message["content"][0].get("type") == "text":
                message["content"] = message["content"][0]["text"]

            if was_stripped:
                self.documents_stripped += 1
                logger.warning(
                    f"Stripped documents from CURRENT message {current_idx} "
                    f"(attachments: {att_ids}) - file exceeded token budget"
                )
                return (current_idx, att_ids)

        return None

    # ============================================================================
    # Unified Context Item Management (v0.5.0 Phase 1)
    # ============================================================================

    def add_context_item(
        self,
        item_type: str,
        message_index: int,
        estimated_tokens: int,
        item_id: str,
        is_excluded_tool: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Add a context item to unified tracking.

        Args:
            item_type: "document", "tool_result", or "image"
            message_index: Index of message containing this item
            estimated_tokens: Token cost estimate
            item_id: Unique identifier (attachment_id, tool_use_id, etc.)
            is_excluded_tool: True if this is a tool result from excluded tool (e.g., memory)
            metadata: Additional data (tool name, filename, etc.)
        """
        item = ContextItem(
            type=item_type,
            message_index=message_index,
            estimated_tokens=estimated_tokens,
            item_id=item_id,
            is_excluded_tool=is_excluded_tool,
            metadata=metadata or {}
        )

        self.context_items.append(item)
        logger.debug(
            f"Added context item: type={item_type}, message_index={message_index}, "
            f"tokens={estimated_tokens}, id={item_id}, excluded={is_excluded_tool}"
        )

    def _reindex_context_items_after_removal(self, removed_index: int) -> None:
        """
        Update context item indices after a message is removed.

        When a message is removed, all items in later messages need their indices decremented.

        Args:
            removed_index: Index of the message that was removed
        """
        # Remove items from the removed message
        self.context_items = [
            item for item in self.context_items
            if item.message_index != removed_index
        ]

        # Decrement indices for items in later messages
        for item in self.context_items:
            if item.message_index > removed_index:
                item.message_index -= 1

        logger.debug(f"Reindexed context items after removing message at index {removed_index}")

    def sync_with_server_clearing(
        self,
        tool_uses_cleared: int,
        excluded_tools: List[str]
    ) -> None:
        """
        Sync local context_items with server-side context editing.

        Server-side context editing clears old tool results automatically.
        We need to mirror that locally to keep state consistent.

        Args:
            tool_uses_cleared: Number of tool uses the server cleared
            excluded_tools: List of tool names excluded from clearing
        """
        if tool_uses_cleared == 0:
            return

        # Find non-excluded tool results, oldest first
        tool_results = [
            item for item in self.context_items
            if item.type == "tool_result" and not item.is_excluded_tool
        ]

        # Sort by message index (oldest first)
        tool_results.sort(key=lambda x: x.message_index)

        # Remove the oldest N that were cleared by server
        to_remove = tool_results[:tool_uses_cleared]

        for item in to_remove:
            self.context_items.remove(item)
            logger.debug(
                f"Removed tool result from context_items (server cleared): "
                f"id={item.item_id}, message_index={item.message_index}"
            )

        if to_remove:
            logger.info(
                f"Synced with server-side context editing: "
                f"removed {len(to_remove)} tool results from tracking"
            )

    async def _convert_messages_for_token_counting(
        self,
        attachment_db,
        local_storage
    ):
        """
        Convert messages with file_id references to lightweight placeholders for token counting.

        DESIGN PRINCIPLE (v0.5.0):
        - Files uploaded to Files API are AVAILABLE to Claude but don't count against context budget
        - Claude uses code execution to peek at large files selectively
        - Token budget controls what's IN CONTEXT, not what's potentially readable
        - file_id references cost ~100-200 tokens (metadata), not the full file content

        This replaces file_id document blocks with lightweight text placeholders that
        represent the actual context cost (file metadata), not the full file content.

        Args:
            attachment_db: AttachmentDatabase instance for querying file metadata
            local_storage: LocalStorageManager instance for loading files

        Returns:
            Tuple of (converted_messages, estimated_extra_tokens):
            - converted_messages: Deep copy with file blocks as lightweight placeholders
            - estimated_extra_tokens: Always 0 (placeholders are counted directly)
        """
        converted_messages = copy.deepcopy(self.get_messages_for_api())

        # Fixed token cost per file reference (metadata, file_id, etc.)
        # This represents what Claude "sees" in context - file metadata, not content
        FILE_REFERENCE_TOKENS = 150  # ~600 chars for filename, size, type info

        # Debug: Log document_references for understanding what we're processing
        logger.info(f"Token counting conversion: {len(converted_messages)} messages, document_references={self.document_references}")

        for message_idx, message in enumerate(converted_messages):
            content = message.get("content")

            if not isinstance(content, list):
                continue

            attachment_ids_for_message = self.document_references.get(str(message_idx), [])
            doc_block_counter = 0

            for i, block in enumerate(content):
                block_type = block.get("type")

                # Handle container_upload blocks (spreadsheets, etc.)
                # These are available at /tmp/uploads/ for code execution
                # Context cost is just the reference metadata
                # Note: container_upload has flat file_id, not nested under "file"
                if block_type == "container_upload":
                    file_id = block.get("file_id", "unknown")
                    # Replace with lightweight placeholder
                    content[i] = {
                        "type": "text",
                        "text": f"[File available for code execution: {file_id}]"
                    }
                    logger.info(f"Replaced container_upload with placeholder for token counting (file_id: {file_id})")
                    continue

                # Handle document blocks with file_id references
                # Debug: Log block structure for diagnosis
                if block_type == "document":
                    source = block.get("source")
                    logger.info(f"Found document block at msg[{message_idx}]: source={source}")
                    if not isinstance(source, dict):
                        logger.warning(f"Document block has non-dict source: {type(source)}")
                        continue
                    if source.get("type") != "file":
                        logger.warning(f"Document block source type is '{source.get('type')}', expected 'file'")
                        continue

                    file_id = source.get("file_id", "unknown")

                    # Get filename from attachment metadata if available
                    filename = "document"
                    size_info = ""
                    if doc_block_counter < len(attachment_ids_for_message):
                        attachment_id = attachment_ids_for_message[doc_block_counter]
                        doc_block_counter += 1

                        try:
                            async with attachment_db.db.execute(
                                "SELECT filename, size_bytes FROM attachments WHERE attachment_id = ?",
                                (attachment_id,)
                            ) as cursor:
                                row = await cursor.fetchone()
                            if row:
                                filename = row[0] or filename
                                if row[1]:
                                    size_kb = row[1] / 1024
                                    if size_kb > 1024:
                                        size_info = f", {size_kb/1024:.1f}MB"
                                    else:
                                        size_info = f", {size_kb:.1f}KB"
                        except Exception as e:
                            logger.debug(f"Could not get attachment metadata: {e}")

                    # Replace with lightweight placeholder representing actual context cost
                    content[i] = {
                        "type": "text",
                        "text": f"[Document: {filename}{size_info} - available via Files API]"
                    }
                    logger.info(f"Replaced document file_id with placeholder for token counting: {filename} (file_id: {file_id})")

                # Handle image blocks - these ARE counted fully since they're base64 in context
                # Images are embedded directly, not via Files API file_id
                elif block_type == "image":
                    # Leave image blocks as-is - they count against context budget
                    pass

        # Simplify messages with single text block
        for message_idx, message in enumerate(converted_messages):
            content = message.get("content")
            if isinstance(content, list):
                if len(content) == 1 and isinstance(content[0], dict) and content[0].get("type") == "text":
                    converted_messages[message_idx]["content"] = content[0]["text"]
                elif len(content) == 0:
                    converted_messages[message_idx]["content"] = "[empty message]"

        logger.debug(f"Converted {len(self.document_references)} file references to placeholders for token counting")

        return converted_messages, 0  # No extra tokens - placeholders are counted directly

    def get_messages_for_api(self) -> List[Dict[str, Any]]:
        """
        Get messages array ready for Claude API.

        Strips internal-only fields (message_type) before sending to API.
        Also sanitizes server tool blocks to prevent orphaned tool_use/result pairs.

        Bug #15 fix: Uses two-pass sanitization to remove both:
        - Server tool results missing tool_use_id
        - Server tool uses without corresponding valid results

        Returns:
            Copy of messages array without internal fields
        """
        # Server tool result types that require tool_use_id
        server_tool_result_types = {
            "code_execution_result",
            "bash_code_execution_tool_result",
            "text_editor_code_execution_tool_result",
            "web_search_tool_result",
            "web_fetch_tool_result"
        }

        # Bug #15 fix: Server tool use types that require corresponding results
        server_tool_use_types = {
            "bash_code_execution",
            "text_editor_code_execution",
            "server_tool_use"
        }

        # Strip message_type field (internal only, not part of API spec)
        api_messages = []
        for msg in self.messages:
            api_msg = {k: v for k, v in msg.items() if k != "message_type"}

            # Bug #15 fix: Two-pass sanitization for server tool blocks
            if "content" in api_msg and isinstance(api_msg["content"], list):
                content_list = api_msg["content"]

                # First pass: Collect valid tool_use_ids (those with corresponding results)
                valid_tool_use_ids = set()
                for block in content_list:
                    if isinstance(block, dict):
                        block_type = block.get("type", "")
                        if block_type in server_tool_result_types and "tool_use_id" in block:
                            valid_tool_use_ids.add(block["tool_use_id"])

                # Second pass: Filter out orphaned tool uses AND results missing tool_use_id
                sanitized_content = []
                for block in content_list:
                    if isinstance(block, dict):
                        block_type = block.get("type", "")

                        # Bug #15 fix: Skip server tool uses without valid results
                        if block_type in server_tool_use_types:
                            tool_id = block.get("id")
                            if tool_id and tool_id not in valid_tool_use_ids:
                                logger.debug(f"Skipping orphaned {block_type} with id {tool_id} (no valid result)")
                                continue

                        # Bug #12 fix: Skip server tool results missing tool_use_id
                        if block_type in server_tool_result_types:
                            if "tool_use_id" not in block:
                                logger.debug(f"Skipping {block_type} block missing tool_use_id")
                                continue

                        sanitized_content.append(block)
                    else:
                        sanitized_content.append(block)
                api_msg["content"] = sanitized_content

            api_messages.append(api_msg)
        return api_messages

    def has_thinking_blocks(self) -> bool:
        """
        Check if conversation contains any thinking blocks.

        Used to determine if extended thinking should be enabled for token counting.

        Returns:
            True if any assistant message contains thinking blocks
        """
        for msg in self.messages:
            if msg.get("role") == "assistant":
                content = msg.get("content")
                if isinstance(content, list):
                    for block in content:
                        if block.get("type") == "thinking":
                            return True
        return False

    def get_message_count(self) -> int:
        """Get current message count"""
        return len(self.messages)

    def set_token_count(self, tokens: int) -> None:
        """
        Update conversation token count.

        Args:
            tokens: Total conversation tokens
        """
        self.conversation_tokens = tokens
        self.last_token_count_time = datetime.utcnow()

    def get_token_count(self) -> int:
        """Get current conversation token count"""
        return self.conversation_tokens

    def is_over_token_budget(self) -> bool:
        """Check if over token budget"""
        return self.conversation_tokens > self.max_tokens

    def get_token_usage_percent(self) -> float:
        """Get token usage as percentage of cap"""
        if self.max_tokens == 0:
            return 0.0
        return (self.conversation_tokens / self.max_tokens) * 100.0

    async def count_tokens(
        self,
        anthropic_client,
        system_prompt: str,
        tools: List[Dict[str, Any]],
        attachment_db=None,
        local_storage=None
    ) -> int:
        """
        Count total input tokens for current conversation state.

        Uses Anthropic's token counting API with 60-second cache to prevent
        rate limit issues.

        Converts file_id references to base64 for token counting API, which
        doesn't support file_id format. Original messages remain unchanged.

        Args:
            anthropic_client: AsyncAnthropic client instance
            system_prompt: System prompt string
            tools: Tools array for API
            attachment_db: Optional AttachmentDatabase for file_id conversion
            local_storage: Optional LocalStorageManager for file_id conversion

        Returns:
            Total input tokens (system + tools + messages)
        """
        # Check cache (60-second TTL)
        if self.last_token_count_time:
            age_seconds = (datetime.utcnow() - self.last_token_count_time).total_seconds()
            if age_seconds < 60:
                logger.debug(f"Token count cache hit (age: {age_seconds:.1f}s)")
                return self.conversation_tokens

        # Convert file_id references to lightweight placeholders for token counting
        messages_for_counting = self.get_messages_for_api()
        estimated_extra_tokens = 0
        logger.info(f"count_tokens: attachment_db={attachment_db is not None}, local_storage={local_storage is not None}, document_references={len(self.document_references)} refs")
        if attachment_db and local_storage and self.document_references:
            try:
                converted, extra = await self._convert_messages_for_token_counting(
                    attachment_db,
                    local_storage
                )
                messages_for_counting = converted
                estimated_extra_tokens = extra
                if extra > 0:
                    logger.debug(f"Partial file conversion: {extra} tokens estimated for unconverted files")
                else:
                    logger.debug("All file references converted for token counting")
            except Exception as e:
                logger.error(f"Failed to convert messages for token counting: {e}")
                # Fall back to original messages

        # Count tokens via API
        # NOTE: Exclude tools to avoid API validation errors with custom tool types
        # Only enable thinking if conversation actually contains thinking blocks
        try:
            count_params = {
                "model": "claude-sonnet-4-5-20250929",
                "system": system_prompt,
                "messages": messages_for_counting  # Use converted messages
                # tools excluded - contributes minimal overhead anyway
            }

            # Only enable thinking parameter if conversation has thinking blocks
            # (avoids API errors when old DB messages lack thinking blocks)
            if self.has_thinking_blocks():
                count_params["thinking"] = {"type": "enabled", "budget_tokens": 10000}
                logger.debug("Enabling extended thinking for token counting (thinking blocks present)")

            # Use beta API with required beta features for Files API and code execution
            result = await anthropic_client.beta.messages.count_tokens(
                betas=["code-execution-2025-08-25", "files-api-2025-04-14"],
                **count_params
            )

            token_count = result.input_tokens + estimated_extra_tokens
            self.set_token_count(token_count)

            if estimated_extra_tokens > 0:
                logger.debug(f"Token count: {token_count} (API: {result.input_tokens} + estimated: {estimated_extra_tokens}, {len(self.messages)} messages)")
            else:
                logger.debug(f"Token count: {token_count} ({len(self.messages)} messages)")
            return token_count

        except Exception as e:
            # Rate limit or API error - use cached value or estimate
            logger.warning(f"Token counting failed: {e}")

            if self.conversation_tokens > 0:
                # Return last known count
                logger.debug(f"Using cached token count: {self.conversation_tokens}")
                return self.conversation_tokens
            else:
                # Rough estimate: 4 characters per token
                estimated = self._estimate_tokens()
                logger.debug(f"Using estimated token count: {estimated}")
                self.set_token_count(estimated)
                return estimated

    @staticmethod
    async def count_static_tokens(
        anthropic_client,
        system_prompt: str,
        tools: List[Dict[str, Any]]
    ) -> int:
        """
        Count static context tokens (system prompt + tools).

        This is counted separately from conversation content to understand
        true conversation token usage and remaining budget.

        Args:
            anthropic_client: AsyncAnthropic client instance
            system_prompt: System prompt string
            tools: Tools array for API

        Returns:
            Static context token count
        """
        # NOTE: Exclude tools to avoid API validation errors with custom tool types
        try:
            # API requires at least one message, so use minimal dummy message
            # Use beta API with required beta features for Files API and code execution
            result = await anthropic_client.beta.messages.count_tokens(
                betas=["code-execution-2025-08-25", "files-api-2025-04-14"],
                model="claude-sonnet-4-5-20250929",
                system=system_prompt,
                messages=[{"role": "user", "content": "test"}]  # Minimal dummy message
                # tools excluded - contributes minimal overhead anyway
            )

            static_tokens = result.input_tokens
            logger.debug(f"Static context tokens: {static_tokens}")
            return static_tokens

        except Exception as e:
            logger.error(f"Static token counting failed: {e}")
            # Rough estimate: 1 token per 4 characters
            estimated = len(system_prompt) // 4 + len(str(tools)) // 4
            logger.debug(f"Using estimated static tokens: {estimated}")
            return estimated

    def _estimate_tokens(self) -> int:
        """
        Rough token estimation fallback.

        Uses simple heuristic: ~4 characters per token.

        Returns:
            Estimated token count
        """
        total_chars = 0

        for message in self.messages:
            content = message.get("content", "")

            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if block.get("type") == "text":
                        total_chars += len(block.get("text", ""))

        return total_chars // 4

    def invalidate_token_cache(self) -> None:
        """Force recount on next token check"""
        self.last_token_count_time = None
        logger.debug("Token cache invalidated")

    # ============================================================================
    # Active Skills Management (v0.5.0 Progressive Disclosure)
    # ============================================================================

    def set_active_skills(self, skill_names: List[str], max_skills: int = 2) -> None:
        """
        Set currently active skills for this conversation.

        Args:
            skill_names: List of skill names to activate
            max_skills: Maximum skills allowed (default 2)
        """
        self.active_skills = skill_names[:max_skills]
        self.last_updated = datetime.utcnow()
        logger.debug(f"Active skills set: {self.active_skills}")

    def add_active_skill(self, skill_name: str, max_skills: int = 2) -> bool:
        """
        Add a skill to active skills list.

        If at max capacity, returns False (caller should handle replacement).

        Args:
            skill_name: Skill name to add
            max_skills: Maximum skills allowed

        Returns:
            True if added, False if at capacity
        """
        if skill_name in self.active_skills:
            return True  # Already active

        if len(self.active_skills) >= max_skills:
            return False  # At capacity

        self.active_skills.append(skill_name)
        self.last_updated = datetime.utcnow()
        logger.debug(f"Added skill: {skill_name}. Active: {self.active_skills}")
        return True

    def replace_active_skill(self, old_skill: str, new_skill: str) -> bool:
        """
        Replace an active skill with a new one.

        Args:
            old_skill: Skill to remove
            new_skill: Skill to add

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

    def update_token_cache(self, tokens: int) -> None:
        """
        Update cached token count without API call.

        Args:
            tokens: New token count
        """
        self.conversation_tokens = tokens
        self.last_token_count_time = datetime.utcnow()
        logger.debug(f"Token cache updated: {tokens}")

    def get_remaining_token_budget(self, static_tokens: int) -> int:
        """
        Calculate remaining token budget for conversation.

        Args:
            static_tokens: Tokens used by system prompt + tools

        Returns:
            Remaining tokens available for conversation content
        """
        return self.max_tokens - static_tokens - self.conversation_tokens

    def should_warn_about_tokens(self) -> bool:
        """Check if at warning threshold (95%)"""
        return self.get_token_usage_percent() >= 95.0

    def get_documents_to_strip_for_budget(
        self,
        target_tokens: int
    ) -> List[Tuple[int, List[str]]]:
        """
        Identify which documents to strip to reach target token count.

        Works backwards from oldest messages, identifying documents
        that can be stripped to reduce token usage.

        Args:
            target_tokens: Target token count to achieve

        Returns:
            List of (message_index, attachment_ids) tuples to strip
        """
        to_strip = []
        current_tokens = self.conversation_tokens

        # Work through messages oldest-first
        for i in range(len(self.messages)):
            if current_tokens <= target_tokens:
                break

            message_idx_str = str(i)
            if message_idx_str in self.document_references:
                attachment_ids = self.document_references[message_idx_str]
                to_strip.append((i, attachment_ids))

                # Estimate token savings (rough: assume 1000 tokens per document)
                estimated_savings = len(attachment_ids) * 1000
                current_tokens -= estimated_savings

        return to_strip

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize to dictionary for database storage.

        Returns:
            Dictionary representation
        """
        # Serialize context_items (v0.5.0 Phase 1)
        context_items_serialized = [
            {
                "type": item.type,
                "message_index": item.message_index,
                "estimated_tokens": item.estimated_tokens,
                "item_id": item.item_id,
                "is_excluded_tool": item.is_excluded_tool,
                "metadata": item.metadata
            }
            for item in self.context_items
        ]

        return {
            "channel_id": self.channel_id,
            "max_messages": self.max_messages,
            "max_tokens": self.max_tokens,
            "messages": self.messages,
            "conversation_tokens": self.conversation_tokens,
            "document_references": self.document_references,
            "context_items": context_items_serialized,  # v0.5.0 Phase 1
            "active_skills": self.active_skills,  # v0.5.0 Progressive Disclosure
            "messages_removed": self.messages_removed,
            "documents_stripped": self.documents_stripped,
            "last_updated": self.last_updated.isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationState":
        """
        Deserialize from dictionary.

        Args:
            data: Dictionary from database

        Returns:
            ConversationState instance
        """
        state = cls(
            channel_id=data["channel_id"],
            max_messages=data["max_messages"],
            max_tokens=data["max_tokens"]
        )

        state.messages = data["messages"]

        # Backward compatibility: Add message_type to legacy messages without it
        for msg in state.messages:
            if "message_type" not in msg:
                # Default legacy messages to Discord type based on role
                msg["message_type"] = f"discord_{msg['role']}"
                logger.debug(f"Added default message_type to legacy message: {msg['message_type']}")

        state.conversation_tokens = data.get("conversation_tokens", 0)
        state.document_references = data.get("document_references", {})
        state.active_skills = data.get("active_skills", [])  # v0.5.0 Progressive Disclosure
        state.messages_removed = data.get("messages_removed", 0)
        state.documents_stripped = data.get("documents_stripped", 0)

        # Deserialize context_items (v0.5.0 Phase 1)
        context_items_data = data.get("context_items", [])
        state.context_items = [
            ContextItem(
                type=item_data["type"],
                message_index=item_data["message_index"],
                estimated_tokens=item_data["estimated_tokens"],
                item_id=item_data["item_id"],
                is_excluded_tool=item_data.get("is_excluded_tool", False),
                metadata=item_data.get("metadata")
            )
            for item_data in context_items_data
        ]

        if "last_updated" in data:
            state.last_updated = datetime.fromisoformat(data["last_updated"])

        return state

    def __repr__(self) -> str:
        return (
            f"ConversationState(channel={self.channel_id}, "
            f"messages={len(self.messages)}/{self.max_messages}, "
            f"tokens={self.conversation_tokens}/{self.max_tokens}, "
            f"usage={self.get_token_usage_percent():.1f}%)"
        )
