"""
Context Builder - Smart Assembly for Claude API

Builds rich context from Discord messages with:
- @mention resolution to display names
- Reply chain threading
- Recent message history
- Memory system integration
- Image processing
"""

import discord
import logging
import re
import pytz
from datetime import datetime
from typing import Optional, List, Dict, Any, TYPE_CHECKING
import sys
import os

# Add tools directory for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

if TYPE_CHECKING:
    from .config import BotConfig
    from .message_memory import MessageMemory
    from .memory_manager import MemoryManager
    from .data_isolation import DataIsolationEnforcer
    from .unified_attachment_manager import UnifiedAttachmentManager
    from .skills_manager import SkillsManager

from tools.image_processor import ImageProcessor
from tools.skills_tool import build_skills_catalog_prompt
from .internal_constants import MANDATORY_RESPONSE_JUDGMENT_PROMPT, WEB_SEARCH_DISABLED_PROMPT

logger = logging.getLogger(__name__)


class ContextBuilder:
    """
    Assembles rich context for Claude API calls.

    Resolves @mentions, threads reply chains, integrates memory paths,
    and processes images into multimodal content blocks.
    """

    def __init__(
        self,
        config: "BotConfig",
        message_memory: "MessageMemory",
        memory_manager: "MemoryManager",
        data_isolation: Optional["DataIsolationEnforcer"] = None,
        attachment_manager: Optional["UnifiedAttachmentManager"] = None,
        skills_manager: Optional["SkillsManager"] = None
    ):
        self.config = config
        self.message_memory = message_memory
        self.memory_manager = memory_manager
        self.data_isolation = data_isolation
        self.attachment_manager = attachment_manager
        self.skills_manager = skills_manager
        self._mention_names: Dict[int, str] = {}  # id -> display name memo
        self.image_processor = ImageProcessor()

        logger.info(f"ContextBuilder initialized for bot '{config.bot_id}'")

    def build_skills_prompt(self, active_skills: List[str]) -> str:
        """
        Build the skills catalog section for system prompt.

        Enables progressive disclosure - Claude sees all available skills
        and knows which ones are currently loaded.

        Args:
            active_skills: List of currently active skill names

        Returns:
            Formatted XML section for system prompt, or empty string if no skills_manager
        """
        if not self.skills_manager:
            return ""

        return build_skills_catalog_prompt(self.skills_manager, active_skills)

    @staticmethod
    def _trim_episode_index(state_content: str, keep_last: int = None) -> str:
        """Keep only the tail of the episode index when inlining the state file."""
        from core.internal_constants import EPISODE_INDEX_SEED_TAIL
        keep_last = keep_last or EPISODE_INDEX_SEED_TAIL
        parts = state_content.split("\n## Episode Index")
        if len(parts) < 2:
            return state_content
        index_lines = [l for l in parts[1].splitlines() if l.strip().startswith("- ")]
        trimmed = index_lines[-keep_last:]
        omitted = len(index_lines) - len(trimmed)
        header = "\n## Episode Index"
        if omitted > 0:
            header += f"\n- ({omitted} older episode(s) omitted - episode files cover them)"
        return parts[0] + header + "\n" + "\n".join(trimmed) + "\n"

    async def build_context(self, message: discord.Message, exclude_message_ids: List[int] = None) -> dict:
        """
        Build context dict for Claude API call.

        Returns dict with system_prompt, messages array, and stats.
        Optionally excludes specific message IDs (e.g., filtering in-flight messages).
        """
        # Track stats for logging
        stats = {
            "mentions_resolved": 0,
            "reply_chain_length": 0,
            "recent_messages": 0,
            "reactions_found": 0,
            "attachments_processed": 0
        }

        # Get bot's Discord display name
        bot_display_name = "Assistant"
        if message.guild and message.guild.me:
            bot_display_name = message.guild.me.display_name

        # Build date/time awareness context in server timezone
        server_tz = pytz.timezone(self.config.discord.timezone)
        current_time_obj = datetime.utcnow().replace(tzinfo=pytz.UTC).astimezone(server_tz)
        current_time = current_time_obj.strftime('%Y-%m-%d %H:%M %Z')
        date_context = f"Current server date/time: {current_time}"

        # Get base personality prompt
        base_prompt = (
            self.config.personality.base_prompt
            if self.config.personality
            else "You are a helpful Discord bot assistant."
        )

        # Build follow-up instructions if enabled
        followup_instructions = ""
        if self.config.agentic and self.config.agentic.followups.enabled:
            server_id = str(message.guild.id) if message.guild else None
            if server_id:
                followups_path = self.memory_manager.get_followups_path(server_id)
                current_user_id = str(message.author.id)
                current_user_name = message.author.display_name
                current_channel_id = str(message.channel.id)

                followup_instructions = f"""

# Follow-Up System

When people mention future events, use your judgment to decide if a follow-up would be helpful or engaging. Create follow-ups when checking in later would be natural and valuable.

To create a follow-up, use the memory tool to write to: {followups_path}

**Current Context:**
- Current user: {current_user_name} (ID: {current_user_id})
- Current channel ID: {current_channel_id}

Format (JSON):
{{
  "pending": [
    {{
      "id": "unique-id-<YYYYMMDD-HHMM>",
      "user_id": "{current_user_id}",
      "user_name": "{current_user_name}",
      "channel_id": "{current_channel_id}",
      "event": "<brief description>",
      "context": "<relevant context>",
      "mentioned_date": "<current server date/time>",
      "follow_up_after": "<ISO 8601 datetime>",
      "priority": "low|medium|high"
    }}
  ],
  "completed": []
}}

NOTE: The user_id MUST be the numeric Discord user ID (like {current_user_id}), NOT the display name.

**When to create follow-ups:**

Use social intelligence to decide when a follow-up would be natural and valuable. Would a thoughtful friend check in about this later?

Good candidates:
- Personal events (appointments, interviews, exams, presentations)
- Group activities (game nights, watch parties, meetups)
- Anticipated releases or events multiple people care about
- Projects and deadlines
- Life changes (moves, trips, new jobs)

Skip follow-ups for:
- Vague mentions without clear timeframes
- Recurring/routine events
- Past events
- When user explicitly declines

**Timing:** Use judgment based on the event. The system checks periodically, so schedule follow-ups for when it would be natural to check in.
"""

        # Add data isolation transparency (v0.5.0)
        isolation_transparency = ""
        if self.data_isolation:
            isolation_transparency = "\n" + self.data_isolation.get_transparency_message() + "\n"

        # Add web search disabled notice if applicable
        web_search_notice = ""
        if not self.config.api.web_search.enabled:
            web_search_notice = WEB_SEARCH_DISABLED_PROMPT

        # Assemble system prompt with XML structure (Phase 7 - v0.5.0)
        system_prompt = f"""<identity>
You are {bot_display_name}. Your Discord user ID is {message.guild.me.id if (message.guild and message.guild.me) else 'unknown'}.

NOTE: Users can set personal timezones with: !timezone [timezone]
User timezones are stored in their memory profiles.
NOTE: When users @mention you, it will appear as @{bot_display_name} in the message text.
</identity>

<context_awareness>
You are rejoining an ongoing channel. Your in-context history is a seed: the
recent tail of the conversation plus the channel state below. Earlier
conversation has been distilled into episode files.

If a reference is unclear, do NOT guess:
- Open the relevant episode file with the memory tool (paths in the episode index)
- Search message history with discord tools (search_messages / view_messages)
- Re-fetch attachments with get_attachment / code_execution if an analysis was cleared

Old tool results may appear as "[tool result cleared at turn boundary ...]" -
re-run the tool if you need that information again.

Write important findings and decisions to memory files: your in-context working
state does not survive the session boundary, memory files do.
</context_awareness>

<instructions>
{MANDATORY_RESPONSE_JUDGMENT_PROMPT}
{base_prompt}{followup_instructions}
{isolation_transparency}{web_search_notice}
IMPORTANT: In the conversation history below, messages marked "Assistant (you)" are YOUR OWN previous responses. Do not refer to them as if someone else said them. These are what you already said earlier in this conversation.

NOTE: Messages showing "[Forwarded message - content not accessible]" are forwards from other channels. You cannot see forwarded message content due to Discord API limitations.

NOTE: Messages showing "[YOU CAME ONLINE]" or "[YOU WENT OFFLINE]" are lifecycle events indicating when you were restarted or shutdown.

SEARCH METHODOLOGY:
1. Start broad (search_messages keywords only) → get message_ids
2. View context (view_messages mode='around' message_id) → see conversation
3. Triangulate: if wrong context, search related keywords from what you found
4. Track threads: view_messages shows adjacent messages - follow the chain
5. Find responses: search for keywords from questions you discovered
6. Narrow spatially: add channel_id if global search too noisy
7. Conclude not found: if direct keywords exhausted with no results, stop - tell user it's not accessible

Pattern: keyword → context → refine → adjacent → related → done

MEMORY USAGE: You have access to memory files for persistent information. Use them proactively to:
- Remember important facts about users and their preferences
- Track ongoing projects or topics of interest
- Save context that would be valuable in future conversations
- Build understanding of channel culture and dynamics

TOOL USAGE GUIDELINES (CRITICAL):
When you have access to the code_execution tool:
- For spreadsheets (xlsx, xlsm, csv, xls): ALWAYS use code_execution with pandas or openpyxl to read and analyze
- For binary documents (docx, pptx): Use code_execution to extract text content
- For large files marked as 'container_upload': Use code_execution to access at the documented path
- DO NOT rely on document preview/extraction for spreadsheets - it may be incomplete or miss formatting
- When asked to calculate, analyze data, or process file contents: USE code_execution first

Example for spreadsheet:
```python
import os, pandas as pd
df = pd.read_excel(os.path.join(os.environ.get('INPUT_DIR', '.'), 'filename.xlsx'))
print(df.describe())
```

DELIVERING FILES: files you place in $OUTPUT_DIR (the code-exec environment's
output directory) are attached to your Discord reply. To deliver a document,
finish with e.g.: cp /tmp/briefing.pptx "$OUTPUT_DIR"/briefing.pptx
Files left anywhere else are not attached. Never claim a file is attached
unless you put it in $OUTPUT_DIR.

The attachments_index section lists this channel's recent files with their IDs;
retrieve anything marked 'not in context' with the discord get_attachment tool.

CRITICAL: Do NOT narrate your thought process, explain your reasoning, or describe what you're about to do in your responses. Just respond naturally and directly. Your thinking is private - users only see your final response.
</instructions>"""

        # Inline the channel state file (episodizer-maintained seed) - v0.6.0
        server_id = str(message.guild.id) if message.guild else None
        if server_id:
            state_path = self.memory_manager.get_channel_context_path(server_id, str(message.channel.id))
            channel_state = await self.memory_manager.read(state_path)
            if channel_state:
                channel_state = self._trim_episode_index(channel_state)
                episodes_dir = self.memory_manager.get_episodes_dir_path(server_id, str(message.channel.id))
                system_prompt += (
                    f"\n\n<channel_state>\nEpisode files live in: {episodes_dir}/\n\n"
                    f"{channel_state}\n</channel_state>"
                )

        # Get recent messages (excluding current to avoid duplication)
        channel_id = str(message.channel.id)
        all_recent = await self.message_memory.get_recent(
            channel_id,
            limit=self.config.api.context_messages + 1,
            exclude_message_ids=exclude_message_ids
        )

        # Filter out current message (it was just saved to DB)
        recent_messages = [msg for msg in all_recent if msg.message_id != str(message.id)]

        # Trim to configured limit after filtering
        recent_messages = recent_messages[:self.config.api.context_messages]

        # Build messages array for Claude
        messages = []

        # Add recent message history as context
        if recent_messages:
            stats["recent_messages"] = len(recent_messages)
            history_parts = []
            history_parts.append("# Recent Conversation History")
            history_parts.append("")

            # Only THIS bot's messages are "Assistant (you)" - other bots in
            # the channel are participants, not the assistant (multi-bot)
            own_id = str(message.guild.me.id) if message.guild else None

            # Check if current message is a reply
            reply_chain = await self._get_reply_chain(message)
            if reply_chain:
                stats["reply_chain_length"] = len(reply_chain)
                history_parts.append("## Reply Chain (Oldest to Newest)")
                history_parts.append("")
                for msg in reply_chain:
                    resolved_content, resolved_count = await self._resolve_mentions(msg.content, message.guild)
                    stats["mentions_resolved"] += resolved_count
                    if str(msg.author.id) == own_id or (own_id is None and msg.author.bot):
                        author_display = "Assistant (you)"
                    else:
                        author_display = msg.author.display_name
                    timestamp_str = msg.created_at.strftime('%H:%M')
                    history_parts.append(f"[{timestamp_str}] **{author_display}**: {resolved_content}")
                history_parts.append("")
                history_parts.append("## Recent Messages")
                history_parts.append("")

            # Add recent messages
            for msg in recent_messages:
                # Clarify bot's own messages vs user messages
                if str(msg.author_id) == own_id or (own_id is None and msg.is_bot):
                    author_display = "Assistant (you)"
                else:
                    author_display = msg.author_name

                # Resolve mentions
                resolved_content, resolved_count = await self._resolve_mentions(msg.content, message.guild)
                stats["mentions_resolved"] += resolved_count

                # Check for attachments if attachment manager is available
                attachment_info = ""
                if self.attachment_manager:
                    try:
                        async with self.attachment_manager.attachment_db.db.execute(
                            "SELECT attachment_id, filename FROM attachments WHERE message_id = ?",
                            (str(msg.message_id),)
                        ) as cursor:
                            attachment_rows = await cursor.fetchall()
                            if attachment_rows:
                                attachment_strs = [f"{row['filename']} (ID: {row['attachment_id']})" for row in attachment_rows]
                                attachment_info = f" [Attachments: {', '.join(attachment_strs)}; use get_attachment to retrieve]"
                    except Exception as e:
                        logger.debug(f"Failed to query attachments for message {msg.message_id}: {e}")

                timestamp_str = msg.timestamp.strftime('%H:%M')
                history_parts.append(f"[{timestamp_str}] **{author_display}**: {resolved_content}{attachment_info}")

            history_parts.append("")

            # Add current message with context
            resolved_content, resolved_count = await self._resolve_mentions(message.content, message.guild)
            stats["mentions_resolved"] += resolved_count

            message_with_context, has_reactions = self._format_message_with_context(
                author=message.author.display_name,
                content=resolved_content,
                message=message
            )
            if has_reactions:
                stats["reactions_found"] += 1
            history_parts.append(message_with_context)

            # Add memory context paths
            if message.guild:
                server_id = str(message.guild.id)
                user_ids = [str(message.author.id)]

                # Add unique user IDs from recent messages
                for msg in recent_messages[-5:]:  # Last few messages
                    if msg.author_id not in user_ids:
                        user_ids.append(msg.author_id)

                memory_context = self.memory_manager.build_memory_context(
                    server_id, channel_id, user_ids
                )
                history_parts.append("")
                history_parts.append(memory_context)

            # Process current message attachments
            attachments = await self.process_attachments(message)

            # Process replied-to message attachments (retroactive processing)
            replied_attachments = []
            if message.reference and message.reference.message_id:
                replied_attachments = await self._get_attachment_data(str(message.reference.message_id))
                if replied_attachments:
                    logger.info(f"Retrieved {len(replied_attachments)} attachments from replied-to message")

            # Combine all attachments
            all_attachments = replied_attachments + attachments
            if all_attachments:
                stats["attachments_processed"] = len(all_attachments)
                # Content becomes array with text and attachments
                content_parts = [{"type": "text", "text": "\n".join(history_parts)}]
                content_parts.extend(all_attachments)
                messages.append(
                    {"role": "user", "content": content_parts}
                )
            else:
                # No attachments, use string content
                messages.append(
                    {"role": "user", "content": "\n".join(history_parts)}
                )

        else:
            # No history, just current message with context
            resolved_content, resolved_count = await self._resolve_mentions(message.content, message.guild)
            stats["mentions_resolved"] += resolved_count

            message_with_context, has_reactions = self._format_message_with_context(
                author=message.author.display_name,
                content=resolved_content,
                message=message
            )
            if has_reactions:
                stats["reactions_found"] += 1

            # Process current message attachments
            attachments = await self.process_attachments(message)

            # Process replied-to message attachments (retroactive processing)
            replied_attachments = []
            if message.reference and message.reference.message_id:
                replied_attachments = await self._get_attachment_data(str(message.reference.message_id))
                if replied_attachments:
                    logger.info(f"Retrieved {len(replied_attachments)} attachments from replied-to message")

            # Combine all attachments
            all_attachments = replied_attachments + attachments
            if all_attachments:
                stats["attachments_processed"] = len(all_attachments)
                content_parts = [{"type": "text", "text": message_with_context}]
                content_parts.extend(all_attachments)
                messages.append(
                    {"role": "user", "content": content_parts}
                )
            else:
                messages.append(
                    {"role": "user", "content": message_with_context}
                )

        # Time rides in a separate uncached system block (v0.6.0 Phase 5):
        # a timestamp inside the cached prefix busted the prompt cache every minute
        return {
            "system_prompt": system_prompt,
            "time_context": date_context,
            "messages": messages,
            "stats": stats,
        }

    async def _resolve_mentions(self, content: str, guild: Optional[discord.Guild]) -> tuple[str, int]:
        """
        Resolve @mentions to display names.

        Returns tuple of (resolved content, count of mentions resolved).
        """
        if not guild:
            return content, 0

        # Pattern for user mentions: <@123456789> or <@!123456789>
        mention_pattern = re.compile(r'<@!?(\d+)>')
        mentions_found = mention_pattern.findall(content)

        if not mentions_found:
            return content, 0

        resolved_count = 0

        async def replace_mention(match):
            nonlocal resolved_count
            user_id = int(match.group(1))
            # Memo first: context builds re-resolve the same ids constantly,
            # and per-mention fetch_member calls were hitting 429s (the bot
            # then silently showed raw ids to the model)
            cached = self._mention_names.get(user_id)
            if cached:
                resolved_count += 1
                return f"@{cached} (<@{user_id}>)"
            # Local member cache (members intent is enabled) - no API call
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except (discord.NotFound, discord.HTTPException):
                    # Keep original mention if user not found
                    return match.group(0)
            self._mention_names[user_id] = member.display_name
            resolved_count += 1
            # Name for understanding, raw form so the model can emit working
            # mentions by copying it
            return f"@{member.display_name} (<@{user_id}>)"

        # Replace all mentions
        resolved = content
        for match in mention_pattern.finditer(content):
            replacement = await replace_mention(match)
            resolved = resolved.replace(match.group(0), replacement, 1)

        return resolved, resolved_count

    async def _get_attachment_data(self, message_id: str) -> List[Dict]:
        """
        Retrieve attachment data for a message from the database.

        Queries database for cached file_id and local_path to avoid re-uploading.
        Falls back to retroactive processing only if database has no records.
        Returns list of content blocks ready for Claude API.
        """
        if not self.attachment_manager:
            return []

        attachment_blocks = []

        try:
            # Query for complete attachment metadata (including cached file_id)
            async with self.attachment_manager.attachment_db.db.execute(
                """SELECT attachment_id, filename, attachment_type, file_id, local_path,
                          processed_base64, processed_mime
                   FROM attachments WHERE message_id = ?""",
                (message_id,)
            ) as cursor:
                rows = await cursor.fetchall()

            # If no cached data, fall back to retroactive processing
            if not rows:
                logger.debug(f"No cached attachments for message {message_id}, using retroactive processing")
                return await self._retroactive_attachment_processing(message_id)

            # Use cached data from database
            for row in rows:
                attachment_id, filename, att_type, file_id, local_path, cached_base64, cached_mime = row

                try:
                    if att_type == "image":
                        # Check for cached base64 first
                        if cached_base64 and cached_mime:
                            attachment_blocks.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": cached_mime,
                                    "data": cached_base64
                                }
                            })
                            logger.debug(f"Loaded cached image from DB: {filename}")
                        elif local_path:
                            # Load from local storage
                            import base64
                            file_bytes = await self.attachment_manager.local_storage.load(local_path)
                            base64_data = base64.standard_b64encode(file_bytes).decode('utf-8')

                            # Determine media type from filename
                            media_type = self._get_media_type_from_filename(filename)

                            attachment_blocks.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": base64_data
                                }
                            })
                            logger.debug(f"Loaded image from local storage: {filename}")
                        else:
                            # Fall back to retroactive for this attachment
                            logger.warning(f"No cached data for image {filename}, using retroactive")
                            fallback_data = await self._process_single_attachment_fallback(attachment_id, filename)
                            if fallback_data:
                                attachment_blocks.append(fallback_data)

                    elif att_type in ("document", "code"):
                        # Use cached file_id (NO re-upload!)
                        if file_id:
                            attachment_blocks.append({
                                "type": "document",
                                "source": {
                                    "type": "file",
                                    "file_id": file_id
                                }
                            })
                            logger.debug(f"Loaded document from DB cache: {filename} ({file_id})")
                        else:
                            # Fall back to retroactive for this attachment
                            logger.warning(f"No file_id for document {filename}, using retroactive")
                            fallback_data = await self._process_single_attachment_fallback(attachment_id, filename)
                            if fallback_data:
                                attachment_blocks.append(fallback_data)

                except Exception as e:
                    logger.error(f"Failed to load cached attachment {filename}: {e}")
                    # Add placeholder
                    attachment_blocks.append({
                        "type": "text",
                        "text": f"\n[Attachment: {filename} - no longer available]"
                    })

        except Exception as e:
            logger.error(f"Failed to query attachments for message {message_id}: {e}")

        return attachment_blocks

    async def _retroactive_attachment_processing(self, message_id: str) -> List[Dict]:
        """
        Fallback: Use retroactive processing when database has no cached data.

        This maintains backwards compatibility for messages processed before caching was added.
        """
        attachment_blocks = []

        try:
            async with self.attachment_manager.attachment_db.db.execute(
                "SELECT attachment_id, filename FROM attachments WHERE message_id = ?",
                (message_id,)
            ) as cursor:
                rows = await cursor.fetchall()

            for row in rows:
                attachment_id, filename = row
                block = await self._process_single_attachment_fallback(attachment_id, filename)
                if block:
                    attachment_blocks.append(block)

        except Exception as e:
            logger.error(f"Retroactive processing failed for message {message_id}: {e}")

        return attachment_blocks

    async def _process_single_attachment_fallback(self, attachment_id: str, filename: str):
        """
        Process a single attachment via retroactive processing fallback.

        Returns a content block dict or None if processing fails.
        """
        try:
            file_data = await self.attachment_manager.get_attachment_for_processing(attachment_id)

            if file_data:
                if file_data["method"] == "base64":
                    logger.debug(f"Retrieved image {filename} via retroactive processing")
                    return {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": file_data["media_type"],
                            "data": file_data["data"]
                        }
                    }
                elif file_data["method"] == "file_id":
                    if file_data.get("use_as_document_block", True):
                        logger.debug(f"Retrieved document {filename} via retroactive processing")
                        return {
                            "type": "document",
                            "source": {
                                "type": "file",
                                "file_id": file_data["data"]
                            }
                        }
                    else:
                        logger.debug(f"Retrieved code file {filename} via retroactive processing")
                        return {
                            "type": "text",
                            "text": f"\n[Attached file: {filename} - available to code execution]"
                        }
        except Exception as e:
            logger.warning(f"Retroactive processing failed for {filename}: {e}")
            return {
                "type": "text",
                "text": f"\n[Attachment: {filename} - no longer available]"
            }

        return None

    @staticmethod
    def _get_media_type_from_filename(filename: str) -> str:
        """Determine media type from file extension."""
        ext = filename.lower().split('.')[-1] if '.' in filename else ''
        media_types = {
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'webp': 'image/webp'
        }
        return media_types.get(ext, 'image/jpeg')

    async def _get_reply_chain(self, message: discord.Message) -> List[discord.Message]:
        """
        Follow reply chain backwards to build context.

        Limited depth to prevent excessive context bloat.
        Returns oldest-first order, empty list if not a reply.
        """
        if not message.reference:
            return []

        chain = []
        current = message

        # Follow chain backwards (limited depth to prevent context bloat)
        for _ in range(5):
            if not current.reference:
                break

            try:
                replied_to = await current.channel.fetch_message(current.reference.message_id)
                chain.append(replied_to)
                current = replied_to
            except (discord.NotFound, discord.HTTPException) as e:
                logger.debug(f"Could not fetch reply chain message: {e}")
                break

        # Reverse to get oldest-first order
        chain.reverse()

        logger.debug(f"Built reply chain with {len(chain)} messages")
        return chain

    def _format_message_with_context(
        self, author: str, content: str, message: discord.Message
    ) -> tuple[str, bool]:
        """
        Format message with context (timestamp, reactions, reply info).

        Returns tuple of (formatted string, has_reactions boolean).
        """
        timestamp_str = message.created_at.strftime('%H:%M')
        parts = [f"[{timestamp_str}] **{author}**: {content}"]
        has_reactions = False

        # Add reaction information
        if message.reactions:
            reaction_strs = []
            for reaction in message.reactions:
                emoji_str = str(reaction.emoji)
                count = reaction.count
                reaction_strs.append(f"{emoji_str}×{count}")

            if reaction_strs:
                has_reactions = True
                parts.append(f"  *(Reactions: {', '.join(reaction_strs)})*")

        # Add reply information
        if message.reference and message.reference.resolved:
            replied_msg = message.reference.resolved
            if isinstance(replied_msg, discord.Message):
                own = message.guild.me if message.guild else None
                if own is not None:
                    is_self = replied_msg.author.id == own.id
                else:
                    is_self = replied_msg.author.bot
                replied_author = "Assistant (you)" if is_self else replied_msg.author.display_name
                # Truncate long replied content
                truncate_length = 100
                replied_content = replied_msg.content[:truncate_length]
                if len(replied_msg.content) > truncate_length:
                    replied_content += "..."
                parts.append(f"  *(Replying to {replied_author}: \"{replied_content}\")*")

        return "\n".join(parts), has_reactions

    async def process_attachments(self, message: discord.Message) -> List[Dict]:
        """
        Process all attachments (images, documents, spreadsheets, etc.) into Claude API format.

        Handles both base64 (images) and file_id (documents) formats.
        Limits attachments to API maximum.
        Returns list of processed attachment content blocks.
        """
        if not message.attachments:
            return []

        # Limit attachments to API maximum
        api_limit = 20
        attachments_to_process = message.attachments[:api_limit]
        if len(message.attachments) > api_limit:
            logger.warning(f"Message has {len(message.attachments)} attachments, limiting to {api_limit}")

        # Process each attachment
        processed_attachments = []
        for attachment in attachments_to_process:
            try:
                if self.attachment_manager:
                    # Use attachment manager (stores in database + processes)
                    result = await self.attachment_manager.process_attachment(
                        attachment=attachment,
                        message=message,
                        is_realtime=True
                    )

                    if result and result.get("for_api"):
                        api_data = result["for_api"]

                        if api_data["method"] == "base64":
                            # Image: base64 format
                            processed_attachments.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": api_data["media_type"],
                                    "data": api_data["data"]
                                }
                            })
                            logger.info(f"Processed image via attachment manager: {attachment.filename}")

                        elif api_data["method"] == "file_id":
                            # Files API: check if should be document block or text mention
                            if api_data.get("use_as_document_block", True):
                                # PDF/plaintext: add as document content block for direct viewing
                                processed_attachments.append({
                                    "type": "document",
                                    "source": {
                                        "type": "file",
                                        "file_id": api_data["data"]
                                    }
                                })
                                logger.info(f"Processed document block: {attachment.filename}")
                            else:
                                # Code execution files: container_upload block
                                # Bug #25 fix: Use container_upload instead of text mention
                                processed_attachments.append({
                                    "type": "container_upload",
                                    "file_id": api_data['data']
                                })
                                logger.info(f"Processed code execution file: {attachment.filename} (file_id: {api_data['data']})")

                else:
                    # Fallback: use ImageProcessor directly (backward compatibility, images only)
                    processed = await self.image_processor.process_attachment(attachment)
                    if processed:
                        processed_attachments.append(processed)
                        logger.info(f"Processed image via ImageProcessor fallback: {attachment.filename}")

            except Exception as e:
                logger.error(f"Failed to process attachment {attachment.filename}: {e}")

        return processed_attachments
