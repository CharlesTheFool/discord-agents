# Preserved Algorithms - Reference Documentation

**Source:** Discord-Claude Bot Framework - Technical Specification v2.0

These algorithms are battle-tested from the original bot implementation and should be **preserved exactly** - port, don't rewrite. They've been fine-tuned for small Discord servers (<50 users) and work reliably in production.

---

## Table of Contents

1. [SimpleRateLimiter](#simplerateLimiter)
2. [Conversation Momentum](#conversation-momentum)
3. [Image Compression Pipeline](#image-compression-pipeline)
4. [Engagement Tracking](#engagement-tracking)

---

## SimpleRateLimiter

**Original Source:** `slh.py` lines 119-164  
**Port To:** `core/rate_limiter.py`

### Overview

Adaptive rate limiting with two-window system and engagement-based learning. Prevents spam while adapting to user responsiveness.

### Core Algorithm

```python
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Tuple, Optional

class RateLimiter:
    """
    Two-window rate limiting with engagement-based adaptation.
    
    Windows:
    - Short window: 5 minutes, max 20 responses
    - Long window: 1 hour, max 200 responses
    
    Adaptation:
    - Track ignored messages (no reaction/reply within 30s)
    - After 5 consecutive ignores, bot goes silent
    - Engagement (reaction, reply) reduces ignore count
    
    This is tuned for small servers (<50 users).
    """
    
    def __init__(self):
        # Track response timestamps per channel
        self.response_times = defaultdict(list)
        
        # Track consecutive ignores per channel
        self.ignored_count = defaultdict(int)
        
        # Configuration
        self.short_window_minutes = 5
        self.short_window_max = 20
        
        self.long_window_minutes = 60
        self.long_window_max = 200
        
        self.ignore_threshold = 5  # Consecutive ignores before silence
    
    def can_respond(self, channel_id: str) -> Tuple[bool, Optional[str]]:
        """
        Check if bot can respond in channel.
        
        Args:
            channel_id: Discord channel ID
        
        Returns:
            (can_respond, reason_if_blocked)
            
        Reasons:
            - None: Can respond
            - "rate_limit_short": Hit 5-minute limit
            - "rate_limit_long": Hit 1-hour limit  
            - "ignored_threshold": Too many consecutive ignores
        """
        now = datetime.now()
        times = self.response_times[channel_id]
        
        # Clean up old responses outside long window
        cutoff = now - timedelta(minutes=self.long_window_minutes)
        times = [t for t in times if t > cutoff]
        self.response_times[channel_id] = times
        
        # Check short window (5 minutes, max 20)
        short_cutoff = now - timedelta(minutes=self.short_window_minutes)
        short_window_responses = [t for t in times if t > short_cutoff]
        
        if len(short_window_responses) >= self.short_window_max:
            return False, "rate_limit_short"
        
        # Check long window (1 hour, max 200)
        if len(times) >= self.long_window_max:
            return False, "rate_limit_long"
        
        # Check ignore threshold
        if self.ignored_count[channel_id] >= self.ignore_threshold:
            return False, "ignored_threshold"
        
        return True, None
    
    def record_response(self, channel_id: str):
        """
        Record that bot sent a message.
        
        Args:
            channel_id: Discord channel ID
        """
        self.response_times[channel_id].append(datetime.now())
    
    def record_ignored(self, channel_id: str):
        """
        Record that bot was ignored (no engagement within 30s).
        Increments ignore counter.
        
        Args:
            channel_id: Discord channel ID
        """
        self.ignored_count[channel_id] += 1
        
        # Debug logging
        count = self.ignored_count[channel_id]
        print(f"Channel {channel_id}: Ignored count now {count}/{self.ignore_threshold}")
    
    def record_engagement(self, channel_id: str):
        """
        Record engagement (reaction, reply to bot message).
        Reduces ignore counter, rewards responsiveness.
        
        Args:
            channel_id: Discord channel ID
        """
        # Reduce ignore count, but don't go negative
        self.ignored_count[channel_id] = max(0, self.ignored_count[channel_id] - 1)
        
        # Debug logging
        count = self.ignored_count[channel_id]
        print(f"Channel {channel_id}: Engagement! Ignore count now {count}")
    
    def get_stats(self, channel_id: str) -> dict:
        """
        Get current rate limit stats for channel.
        Useful for debugging and monitoring.
        
        Returns:
            {
                "responses_5min": int,
                "responses_1hr": int,
                "ignored_count": int,
                "is_silenced": bool
            }
        """
        now = datetime.now()
        times = self.response_times[channel_id]
        
        # Count responses in windows
        short_cutoff = now - timedelta(minutes=self.short_window_minutes)
        long_cutoff = now - timedelta(minutes=self.long_window_minutes)
        
        responses_5min = len([t for t in times if t > short_cutoff])
        responses_1hr = len([t for t in times if t > long_cutoff])
        
        ignored = self.ignored_count[channel_id]
        
        return {
            "responses_5min": responses_5min,
            "responses_1hr": responses_1hr,
            "ignored_count": ignored,
            "is_silenced": ignored >= self.ignore_threshold,
            "limits": {
                "short_window": f"{responses_5min}/{self.short_window_max}",
                "long_window": f"{responses_1hr}/{self.long_window_max}"
            }
        }
    
    def reset_channel(self, channel_id: str):
        """
        Reset all limits for channel.
        Useful for testing or manual intervention.
        """
        self.response_times[channel_id].clear()
        self.ignored_count[channel_id] = 0
```

### Usage Example

```python
# Initialize
rate_limiter = RateLimiter()

# Before responding
can_respond, reason = rate_limiter.can_respond(channel_id)

if not can_respond:
    print(f"Cannot respond: {reason}")
    return

# After sending message
rate_limiter.record_response(channel_id)

# Start engagement tracking (runs after 30s delay)
asyncio.create_task(track_engagement(message_id, channel_id, rate_limiter))
```

### Tuning Notes

**Current thresholds work well for:**
- Small servers (5-50 active users)
- Casual conversation pace
- Mixed channel activity levels

**Consider adjusting if:**
- Large servers (100+ users): Increase both window maximums
- Very active channels: Increase short window max
- Bot too quiet: Reduce ignore threshold from 5 to 3
- Bot too chatty: Increase ignore threshold from 5 to 7

**Do NOT change without testing:**
- Window durations (5min/1hr) - these are well-calibrated
- Ignore threshold reduction rate (1 per engagement)

---

## Conversation Momentum

**Original Source:** `slh.py` lines 711-733 (`get_conversation_momentum()`)  
**Port To:** `core/context_builder.py` or `core/reactive_engine.py`

### Overview

Classifies conversation activity level by analyzing message timing gaps. Used to adjust bot's response probability - hot conversations get higher engagement rates.

### Core Algorithm

```python
from typing import List
from datetime import datetime

def calculate_conversation_momentum(messages: List['StoredMessage']) -> str:
    """
    Classify conversation activity level based on message gaps.
    
    Args:
        messages: List of recent messages (chronological order, oldest first)
    
    Returns:
        "hot" | "warm" | "cold"
        
    Classification:
        - hot:  avg gap < 15 minutes (rapid exchanges)
        - warm: avg gap < 1 hour (steady discussion)
        - cold: avg gap > 1 hour (slow/idle)
    
    Tuning:
        These thresholds assume small servers with organic conversation.
        Large servers may need adjustment:
        - Hot:  < 5 minutes
        - Warm: < 30 minutes
        - Cold: > 30 minutes
    """
    # Need at least 2 messages to calculate gaps
    if len(messages) < 2:
        return "cold"
    
    # Calculate time gaps between consecutive messages
    gaps = []
    for i in range(1, len(messages)):
        current_time = messages[i].timestamp
        previous_time = messages[i-1].timestamp
        
        # Gap in minutes
        gap_seconds = (current_time - previous_time).total_seconds()
        gap_minutes = gap_seconds / 60
        
        gaps.append(gap_minutes)
    
    # Average gap
    avg_gap = sum(gaps) / len(gaps)
    
    # Classify
    if avg_gap < 15:
        return "hot"
    elif avg_gap < 60:
        return "warm"
    else:
        return "cold"
```

### Usage Example

```python
# In ReactiveEngine.process_message()

# Get recent messages
recent_messages = await self.message_storage.get_recent(
    channel_id=channel_id,
    limit=20
)

# Calculate momentum
momentum = calculate_conversation_momentum(recent_messages)

# Adjust response probability
response_chance = self.config.reactive.response_chance[momentum]
# hot: 0.4, warm: 0.25, cold: 0.1

if random.random() < response_chance:
    # Respond
    pass
```

### Integration with Response Plan

```python
# Include momentum in Claude's analysis context
context = f"""
Recent conversation activity: {momentum}

{momentum} conversations have the following characteristics:
- hot: Rapid back-and-forth, people are engaged
- warm: Steady discussion, moderate engagement  
- cold: Slow/idle, low engagement

Use this to decide if jumping in makes sense.
"""
```

### Visualization for Debugging

```python
def visualize_momentum(messages: List['StoredMessage']):
    """
    Print visual representation of message timing.
    Useful for debugging momentum calculation.
    """
    if len(messages) < 2:
        print("Not enough messages to visualize")
        return
    
    print(f"Message count: {len(messages)}")
    print(f"Time span: {messages[0].timestamp} to {messages[-1].timestamp}")
    print()
    
    # Show gaps
    for i in range(1, len(messages)):
        gap = (messages[i].timestamp - messages[i-1].timestamp).total_seconds() / 60
        
        # Visual indicator
        if gap < 15:
            indicator = "🔥"  # Hot
        elif gap < 60:
            indicator = "🌡️"   # Warm
        else:
            indicator = "❄️"  # Cold
        
        print(f"{indicator} {gap:.1f}min gap: {messages[i-1].author.name} -> {messages[i].author.name}")
    
    # Show classification
    momentum = calculate_conversation_momentum(messages)
    print(f"\nOverall momentum: {momentum}")
```

### Calibration Notes

**Current thresholds (15min/1hr) work well for:**
- Friend group servers
- Small gaming communities
- Casual discussion channels

**Warning signs needing adjustment:**
- Bot thinks everything is "cold" → Lower thresholds
- Bot thinks everything is "hot" → Raise thresholds
- Bot responds too much in slow channels → Check response_chance config

---

## Image Compression Pipeline

**Original Source:** `slh.py` lines 220-226, 410-581  
**Port To:** `tools/image_processor.py`

### Overview

Multi-strategy image compression to fit Claude API limits (5MB per image). Tries increasingly aggressive strategies until size target is met.

**Target:** 73% of API limit (~3.65MB) to account for Base64 encoding overhead.

### Core Algorithm

```python
from PIL import Image
from io import BytesIO
import aiohttp
from typing import Optional
from urllib.parse import urlparse

class ImageProcessor:
    """
    Multi-strategy image compression pipeline.
    
    Target: 73% of 5MB API limit = ~3.65MB
    Why 73%: Base64 encoding adds ~33% overhead (4/3)
    
    Strategy sequence:
    1. Check if compression needed
    2. Optimize current format
    3. JPEG quality reduction (85→75→65→...→10)
    4. WebP conversion (85→75→65→...→15)
    5. Nuclear resize (0.7x dimensions)
    6. Thumbnail fallback (512x512)
    """
    
    def __init__(self):
        # API limit: 5MB per image
        self.api_limit = 5 * 1024 * 1024
        
        # Target: 73% of limit (accounts for Base64)
        self.target_size = int(self.api_limit * 0.73)
        
        # Security: Download limits
        self.max_download_size = 50 * 1024 * 1024  # 50MB
        self.download_timeout = 30  # seconds
        
        # Allowed CDN domains
        self.allowed_domains = [
            "cdn.discordapp.com",
            "media.discordapp.net"
        ]
    
    async def process_attachment(self, attachment) -> Optional[dict]:
        """
        Process Discord image attachment.
        
        Args:
            attachment: discord.Attachment object
        
        Returns:
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg|png|webp",
                    "data": "base64_encoded_string"
                }
            }
            
            None if processing fails
        """
        # Security check
        if not self._is_allowed_url(attachment.url):
            print(f"Blocked non-Discord URL: {attachment.url}")
            return None
        
        # Download image
        try:
            image_data = await self._download_image(attachment.url)
        except Exception as e:
            print(f"Failed to download image: {e}")
            return None
        
        # Check if compression needed
        if not self._needs_compression(image_data, attachment.size):
            # Small enough, use as-is
            import base64
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": self._guess_mime_type(attachment.filename),
                    "data": base64.b64encode(image_data).decode()
                }
            }
        
        # Compress image
        compressed = await self._compress_image(image_data)
        
        if compressed is None:
            print("Compression failed")
            return None
        
        # Return Claude API format
        import base64
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/webp",  # Most compressed use WebP
                "data": base64.b64encode(compressed).decode()
            }
        }
    
    def _is_allowed_url(self, url: str) -> bool:
        """Only allow Discord CDN URLs"""
        parsed = urlparse(url)
        return parsed.netloc in self.allowed_domains
    
    async def _download_image(self, url: str) -> bytes:
        """
        Securely download image with size/time limits.
        
        Raises:
            Exception if download fails or exceeds limits
        """
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=self.download_timeout) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")
                
                # Stream download, check size incrementally
                chunks = []
                total_size = 0
                
                async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1MB chunks
                    chunks.append(chunk)
                    total_size += len(chunk)
                    
                    if total_size > self.max_download_size:
                        raise Exception("Image too large (>50MB)")
                
                return b"".join(chunks)
    
    def _needs_compression(self, image_data: bytes, reported_size: int) -> bool:
        """
        Check if image needs compression.
        
        Args:
            image_data: Raw image bytes
            reported_size: Size reported by Discord
        
        Returns:
            True if compression needed, False otherwise
        """
        actual_size = len(image_data)
        
        # Use actual size, not reported (reported can be wrong)
        return actual_size > self.target_size
    
    async def _compress_image(self, image_data: bytes) -> Optional[bytes]:
        """
        Apply compression strategies sequentially until target met.
        
        Strategy order:
        1. Optimize current format
        2. JPEG quality reduction
        3. WebP conversion
        4. Nuclear resize
        5. Thumbnail fallback
        """
        img = Image.open(BytesIO(image_data))
        
        # Store original format
        original_format = img.format
        
        # Strategy 1: Optimize current format
        result = self._optimize_format(img)
        if len(result) <= self.target_size:
            print(f"Compressed via optimization: {len(result)} bytes")
            return result
        
        # Strategy 2: JPEG quality reduction (if JPEG/JPG)
        if original_format in ['JPEG', 'JPG']:
            result = self._try_jpeg_quality(img)
            if result and len(result) <= self.target_size:
                print(f"Compressed via JPEG quality: {len(result)} bytes")
                return result
        
        # Strategy 3: WebP conversion
        result = self._try_webp_conversion(img)
        if result and len(result) <= self.target_size:
            print(f"Compressed via WebP: {len(result)} bytes")
            return result
        
        # Strategy 4: Nuclear resize (0.7x dimensions)
        result = self._try_nuclear_resize(img)
        if result and len(result) <= self.target_size:
            print(f"Compressed via nuclear resize: {len(result)} bytes")
            return result
        
        # Strategy 5: Thumbnail fallback (512x512)
        result = self._try_thumbnail_fallback(img)
        print(f"Compressed via thumbnail fallback: {len(result)} bytes")
        return result
    
    def _optimize_format(self, img: Image.Image) -> bytes:
        """
        Optimize image in current format.
        Uses PIL's optimize parameter.
        """
        buffer = BytesIO()
        
        # Convert RGBA to RGB if saving as JPEG
        if img.mode == 'RGBA' and img.format == 'JPEG':
            img = img.convert('RGB')
        
        img.save(buffer, format=img.format or 'PNG', optimize=True)
        return buffer.getvalue()
    
    def _try_jpeg_quality(self, img: Image.Image) -> Optional[bytes]:
        """
        Try JPEG quality reduction: 85→75→65→55→45→35→25→15→10
        """
        # Convert RGBA to RGB for JPEG
        if img.mode == 'RGBA':
            img = img.convert('RGB')
        
        qualities = [85, 75, 65, 55, 45, 35, 25, 15, 10]
        
        for quality in qualities:
            buffer = BytesIO()
            img.save(buffer, format='JPEG', quality=quality, optimize=True)
            result = buffer.getvalue()
            
            if len(result) <= self.target_size:
                return result
        
        return None
    
    def _try_webp_conversion(self, img: Image.Image) -> Optional[bytes]:
        """
        Try WebP conversion: 85→75→65→55→45→35→25→15
        WebP generally compresses better than JPEG.
        """
        qualities = [85, 75, 65, 55, 45, 35, 25, 15]
        
        for quality in qualities:
            buffer = BytesIO()
            img.save(buffer, format='WEBP', quality=quality, method=6)
            result = buffer.getvalue()
            
            if len(result) <= self.target_size:
                return result
        
        return None
    
    def _try_nuclear_resize(self, img: Image.Image) -> Optional[bytes]:
        """
        Nuclear option: Resize to 70% dimensions.
        Reduces resolution but preserves aspect ratio.
        """
        new_width = int(img.width * 0.7)
        new_height = int(img.height * 0.7)
        
        resized = img.resize(
            (new_width, new_height),
            Image.Resampling.LANCZOS  # High-quality downsampling
        )
        
        # Save as WebP with good quality
        buffer = BytesIO()
        resized.save(buffer, format='WEBP', quality=75, method=6)
        return buffer.getvalue()
    
    def _try_thumbnail_fallback(self, img: Image.Image) -> bytes:
        """
        Last resort: Create 512x512 thumbnail.
        Maintains aspect ratio, fits within 512x512 box.
        """
        img.thumbnail((512, 512), Image.Resampling.LANCZOS)
        
        buffer = BytesIO()
        img.save(buffer, format='WEBP', quality=85, method=6)
        return buffer.getvalue()
    
    def _guess_mime_type(self, filename: str) -> str:
        """Guess MIME type from filename"""
        ext = filename.lower().split('.')[-1]
        
        mime_types = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'gif': 'image/gif',
            'webp': 'image/webp'
        }
        
        return mime_types.get(ext, 'image/jpeg')
```

### Usage Example

```python
# Initialize processor
image_processor = ImageProcessor()

# Process attachments from Discord message
async def process_message_images(message: discord.Message) -> List[dict]:
    """Process all image attachments"""
    images = []
    
    for attachment in message.attachments:
        # Check if image
        if not attachment.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            continue
        
        # Process image
        processed = await image_processor.process_attachment(attachment)
        
        if processed:
            images.append(processed)
    
    return images

# Use in Claude API call
images = await process_message_images(message)

response = await client.messages.create(
    model="claude-sonnet-4-5-20250929",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What's in these images?"},
                *images  # Include processed images
            ]
        }
    ],
    max_tokens=1024
)
```

### Testing Compression

```python
async def test_compression():
    """Test compression on sample image"""
    import asyncio
    
    processor = ImageProcessor()
    
    # Mock attachment
    class MockAttachment:
        url = "https://cdn.discordapp.com/attachments/123/456/large_image.png"
        filename = "large_image.png"
        size = 10 * 1024 * 1024  # 10MB
    
    attachment = MockAttachment()
    
    result = await processor.process_attachment(attachment)
    
    if result:
        import base64
        compressed_size = len(base64.b64decode(result['source']['data']))
        print(f"Compressed size: {compressed_size / 1024:.2f} KB")
        print(f"Target: {processor.target_size / 1024:.2f} KB")
        print(f"Success: {compressed_size <= processor.target_size}")
    else:
        print("Compression failed")

# Run test
asyncio.run(test_compression())
```

### Preservation Notes

**DO NOT change:**
- Target size calculation (73% of 5MB) - accounts for Base64 overhead
- Strategy sequence order - each builds on previous failures
- Quality step values - these are well-tuned
- Thumbnail size (512x512) - good balance of quality/size

**CAN adjust:**
- Download timeout (if on slow connections)
- Max download size (if processing local images)
- Nuclear resize factor (currently 0.7x)

---

## Engagement Tracking

**Original Source:** `slh.py` lines 1359-1386 (`track_engagement()`)  
**Port To:** `core/reactive_engine.py`

### Overview

Monitors bot messages for engagement (reactions, replies) after a 30-second delay. Updates rate limiter to adapt bot's response frequency based on user responsiveness.

### Core Algorithm

```python
import asyncio
import discord
from typing import Optional

async def track_engagement(message_id: int,
                          channel: discord.TextChannel,
                          rate_limiter: 'RateLimiter',
                          delay: int = 30) -> bool:
    """
    Track engagement on bot message.
    
    Waits 30 seconds, then checks if message got:
    - Reactions (any emoji)
    - Replies (messages referencing this one)
    
    If engaged: rate_limiter.record_engagement() - reduces ignore count
    If ignored: rate_limiter.record_ignored() - increases ignore count
    
    Args:
        message_id: Discord message ID to track
        channel: Channel where message was sent
        rate_limiter: RateLimiter instance
        delay: Seconds to wait before checking (default 30)
    
    Returns:
        True if engaged, False if ignored
    
    Why 30 seconds?
    - Long enough for users to read and react
    - Short enough to adapt quickly
    - Tested and well-calibrated
    """
    # Wait before checking
    await asyncio.sleep(delay)
    
    try:
        # Fetch fresh message to see current state
        message = await channel.fetch_message(message_id)
        
    except discord.NotFound:
        # Message was deleted - don't count as ignored
        print(f"Message {message_id} was deleted")
        return False
    
    except discord.HTTPException as e:
        print(f"Error fetching message {message_id}: {e}")
        return False
    
    # Check reactions
    has_reactions = len(message.reactions) > 0
    
    # Check replies
    has_replies = await _check_for_replies(message, channel)
    
    # Record result
    engaged = has_reactions or has_replies
    
    if engaged:
        rate_limiter.record_engagement(channel.id)
        print(f"Message {message_id}: ENGAGED ({'reactions' if has_reactions else 'replies'})")
    else:
        rate_limiter.record_ignored(channel.id)
        print(f"Message {message_id}: IGNORED")
    
    return engaged

async def _check_for_replies(message: discord.Message,
                            channel: discord.TextChannel,
                            lookback_limit: int = 10) -> bool:
    """
    Check if any recent messages reply to this message.
    
    Args:
        message: Message to check replies for
        channel: Channel to search
        lookback_limit: How many recent messages to check
    
    Returns:
        True if any replies found
    """
    try:
        # Get messages after this one
        recent = await channel.history(
            after=message.created_at,
            limit=lookback_limit
        ).flatten()
        
        # Check if any reference this message
        for msg in recent:
            if msg.reference and msg.reference.message_id == message.id:
                return True
        
        return False
        
    except discord.HTTPException:
        return False
```

### Integration with ReactiveEngine

```python
# In ReactiveEngine.execute_response()

async def execute_response(self, plan: ResponsePlan, channel: discord.TextChannel):
    """Execute response plan and track engagement"""
    
    for response in plan.responses:
        # Send message with typing
        sent_message = await self._send_with_typing(
            channel=channel,
            content=response.message,
            delay=response.delay
        )
        
        # Record response in rate limiter
        self.rate_limiter.record_response(channel.id)
        
        # Start engagement tracking in background
        asyncio.create_task(
            track_engagement(
                message_id=sent_message.id,
                channel=channel,
                rate_limiter=self.rate_limiter,
                delay=30
            )
        )
        
        # Apply cooldowns
        self._apply_cooldowns(channel.id, len(plan.responses))
```

### Advanced: Engagement Statistics

```python
from collections import defaultdict
from datetime import datetime, timedelta

class EngagementTracker:
    """
    Track engagement statistics over time.
    Useful for analytics and proactive engagement decisions.
    """
    
    def __init__(self):
        self.engagement_history = defaultdict(list)
        # Store: (timestamp, engaged: bool)
    
    def record_engagement_result(self, channel_id: str, engaged: bool):
        """Record engagement outcome"""
        self.engagement_history[channel_id].append({
            "timestamp": datetime.now(),
            "engaged": engaged
        })
    
    def get_engagement_rate(self, channel_id: str, 
                           hours: int = 24) -> Optional[float]:
        """
        Calculate engagement rate for channel.
        
        Args:
            channel_id: Channel to analyze
            hours: Look back N hours
        
        Returns:
            Engagement rate (0.0 to 1.0) or None if insufficient data
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        
        history = self.engagement_history[channel_id]
        recent = [h for h in history if h["timestamp"] > cutoff]
        
        if len(recent) < 5:
            return None  # Need at least 5 data points
        
        engaged_count = sum(1 for h in recent if h["engaged"])
        return engaged_count / len(recent)
    
    def should_attempt_proactive(self, channel_id: str) -> bool:
        """
        Decide if proactive engagement likely to succeed.
        
        Returns:
            True if engagement rate > 30%
        """
        rate = self.get_engagement_rate(channel_id, hours=168)  # 7 days
        
        if rate is None:
            return True  # No data, worth trying
        
        return rate > 0.3  # 30% threshold
```

### Calibration Notes

**30-second delay is well-tuned for:**
- Typical reading speed
- Time to find emoji
- Time to compose short reply

**Adjust delay if:**
- Bot thinks everything is ignored: Increase to 45-60s
- Bot adapts too slowly: Decrease to 20-25s
- Different channel types: Use different delays

**DO NOT:**
- Make delay < 15s (too fast for users)
- Make delay > 60s (bot adapts too slowly)
- Skip tracking (essential for adaptive behavior)

---

## Testing the Algorithms

### Unit Tests

```python
import pytest
from datetime import datetime, timedelta

def test_rate_limiter_short_window():
    """Test short window (5 min, max 20)"""
    limiter = RateLimiter()
    channel = "test_channel"
    
    # Send 20 messages (should work)
    for i in range(20):
        can_respond, _ = limiter.can_respond(channel)
        assert can_respond, f"Failed at message {i+1}"
        limiter.record_response(channel)
    
    # 21st should be blocked
    can_respond, reason = limiter.can_respond(channel)
    assert not can_respond
    assert reason == "rate_limit_short"

def test_conversation_momentum():
    """Test momentum calculation"""
    from collections import namedtuple
    Message = namedtuple('Message', ['timestamp'])
    
    base = datetime.now()
    
    # Hot conversation (5 min gaps)
    messages_hot = [
        Message(base + timedelta(minutes=i*5))
        for i in range(5)
    ]
    assert calculate_conversation_momentum(messages_hot) == "hot"
    
    # Warm conversation (30 min gaps)
    messages_warm = [
        Message(base + timedelta(minutes=i*30))
        for i in range(5)
    ]
    assert calculate_conversation_momentum(messages_warm) == "warm"
    
    # Cold conversation (2 hour gaps)
    messages_cold = [
        Message(base + timedelta(hours=i*2))
        for i in range(5)
    ]
    assert calculate_conversation_momentum(messages_cold) == "cold"

def test_ignore_threshold():
    """Test ignore threshold adaptation"""
    limiter = RateLimiter()
    channel = "test_channel"
    
    # Record 5 ignores
    for i in range(5):
        limiter.record_ignored(channel)
    
    # Should be silenced
    can_respond, reason = limiter.can_respond(channel)
    assert not can_respond
    assert reason == "ignored_threshold"
    
    # One engagement should help
    limiter.record_engagement(channel)
    can_respond, _ = limiter.can_respond(channel)
    assert can_respond
```

### Integration Tests

```python
@pytest.mark.asyncio
async def test_engagement_tracking():
    """Test full engagement tracking flow"""
    # Mock Discord message and channel
    # ... setup mocks
    
    rate_limiter = RateLimiter()
    channel_id = "123456"
    
    # Track non-engaged message
    engaged = await track_engagement(
        message_id=mock_message_id,
        channel=mock_channel,
        rate_limiter=rate_limiter,
        delay=1  # Fast for testing
    )
    
    assert not engaged
    assert rate_limiter.ignored_count[channel_id] == 1
```

---

## Summary

These four algorithms are production-tested and should be ported exactly:

1. **SimpleRateLimiter**: Two-window rate limiting with adaptive learning
2. **Conversation Momentum**: Activity classification for response tuning  
3. **Image Compression**: Multi-strategy pipeline for API limits
4. **Engagement Tracking**: 30-second feedback loop for adaptation

**Critical:** Don't "improve" these algorithms without A/B testing. They're tuned for small Discord servers and work reliably in production. Port them, test them, then move on to new features.
