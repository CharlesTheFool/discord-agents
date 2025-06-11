"""
Context Manager for Discord Bot
Manages comprehensive contextual awareness using server summaries.
"""

from pathlib import Path
from typing import Dict, List, Optional
import discord


class ContextManager:
    """Manages comprehensive contextual awareness for Discord bot."""
    
    def __init__(self, summaries_dir: str = "server_summaries"):
        self.summaries_dir = Path(summaries_dir)
        self.context_cache = {}
        
    def get_comprehensive_context(self, message: discord.Message, recent_messages: List[dict]) -> str:
        """
        Get comprehensive contextual information including all summaries and recent chat history.
        
        Args:
            message: Discord message object
            recent_messages: Last 500 messages from current channel
            
        Returns:
            Formatted context string for Claude with prompt caching markers
        """
        context_parts = []
        
        # ALL USER PROFILES (cacheable)
        all_user_context = self._get_all_user_profiles()
        if all_user_context:
            context_parts.append("## ALL SERVER MEMBERS")
            context_parts.append(all_user_context)
        
        # ALL CHANNEL SUMMARIES (cacheable)
        all_channel_context = self._get_all_channel_summaries()
        if all_channel_context:
            context_parts.append("## ALL CHANNEL SUMMARIES")
            context_parts.append(all_channel_context)
        
        # RECENT CHANNEL HISTORY (not cacheable - changes frequently)
        if recent_messages:
            formatted_history = self._format_recent_messages(recent_messages)
            context_parts.append("## RECENT CHANNEL HISTORY (Last 500 messages)")
            context_parts.append(formatted_history)
        
        return "\n\n".join(context_parts)
    
    def _get_all_user_profiles(self) -> Optional[str]:
        """Get all user profiles for comprehensive context."""
        user_profiles = []
        
        # Find all user profile files
        for profile_file in self.summaries_dir.glob("user_*.md"):
            try:
                with open(profile_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                user_profiles.append(content.strip())
            except Exception as e:
                print(f"Error reading {profile_file}: {e}")
                continue
        
        return "\n\n---\n\n".join(user_profiles) if user_profiles else None
    
    def _get_all_channel_summaries(self) -> Optional[str]:
        """Get all channel summaries for comprehensive context."""
        channel_summaries = []
        
        # Find all channel summary files
        for summary_file in self.summaries_dir.glob("channel_*.md"):
            try:
                with open(summary_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                channel_summaries.append(content.strip())
            except Exception as e:
                print(f"Error reading {summary_file}: {e}")
                continue
        
        return "\n\n---\n\n".join(channel_summaries) if channel_summaries else None
    
    def _format_recent_messages(self, messages: List[dict]) -> str:
        """Format recent messages for context."""
        formatted_messages = []
        
        for msg in messages:
            # Format timestamp nicely
            timestamp = msg.get('timestamp_iso', msg.get('timestamp', ''))
            if hasattr(timestamp, 'isoformat'):
                timestamp = timestamp.isoformat()
            
            # Extract date and time
            date_time = timestamp[:16] if timestamp else "unknown"
            
            # Mark bot messages clearly
            author_name = msg['author']
            if msg.get('is_bot', False):
                author_name = f"SLH-01 (YOU)"
            
            # Format message
            formatted_msg = f"[{date_time}] {author_name}: {msg['content']}"
            formatted_messages.append(formatted_msg)
        
        return "\n".join(formatted_messages)
    
    def refresh_cache(self):
        """Clear context cache to force reload of summaries."""
        self.context_cache.clear()
    
    def get_available_contexts(self) -> Dict[str, List[str]]:
        """Get list of available context files for debugging."""
        available = {
            'user_profiles': [],
            'channel_summaries': []
        }
        
        for context_file in self.summaries_dir.glob("*.md"):
            filename = context_file.stem
            
            if filename.startswith("user_") and not filename.startswith("server_"):
                available['user_profiles'].append(filename)
            elif filename.startswith("channel_"):
                available['channel_summaries'].append(filename)
        
        return available
    
    def context_exists(self) -> bool:
        """Check if any context summaries exist."""
        return any(self.summaries_dir.glob("user_*.md")) or any(self.summaries_dir.glob("channel_*.md"))