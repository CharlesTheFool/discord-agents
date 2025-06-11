"""
Message Memory System
Lightweight in-memory conversation storage for Discord bot context awareness.
"""

from collections import deque
from datetime import datetime
from typing import Dict, List, Optional
import discord


class MessageMemory:
    """Lightweight in-memory conversation storage"""
    
    def __init__(self, max_messages: int = 100):
        """
        Initialize message memory system.
        
        Args:
            max_messages: Maximum messages to store per channel
        """
        self.channels: Dict[int, deque] = {}  # channel_id -> deque of messages
        self.max_messages = max_messages
        
    def add_message(self, channel_id: int, message: discord.Message) -> None:
        """
        Store a message in the channel's conversation history.
        
        Args:
            channel_id: Discord channel ID
            message: Discord message object
        """
        if channel_id not in self.channels:
            self.channels[channel_id] = deque(maxlen=self.max_messages)
            
        self.channels[channel_id].append({
            'id': message.id,
            'author': message.author.name,
            'author_id': message.author.id,
            'content': message.content,
            'timestamp': message.created_at,
            'is_bot': message.author.bot,
            'mentions': [u.id for u in message.mentions],
            'attachments': len(message.attachments) > 0,
            'embeds': len(message.embeds) > 0
        })
        
    def get_context(self, channel_id: int, limit: int = 20) -> List[dict]:
        """
        Retrieve recent conversation context for a channel.
        
        Args:
            channel_id: Discord channel ID
            limit: Maximum number of messages to return
            
        Returns:
            List of message dictionaries, most recent last
        """
        if channel_id not in self.channels:
            return []
        return list(self.channels[channel_id])[-limit:]
    
    def get_recent_participants(self, channel_id: int, minutes: int = 30) -> List[int]:
        """
        Get list of user IDs who've been active recently in the channel.
        
        Args:
            channel_id: Discord channel ID
            minutes: How far back to look for activity
            
        Returns:
            List of user IDs who posted recently
        """
        if channel_id not in self.channels:
            return []
            
        cutoff = datetime.now().timestamp() - (minutes * 60)
        recent_users = set()
        
        for msg in reversed(self.channels[channel_id]):
            if msg['timestamp'].timestamp() < cutoff:
                break
            if not msg['is_bot']:
                recent_users.add(msg['author_id'])
                
        return list(recent_users)
    
    def clear_channel(self, channel_id: int) -> None:
        """Clear all stored messages for a channel."""
        if channel_id in self.channels:
            self.channels[channel_id].clear()
    
    def get_channel_stats(self, channel_id: int) -> dict:
        """
        Get basic statistics about a channel's message history.
        
        Returns:
            Dictionary with message count, unique users, etc.
        """
        if channel_id not in self.channels:
            return {'message_count': 0, 'unique_users': 0, 'bot_messages': 0}
            
        messages = list(self.channels[channel_id])
        unique_users = set(msg['author_id'] for msg in messages if not msg['is_bot'])
        bot_messages = sum(1 for msg in messages if msg['is_bot'])
        
        return {
            'message_count': len(messages),
            'unique_users': len(unique_users),
            'bot_messages': bot_messages,
            'human_messages': len(messages) - bot_messages
        }