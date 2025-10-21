"""
Conversation Logger - Human-readable conversation tracking

Logs Discord conversations in minimalistic, parseable format.
"""

import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class ConversationLogger:
    """
    Logs conversations in simple, readable format.

    Example:
    === 2025-10-03 03:15:42 | #general | charlesthefool ===
    [@MENTION] hello there!

    [DECISION] Respond: YES (mention detected)
    [RATE_LIMIT] 5min: 3/20, 1hr: 15/200, ignored: 0/5

    --- BOT RESPONSE (234 chars) ---
    Hello! How can I help you today?

    [ENGAGEMENT] Tracking started (30s delay)
    ============================================================
    """

    def __init__(self, bot_id: str, log_dir: Path):
        self.bot_id = bot_id
        self.log_file = log_dir / f"{bot_id}_conversations.log"
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"ConversationLogger initialized: {self.log_file}")

    def log_user_message(
        self,
        author: str,
        channel: str,
        content: str,
        is_mention: bool = False
    ):
        """Log incoming user message"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mention_marker = "[@MENTION] " if is_mention else ""

        entry = f"\n{'='*60}\n"
        entry += f"=== {timestamp} | #{channel} | {author} ===\n"
        entry += f"{mention_marker}{content}\n"

        self._write(entry)

    def log_decision(
        self,
        should_respond: bool,
        reason: str,
        rate_limit_stats: Optional[dict] = None
    ):
        """Log bot decision about responding"""
        entry = f"\n[DECISION] Respond: {'YES' if should_respond else 'NO'} ({reason})\n"

        if rate_limit_stats:
            entry += (
                f"[RATE_LIMIT] "
                f"5min: {rate_limit_stats['responses_5min']}/{rate_limit_stats['limits']['short_window'].split('/')[1]}, "
                f"1hr: {rate_limit_stats['responses_1hr']}/{rate_limit_stats['limits']['long_window'].split('/')[1]}, "
                f"ignored: {rate_limit_stats['ignored_count']}/{rate_limit_stats.get('ignore_threshold', 5)}"
            )
            if rate_limit_stats.get('is_silenced'):
                entry += " [SILENCED]"
            entry += "\n"

        self._write(entry)

    def log_thinking(self, thinking: str, token_count: int):
        """Log bot's thinking trace"""
        entry = f"\n[THINKING] ({token_count} tokens)\n{thinking}\n"
        self._write(entry)

    def log_bot_response(self, content: str, char_count: int):
        """Log bot's response"""
        entry = f"\n--- BOT RESPONSE ({char_count} chars) ---\n{content}\n"
        self._write(entry)

    def log_engagement_tracking(self, started: bool = True, delay_seconds: Optional[int] = None):
        """Log engagement tracking status"""
        if started:
            if delay_seconds:
                delay_str = f"{delay_seconds // 60}min" if delay_seconds >= 60 else f"{delay_seconds}s"
                entry = f"\n[ENGAGEMENT] Tracking started ({delay_str} delay)\n"
            else:
                entry = "\n[ENGAGEMENT] Tracking started\n"
        else:
            entry = "\n[ENGAGEMENT] Result recorded\n"

        self._write(entry)

    def log_engagement_result(self, engaged: bool, method: Optional[str] = None):
        """Log engagement tracking result"""
        if engaged:
            entry = f"\n[ENGAGEMENT] ENGAGED ({method})\n"
        else:
            entry = "\n[ENGAGEMENT] IGNORED\n"

        self._write(entry)

    def log_error(self, error: str):
        """Log error during processing"""
        entry = f"\n[ERROR] {error}\n"
        self._write(entry)

    def log_memory_tool(self, command: str, path: str, result_preview: str):
        """Log memory tool operation"""
        entry = f"\n[MEMORY_TOOL] {command.upper()} {path}\n"
        entry += f"  Result: {result_preview[:100]}"
        if len(result_preview) > 100:
            entry += "..."
        entry += "\n"
        self._write(entry)

    def log_tool_use_loop(self, iteration: int, stop_reason: str):
        """Log tool use loop iteration"""
        entry = f"\n[TOOL_LOOP] Iteration {iteration}: {stop_reason}\n"
        self._write(entry)

    def log_context_building(
        self,
        mentions_resolved: int = 0,
        reply_chain_length: int = 0,
        recent_messages: int = 0,
        reactions_found: int = 0,
        images_processed: int = 0
    ):
        """Log context building details"""
        entry = "\n[CONTEXT] Building context:\n"
        if mentions_resolved > 0:
            entry += f"  - Resolved {mentions_resolved} @mention(s)\n"
        if reply_chain_length > 0:
            entry += f"  - Reply chain: {reply_chain_length} message(s)\n"
        if recent_messages > 0:
            entry += f"  - Recent history: {recent_messages} message(s)\n"
        if reactions_found > 0:
            entry += f"  - Found reactions on {reactions_found} message(s)\n"
        if images_processed > 0:
            entry += f"  - Processed {images_processed} image(s)\n"
        self._write(entry)

    def log_cache_status(self, enabled: bool, cache_hit: bool = False):
        """Log prompt caching status"""
        if enabled:
            status = "HIT" if cache_hit else "ENABLED"
            entry = f"\n[CACHE] Prompt caching: {status}\n"
        else:
            entry = "\n[CACHE] Prompt caching: DISABLED\n"
        self._write(entry)

    def log_context_management(
        self,
        tool_uses_cleared: int,
        tokens_cleared: int,
        original_tokens: int
    ):
        """Log context management (context editing) statistics"""
        if tool_uses_cleared > 0:
            cleared_pct = (tokens_cleared / original_tokens * 100) if original_tokens > 0 else 0
            entry = f"\n[CONTEXT_MGMT] Cleared {tool_uses_cleared} tool use(s), "
            entry += f"{tokens_cleared:,} tokens ({cleared_pct:.1f}% of {original_tokens:,})\n"
            self._write(entry)

    def log_separator(self):
        """Log conversation separator"""
        entry = f"{'='*60}\n"
        self._write(entry)

    def _write(self, content: str):
        """Write to log file"""
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            logger.error(f"Error writing to conversation log: {e}")
