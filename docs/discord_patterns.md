# Discord.py Patterns - Reference Guide

**Version:** discord.py 2.3+
**Python:** 3.9+

Essential patterns for building Discord bots with async/await.

---

## Table of Contents

1. [Basic Setup](#basic-setup)
2. [Event Handlers](#event-handlers)
3. [Reply Chains](#reply-chains)
4. [Typing Indicators](#typing-indicators)
5. [Message History](#message-history)
6. [Image Attachments](#image-attachments)
7. [Reactions](#reactions)
8. [Threading](#threading)
9. [Server/Guild Queries](#serverguild-queries)
10. [Error Handling](#error-handling)

---

## Basic Setup

### Minimal Bot

```python
import discord
from discord.ext import commands

# Intents define what events bot can receive
intents = discord.Intents.default()
intents.message_content = True  # Required to read message text
intents.reactions = True        # Required to see reactions
intents.guilds = True           # Required for server info
intents.members = True          # Required for member list

# Create client
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    """Bot connected and ready"""
    print(f'Logged in as {client.user.name}')
    print(f'Bot ID: {client.user.id}')

@client.event
async def on_message(message: discord.Message):
    """New message received"""
    # Ignore own messages
    if message.author == client.user:
        return
    
    # Simple echo
    if message.content.startswith('!echo'):
        await message.channel.send(message.content[6:])

# Start bot
client.run('YOUR_BOT_TOKEN')
```

### With Commands Extension

```python
from discord.ext import commands

# Bot with command prefix
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')

@bot.command()
async def ping(ctx):
    """Simple command: !ping"""
    await ctx.send(f'Pong! Latency: {round(bot.latency * 1000)}ms')

bot.run('YOUR_BOT_TOKEN')
```

---

## Event Handlers

### Core Events

```python
@client.event
async def on_ready():
    """
    Bot connected to Discord.
    Fires once on startup.
    """
    print(f'Bot is ready: {client.user}')
    
    # Set activity status
    await client.change_presence(
        activity=discord.Game(name="with Python")
    )

@client.event
async def on_message(message: discord.Message):
    """
    New message in any channel bot can see.
    Most frequently fired event.
    """
    # Always ignore bot's own messages
    if message.author == client.user:
        return
    
    # Access message properties
    print(f"Author: {message.author.name}")
    print(f"Content: {message.content}")
    print(f"Channel: {message.channel.name}")
    print(f"Server: {message.guild.name}")

@client.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    """Message was edited"""
    if before.content != after.content:
        print(f"Message edited: {before.content} -> {after.content}")

@client.event
async def on_message_delete(message: discord.Message):
    """Message was deleted"""
    print(f"Message deleted: {message.content}")

@client.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    """
    Reaction added to message.
    Track engagement, implement reaction roles, etc.
    """
    if user == client.user:
        return  # Ignore bot's own reactions
    
    print(f"{user.name} reacted with {reaction.emoji}")

@client.event
async def on_reaction_remove(reaction: discord.Reaction, user: discord.User):
    """Reaction removed from message"""
    print(f"{user.name} removed {reaction.emoji}")

@client.event
async def on_member_join(member: discord.Member):
    """New member joined server"""
    channel = member.guild.system_channel
    if channel:
        await channel.send(f'Welcome {member.mention}!')

@client.event
async def on_member_remove(member: discord.Member):
    """Member left server"""
    print(f'{member.name} left the server')

@client.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel):
    """New channel created"""
    print(f'New channel created: {channel.name}')

@client.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    """Channel deleted"""
    print(f'Channel deleted: {channel.name}')
```

### Error Handling in Events

```python
@client.event
async def on_error(event: str, *args, **kwargs):
    """
    Global error handler for all events.
    Catches exceptions that would otherwise crash bot.
    """
    import traceback
    import sys
    
    print(f'Error in event {event}:', file=sys.stderr)
    traceback.print_exc()
    
    # Log to file
    with open('errors.log', 'a') as f:
        traceback.print_exc(file=f)
```

---

## Reply Chains

### Understanding Discord Replies

Discord's reply feature lets users reference any message, creating a chain. Your bot needs to resolve this chain for context.

```python
async def get_reply_chain(message: discord.Message) -> list[dict]:
    """
    Build parent message chain for replies.
    
    Returns list of messages from oldest (root) to newest (parent).
    
    Example:
        User A: "What's for dinner?"
        User B: "Pizza!" (replies to A)
        User C: "What toppings?" (replies to B)
        
        get_reply_chain(User C's message) returns:
        [
            {"author": "User A", "content": "What's for dinner?"},
            {"author": "User B", "content": "Pizza!"}
        ]
    """
    chain = []
    current = message
    
    # Walk up the reply chain
    while current.reference:
        try:
            # Fetch parent message
            parent = await current.channel.fetch_message(
                current.reference.message_id
            )
            
            chain.append({
                "id": str(parent.id),
                "author": parent.author.name,
                "content": parent.content,
                "timestamp": parent.created_at.isoformat(),
                "attachments": len(parent.attachments) > 0
            })
            
            current = parent
            
        except discord.NotFound:
            # Parent message was deleted
            chain.append({
                "id": str(current.reference.message_id),
                "author": "Unknown",
                "content": "[Message deleted]",
                "timestamp": None,
                "attachments": False
            })
            break
        
        except discord.HTTPException as e:
            print(f"Error fetching parent message: {e}")
            break
    
    # Return oldest-first (reverse chronological)
    return list(reversed(chain))
```

### Usage Example

```python
@client.event
async def on_message(message: discord.Message):
    """Handle message with reply context"""
    if message.author == client.user:
        return
    
    # Check if message is a reply
    if message.reference:
        reply_chain = await get_reply_chain(message)
        
        # Format for context
        context = "Reply chain:\n"
        for msg in reply_chain:
            context += f"{msg['author']}: {msg['content']}\n"
        context += f"{message.author.name}: {message.content}"
        
        print(context)
```

### Reply to a Message

```python
async def reply_to_message(message: discord.Message, content: str):
    """
    Reply to a message (creates a reply reference).
    Shows connection in Discord UI.
    """
    await message.reply(content)

# Example usage
@client.event
async def on_message(message: discord.Message):
    if message.content == "ping":
        await message.reply("pong!")  # Creates reply reference
```

---

## Typing Indicators

### Basic Typing

```python
async def send_with_typing(channel: discord.TextChannel, 
                          content: str, 
                          duration: float = 2.0):
    """
    Show 'Bot is typing...' indicator before sending message.
    
    Args:
        channel: Where to send message
        content: Message text
        duration: How long to show typing (seconds)
    """
    async with channel.typing():
        await asyncio.sleep(duration)
    
    return await channel.send(content)

# Example usage
@client.event
async def on_message(message: discord.Message):
    if message.content == "tell me a story":
        await send_with_typing(
            message.channel,
            "Once upon a time...",
            duration=3.0  # Type for 3 seconds
        )
```

### Typing During Processing

```python
async def process_with_typing(message: discord.Message):
    """
    Show typing while doing actual work.
    Typing automatically stops when context exits.
    """
    async with message.channel.typing():
        # Do work while showing typing indicator
        result = await expensive_operation()
        
        # Process data
        formatted = format_result(result)
    
    # Send after typing stops
    await message.channel.send(formatted)
```

### Manual Typing Control

```python
async def long_operation(channel: discord.TextChannel):
    """
    Manually trigger typing for long operations.
    Typing expires after 10 seconds, must re-trigger.
    """
    while processing:
        await channel.trigger_typing()  # Shows typing for 10 seconds
        await asyncio.sleep(8)  # Re-trigger before expiry
        # ... continue processing
```

---

## Message History

### Fetch Recent Messages

```python
async def get_recent_messages(channel: discord.TextChannel, 
                             limit: int = 100) -> list[discord.Message]:
    """
    Get recent messages from channel.
    Returns newest-first by default.
    """
    messages = []
    async for message in channel.history(limit=limit):
        messages.append(message)
    return messages

# Or use .flatten() for list
messages = await channel.history(limit=100).flatten()
```

### Fetch Messages After Specific Time

```python
from datetime import datetime, timedelta

async def get_messages_since(channel: discord.TextChannel, 
                             hours_ago: int = 1) -> list[discord.Message]:
    """Get messages from last N hours"""
    cutoff = datetime.utcnow() - timedelta(hours=hours_ago)
    
    messages = []
    async for message in channel.history(after=cutoff, limit=None):
        messages.append(message)
    
    return messages
```

### Fetch Messages Between Times

```python
async def get_messages_between(channel: discord.TextChannel,
                              start: datetime,
                              end: datetime) -> list[discord.Message]:
    """Get messages in time range"""
    messages = []
    async for message in channel.history(after=start, before=end, limit=None):
        messages.append(message)
    return messages
```

### Fetch Specific Message

```python
async def get_message_by_id(channel: discord.TextChannel, 
                           message_id: int) -> discord.Message:
    """
    Fetch single message by ID.
    Raises discord.NotFound if message doesn't exist.
    """
    try:
        message = await channel.fetch_message(message_id)
        return message
    except discord.NotFound:
        print(f"Message {message_id} not found")
        return None
    except discord.HTTPException as e:
        print(f"Error fetching message: {e}")
        return None
```

### Iterate Through All Messages

```python
async def scan_all_messages(channel: discord.TextChannel):
    """
    Scan all messages in channel history.
    Use carefully - can be slow for large channels.
    """
    message_count = 0
    
    async for message in channel.history(limit=None):  # No limit = all history
        message_count += 1
        
        # Process each message
        print(f"{message.author.name}: {message.content}")
        
        # Rate limiting: Discord allows ~50 requests/second
        if message_count % 100 == 0:
            await asyncio.sleep(1)
    
    print(f"Total messages: {message_count}")
```

---

## Image Attachments

### Check for Images

```python
def get_image_attachments(message: discord.Message) -> list[discord.Attachment]:
    """
    Filter attachments to only images.
    Supports: PNG, JPG, JPEG, GIF, WEBP
    """
    image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    
    images = [
        attachment for attachment in message.attachments
        if any(attachment.filename.lower().endswith(ext) 
               for ext in image_extensions)
    ]
    
    return images
```

### Download Image

```python
async def download_image(attachment: discord.Attachment) -> bytes:
    """
    Download image from Discord CDN.
    Returns bytes of image data.
    """
    # Security: Verify it's a Discord CDN URL
    if not (attachment.url.startswith('https://cdn.discordapp.com') or 
            attachment.url.startswith('https://media.discordapp.net')):
        raise ValueError("Not a valid Discord CDN URL")
    
    # Download with size limit (50MB)
    if attachment.size > 50 * 1024 * 1024:
        raise ValueError("Image too large (>50MB)")
    
    image_bytes = await attachment.read()
    return image_bytes
```

### Save Image to Disk

```python
async def save_image(attachment: discord.Attachment, save_path: str):
    """Save image attachment to disk"""
    await attachment.save(save_path)
    print(f"Saved image: {save_path}")
```

### Process Image with Pillow

```python
from PIL import Image
from io import BytesIO

async def process_image(attachment: discord.Attachment) -> Image.Image:
    """
    Download and open image with Pillow.
    """
    image_bytes = await attachment.read()
    image = Image.open(BytesIO(image_bytes))
    return image

# Example: Grayscale filter
@client.event
async def on_message(message: discord.Message):
    images = get_image_attachments(message)
    
    if images and message.content == "!grayscale":
        # Process first image
        img = await process_image(images[0])
        
        # Convert to grayscale
        gray_img = img.convert('L')
        
        # Save to buffer
        buffer = BytesIO()
        gray_img.save(buffer, format='PNG')
        buffer.seek(0)
        
        # Send back to Discord
        file = discord.File(buffer, filename='grayscale.png')
        await message.channel.send("Here's your grayscale image:", file=file)
```

### Send Image from Disk

```python
async def send_image(channel: discord.TextChannel, 
                    image_path: str,
                    message: str = None):
    """Send image file to channel"""
    file = discord.File(image_path)
    
    if message:
        await channel.send(content=message, file=file)
    else:
        await channel.send(file=file)
```

### Send Image from Bytes

```python
async def send_image_bytes(channel: discord.TextChannel,
                          image_data: bytes,
                          filename: str = "image.png"):
    """Send image from bytes/buffer"""
    buffer = BytesIO(image_data)
    file = discord.File(buffer, filename=filename)
    await channel.send(file=file)
```

### Multiple Images

```python
async def send_multiple_images(channel: discord.TextChannel, 
                              image_paths: list[str]):
    """
    Send multiple images in one message.
    Discord supports up to 10 files per message.
    """
    if len(image_paths) > 10:
        raise ValueError("Discord allows max 10 files per message")
    
    files = [discord.File(path) for path in image_paths]
    await channel.send(files=files)
```

---

## Reactions

### Add Reaction

```python
async def add_reaction(message: discord.Message, emoji: str):
    """
    Add reaction to message.
    
    Emoji can be:
    - Unicode emoji: "👍", "❤️", "😀"
    - Custom emoji: "<:name:id>" or just "name" if bot has access
    """
    await message.add_reaction(emoji)

# Example
@client.event
async def on_message(message: discord.Message):
    if "good bot" in message.content.lower():
        await message.add_reaction("👍")
```

### Remove Reaction

```python
# Remove bot's own reaction
await message.remove_reaction(emoji, client.user)

# Remove specific user's reaction
await message.remove_reaction(emoji, user)

# Remove all reactions of specific emoji
await message.clear_reaction(emoji)

# Remove ALL reactions from message
await message.clear_reactions()
```

### Track Engagement

```python
async def track_engagement(message_id: int, 
                          channel: discord.TextChannel,
                          delay: int = 30) -> bool:
    """
    Check if message got engagement after delay.
    Returns True if reactions or replies, False otherwise.
    """
    # Wait before checking
    await asyncio.sleep(delay)
    
    try:
        # Fetch fresh message to see current reactions
        message = await channel.fetch_message(message_id)
        
        # Check reactions
        has_reactions = len(message.reactions) > 0
        
        # Check replies (messages that reference this one)
        recent = await channel.history(
            after=message.created_at,
            limit=20
        ).flatten()
        
        has_replies = any(
            msg.reference and msg.reference.message_id == message_id
            for msg in recent
        )
        
        return has_reactions or has_replies
        
    except discord.NotFound:
        return False  # Message was deleted
```

### Listen for Specific Reactions

```python
@client.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    """
    React to specific emoji reactions.
    Useful for reaction roles, polls, etc.
    """
    if user == client.user:
        return
    
    # Check for specific emoji
    if str(reaction.emoji) == "📌":
        # Pin message
        await reaction.message.pin()
        await reaction.message.channel.send(
            f"{user.mention} pinned this message!"
        )
    
    elif str(reaction.emoji) == "🗑️":
        # Delete message (if user is author or has permissions)
        if user == reaction.message.author:
            await reaction.message.delete()
```

---

## Threading

### Create Thread

```python
async def create_thread(message: discord.Message, 
                       name: str,
                       auto_archive_duration: int = 60) -> discord.Thread:
    """
    Create thread from message.
    
    Args:
        message: Message to start thread from
        name: Thread name (max 100 chars)
        auto_archive_duration: Minutes until auto-archive (60, 1440, 4320, 10080)
    """
    thread = await message.create_thread(
        name=name,
        auto_archive_duration=auto_archive_duration
    )
    return thread
```

### Send to Thread

```python
async def send_to_thread(thread: discord.Thread, content: str):
    """Send message to thread"""
    await thread.send(content)
```

### Get Active Threads

```python
async def get_active_threads(channel: discord.TextChannel) -> list[discord.Thread]:
    """Get all active threads in channel"""
    threads = await channel.active_threads()
    return threads
```

### Thread Events

```python
@client.event
async def on_thread_create(thread: discord.Thread):
    """New thread created"""
    print(f"Thread created: {thread.name}")
    
    # Auto-join thread
    await thread.join()

@client.event
async def on_thread_update(before: discord.Thread, after: discord.Thread):
    """Thread updated (name, archived status, etc.)"""
    if before.archived != after.archived:
        if after.archived:
            print(f"Thread archived: {after.name}")
        else:
            print(f"Thread unarchived: {after.name}")
```

---

## Server/Guild Queries

### Get Server Info

```python
def get_guild_info(guild: discord.Guild) -> dict:
    """Get server information"""
    return {
        "id": str(guild.id),
        "name": guild.name,
        "member_count": guild.member_count,
        "created_at": guild.created_at.isoformat(),
        "owner_id": str(guild.owner_id),
        "icon_url": str(guild.icon.url) if guild.icon else None,
        "description": guild.description,
        "features": guild.features
    }
```

### List All Members

```python
async def get_all_members(guild: discord.Guild) -> list[dict]:
    """
    Get all server members.
    Requires members intent enabled.
    """
    members = []
    for member in guild.members:
        members.append({
            "id": str(member.id),
            "name": member.name,
            "display_name": member.display_name,
            "bot": member.bot,
            "joined_at": member.joined_at.isoformat() if member.joined_at else None,
            "roles": [role.name for role in member.roles]
        })
    return members
```

### List Channels

```python
def get_all_channels(guild: discord.Guild) -> dict:
    """Get all channels by type"""
    channels = {
        "text": [],
        "voice": [],
        "category": []
    }
    
    for channel in guild.channels:
        if isinstance(channel, discord.TextChannel):
            channels["text"].append({
                "id": str(channel.id),
                "name": channel.name,
                "position": channel.position,
                "topic": channel.topic
            })
        elif isinstance(channel, discord.VoiceChannel):
            channels["voice"].append({
                "id": str(channel.id),
                "name": channel.name,
                "position": channel.position,
                "user_limit": channel.user_limit
            })
        elif isinstance(channel, discord.CategoryChannel):
            channels["category"].append({
                "id": str(channel.id),
                "name": channel.name,
                "position": channel.position
            })
    
    return channels
```

### Get Pinned Messages

```python
async def get_pinned_messages(channel: discord.TextChannel) -> list[dict]:
    """Get all pinned messages in channel"""
    pins = await channel.pins()
    
    return [
        {
            "id": str(msg.id),
            "author": msg.author.name,
            "content": msg.content,
            "created_at": msg.created_at.isoformat(),
            "attachments": len(msg.attachments)
        }
        for msg in pins
    ]
```

### Check Permissions

```python
def check_permissions(member: discord.Member, 
                     channel: discord.TextChannel) -> dict:
    """
    Check what permissions member has in channel.
    """
    perms = channel.permissions_for(member)
    
    return {
        "send_messages": perms.send_messages,
        "read_messages": perms.read_messages,
        "manage_messages": perms.manage_messages,
        "attach_files": perms.attach_files,
        "add_reactions": perms.add_reactions,
        "administrator": perms.administrator
    }
```

---

## Error Handling

### Try-Except Patterns

```python
async def safe_send_message(channel: discord.TextChannel, content: str):
    """Send message with error handling"""
    try:
        return await channel.send(content)
    
    except discord.Forbidden:
        print(f"Missing permissions to send in {channel.name}")
    
    except discord.HTTPException as e:
        print(f"HTTP error sending message: {e}")
    
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
```

### Retry Logic

```python
async def send_with_retry(channel: discord.TextChannel, 
                         content: str,
                         max_retries: int = 3):
    """Send message with exponential backoff retry"""
    for attempt in range(max_retries):
        try:
            return await channel.send(content)
        
        except discord.HTTPException as e:
            if attempt == max_retries - 1:
                raise  # Give up after max retries
            
            # Exponential backoff: 1s, 2s, 4s
            wait_time = 2 ** attempt
            print(f"Retry {attempt + 1} after {wait_time}s...")
            await asyncio.sleep(wait_time)
```

### Common Exceptions

```python
async def handle_common_errors(operation):
    """
    Common Discord.py exceptions and how to handle them.
    """
    try:
        await operation()
    
    except discord.Forbidden:
        # Bot lacks permissions
        print("Missing permissions for this action")
    
    except discord.NotFound:
        # Resource doesn't exist (message deleted, channel removed, etc.)
        print("Resource not found")
    
    except discord.HTTPException as e:
        # Generic HTTP error (rate limit, server error, etc.)
        print(f"HTTP error: {e.status} - {e.text}")
    
    except discord.InvalidArgument:
        # Invalid argument passed to function
        print("Invalid argument")
    
    except discord.LoginFailure:
        # Invalid bot token
        print("Failed to log in - check token")
    
    except asyncio.TimeoutError:
        # Operation timed out
        print("Operation timed out")
```

---

## Performance Tips

### Batch Operations

```python
# BAD: Multiple API calls
for message in messages:
    await message.delete()

# GOOD: Single bulk delete
await channel.delete_messages(messages)
```

### Caching

```python
# Use cached data when possible
guild = client.get_guild(guild_id)  # From cache
member = guild.get_member(user_id)  # From cache

# Only fetch when necessary
guild = await client.fetch_guild(guild_id)  # API call
member = await guild.fetch_member(user_id)  # API call
```

### Rate Limiting

```python
# Discord rate limits: ~50 requests/second
# Add delays for bulk operations
for i, channel in enumerate(channels):
    await channel.send("Announcement")
    
    if i % 10 == 0:
        await asyncio.sleep(1)  # Brief pause every 10 messages
```

---

## Complete Bot Example

```python
import discord
import asyncio
from datetime import datetime

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f'Bot ready: {client.user}')

@client.event
async def on_message(message: discord.Message):
    # Ignore self
    if message.author == client.user:
        return
    
    # Handle reply chain
    if message.reference:
        chain = await get_reply_chain(message)
        print(f"Reply chain length: {len(chain)}")
    
    # Process images
    images = get_image_attachments(message)
    if images:
        print(f"Message has {len(images)} images")
    
    # Respond with typing indicator
    if message.content.startswith("!analyze"):
        async with message.channel.typing():
            await asyncio.sleep(2)  # Simulate processing
            await message.reply("Analysis complete!")
            
        # Track engagement
        sent_msg = await message.channel.send("Did this help?")
        await sent_msg.add_reaction("👍")
        await sent_msg.add_reaction("👎")
        
        # Check engagement after 30s
        asyncio.create_task(
            track_engagement(sent_msg.id, message.channel, delay=30)
        )

async def get_reply_chain(message):
    # Implementation from earlier
    pass

def get_image_attachments(message):
    # Implementation from earlier
    pass

async def track_engagement(message_id, channel, delay):
    # Implementation from earlier
    pass

client.run('YOUR_BOT_TOKEN')
```

---

## Additional Resources

- **Discord.py Documentation:** https://discordpy.readthedocs.io/
- **Discord API Reference:** https://discord.com/developers/docs/
- **Discord.py GitHub:** https://github.com/Rapptz/discord.py
