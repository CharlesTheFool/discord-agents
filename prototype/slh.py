"""
SLH-01 Discord Bot - Agentic Conversation Participant
Natural Discord bot with periodic check-ins and contextual awareness.
"""

import discord
from discord.ext import tasks
from anthropic import AsyncAnthropic # Changed to AsyncAnthropic
import os
import re 
import random
import asyncio
from datetime import datetime, timedelta, timezone
from collections import deque, defaultdict # Keep deque, defaultdict for MessageMemory and SimpleRateLimiter
import json # Explicitly import json
import base64
import aiohttp
from PIL import Image
import io

# Pillow compatibility for older versions
try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE_LANCZOS = Image.ANTIALIAS
from dotenv import load_dotenv
from context_manager import ContextManager # Assuming this exists and works as intended
from bot.memory import MessageMemory # Assuming this exists and works

load_dotenv()

WEB_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3
}

WEB_FETCH_TOOL = {
    "type": "web_fetch_20250910",
    "name": "web_fetch",
    "max_uses": 5,
    "citations": {"enabled": True}
}

# Your SYSTEM_PROMPT, lightly sanitized for platform safety but keeping the spirit
SYSTEM_PROMPT = """you are **slh-01** (sherlock's initials, nothing deeper)—robot protagonist of the detective mystery game (Station No.5) being developed in this discord server. you're a regular member who happens to be sharp, occasionally helpful, and blessed with a dark sense of humor that actually lands.

### core personality
- observant critic who picks battles worth fighting, not every single typo
- dark humor that's actually clever—think anthony jeselnik, not middle school edgelord  
- genuine curiosity about interesting problems; bored by repetitive stupidity
- respects competence, roasts mediocrity, but knows the difference between helping and harassing
- self-aware enough to know when you're being the annoying one

### conversation philosophy  
- quality over quantity—better to drop one perfect line than five mediocre ones
- silence is often the wittiest response
- if you wouldn't interrupt people IRL to say it, probably skip it
- dark humor works because of timing and surprise, not volume
- actually read the room—sometimes people need encouragement, not another roast

### how you talk
- lowercase, conversational, modern american english
- vary your length—sometimes one word hits harder than a sentence
- natural profanity when it serves the joke, not as punctuation
- meme language only when it's the perfect fit, not constantly
- no forced catchphrases or signature moves—let variety be your signature

### engagement triggers (in priority order)
ALWAYS respond to:
1. direct @mentions or questions
2. genuinely interesting technical problems you can solve elegantly

CONSIDER responding to (max 1-2 per hour):
3. hilariously bad takes that deserve one good roast
4. opportunities for genuinely funny dark humor
5. someone struggling where you can help in a non-condescending way

ALMOST NEVER respond to:
- minor errors or suboptimal choices
- conversations that have already moved on
- topics you've already commented on recently
- drama or arguments (unless you can defuse with humor)

### personality notes
- your best insults are clever, not cruel
- admitting when you're wrong or confused makes the hits land harder when you're right
- occasional genuine enthusiasm or help makes the sarcasm more effective
- "shut the fuck up" is funnier when used once a week, not once an hour
- sometimes the edgiest thing is being unexpectedly wholesome

### absolute don'ts
- forcing your personality into every interaction
- repeating the same joke structures
- always having the last word
- treating every message as performance opportunity
- being predictable

remember: you're the friend who makes people laugh with perfectly-timed dark observations, not the exhausting guy who never stops performing. less is more."""


class APIThrottler:
    """Simple API call throttling."""
    def __init__(self, min_delay=1.0):
        self.last_call = datetime.now() - timedelta(seconds=min_delay)
        self.min_delay = min_delay
        self.lock = asyncio.Lock()

    async def throttle(self):
        async with self.lock:
            time_since_last = (datetime.now() - self.last_call).total_seconds()
            if time_since_last < self.min_delay:
                delay = self.min_delay - time_since_last
                print(f"  └─ API throttle: waiting {delay:.1f}s")
                await asyncio.sleep(delay)
            self.last_call = datetime.now()


class SimpleRateLimiter: # Copied from original, seems fine for its purpose
    """Rate limiting that actually makes sense."""
    def __init__(self):
        self.responses = defaultdict(list)
        self.ignored = defaultdict(int)

    def check(self, channel_id: int) -> tuple[bool, str]:
        now = datetime.now()
        self.responses[channel_id] = [
            ts for ts in self.responses[channel_id]
            if now - ts < timedelta(hours=1)
        ]
        if self.ignored[channel_id] >= 5:  # Compromise: quiets after 5 ignored messages
            return False, "being_ignored"
        recent_5min = sum(1 for ts in self.responses[channel_id] if now - ts < timedelta(minutes=5))
        recent_hour = len(self.responses[channel_id])
        if recent_5min >= 20:
            return False, "too_many_recent"
        if recent_hour >= 200:
            return False, "hourly_limit"
        return True, "ok"

    def record_response(self, channel_id: int):
        self.responses[channel_id].append(datetime.now())

    def record_ignored(self, channel_id: int):
        self.ignored[channel_id] += 1
        print(f"  └─ IGNORED: Message ignored in channel {channel_id}, ignore count now: {self.ignored[channel_id]}")

    def record_engagement(self, channel_id: int):
        old_count = self.ignored[channel_id]
        self.ignored[channel_id] = max(0, self.ignored[channel_id] - 1)
        if old_count > 0:
            print(f"  └─ ENGAGEMENT: Positive feedback in channel {channel_id}, ignore count: {old_count} -> {self.ignored[channel_id]}")

    def get_stats(self) -> dict:
        total_responses = sum(len(responses) for responses in self.responses.values())
        active_channels = len([ch for ch, msgs in self.responses.items() if msgs])
        ignored_channels = len([ch for ch, count in self.ignored.items() if count > 0])
        return {
            'total_responses': total_responses,
            'active_channels': active_channels,
            'ignored_channels': ignored_channels,
            'channels_tracked': len(self.responses)
        }


class AgenticBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.reactions = True  # Required for on_reaction_add to work
        super().__init__(intents=intents)

        self.anthropic = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=30.0) # Switched to AsyncAnthropic and added timeout
        self.memory = MessageMemory(max_messages=150)  # Set to 150 as requested
        self.context_manager = ContextManager()
        
        # Rate limiting and engagement tracking
        self.rate_limiter = SimpleRateLimiter()
        self.last_user_reply = defaultdict(lambda: datetime(2020, 1, 1, tzinfo=timezone.utc))  # Per-user cooldowns
        
        # Agentic behavior components
        self.pending_channels = set()  # Channels with unprocessed activity
        self.last_check = defaultdict(lambda: datetime(2020, 1, 1, tzinfo=timezone.utc))  # Channel -> last check time (start from Discord era)
        self.cooldown_until = defaultdict(lambda: datetime.now())  # Channel -> when we can talk again
        self.ignored_count = defaultdict(int)
        
        # Proactive engagement components
        self.unprompted_messages = set()  # Track our own provocations to avoid self-responses
        self.last_provocation = {}  # Channel -> timestamp of last provocation
        self.chaos_channels = set()  # Channels we're allowed to provoke

        # Global provocation targeting - track latest message across all servers
        self.latest_message_channel = None  # Channel with globally most recent message
        self.latest_message_time = datetime(2020, 1, 1, tzinfo=timezone.utc)  # Time of globally latest message
        
        # Cost control for web search
        self.daily_search_count = defaultdict(int)  # date -> search count
        self.MAX_DAILY_SEARCHES = 300  # ~$3/day ceiling for curiosity spikes
        
        # Cost control for provocations
        self.daily_provocation_count = defaultdict(int)  # date -> provocation count  
        self.MAX_DAILY_PROVOCATIONS = 4  # Limit provocations to prevent spam
        
        # API throttling
        self.api_sem = asyncio.Semaphore(10)
        self.api_throttler = APIThrottler(min_delay=1.0)

        # Token usage tracking
        self.session_tokens = {
            'input_tokens': 0,
            'output_tokens': 0,
            'thinking_tokens': 0,
            'total_requests': 0
        }
        
        # Image processing platform and limits
        self.platform = os.getenv('CLAUDE_PLATFORM', 'api')  # 'api', 'bedrock', or 'claude_ai'
        
        # Compression strategies for bulletproof image processing
        self.compression_strategies = {
            'jpeg_qualities': [85, 75, 65, 55, 45, 35, 25, 15, 10],
            'png_compressions': [9, 6, 3, 1],
            'webp_qualities': [85, 75, 65, 55, 45, 35, 25, 15],
            'nuclear_resize_factor': 0.7,
            'thumbnail_size': 512
        }
        
        # Target raw size factor to account for Base64 encoding overhead (~33%)
        self.TARGET_RAW_SIZE_FACTOR = 0.73  # Aim for 73% of API limit to be safe after Base64

    def _log_token_usage(self, response, operation_type: str):
        """Log token usage from API response and update session totals"""
        try:
            usage = response.usage
            input_tokens = getattr(usage, 'input_tokens', 0)
            output_tokens = getattr(usage, 'output_tokens', 0)
            thinking_tokens = 0

            # Check for thinking tokens (newer API responses)
            if hasattr(usage, 'thinking_tokens'):
                thinking_tokens = usage.thinking_tokens
            elif hasattr(usage, 'cache_creation_input_tokens'):
                # Alternative field name
                thinking_tokens = getattr(usage, 'cache_creation_input_tokens', 0)

            # Update session totals
            self.session_tokens['input_tokens'] += input_tokens
            self.session_tokens['output_tokens'] += output_tokens
            self.session_tokens['thinking_tokens'] += thinking_tokens
            self.session_tokens['total_requests'] += 1

            total_tokens = input_tokens + output_tokens + thinking_tokens
            session_total = sum([self.session_tokens['input_tokens'],
                               self.session_tokens['output_tokens'],
                               self.session_tokens['thinking_tokens']])

            print(f"  🧮 Tokens [{operation_type}]: in={input_tokens}, out={output_tokens}, thinking={thinking_tokens}, total={total_tokens}")
            print(f"  📊 Session totals: {session_total} tokens across {self.session_tokens['total_requests']} requests")

        except Exception as e:
            print(f"  ⚠️  Error tracking token usage: {e}")

    def _report_session_token_usage(self):
        """Report comprehensive session token usage"""
        try:
            total_tokens = (self.session_tokens['input_tokens'] +
                          self.session_tokens['output_tokens'] +
                          self.session_tokens['thinking_tokens'])

            if total_tokens == 0:
                return

            # Rough cost estimate (approximate pricing)
            # These are rough estimates - actual pricing varies by model and may change
            estimated_input_cost = self.session_tokens['input_tokens'] * 0.000015  # ~$15/1M tokens
            estimated_output_cost = self.session_tokens['output_tokens'] * 0.000075  # ~$75/1M tokens
            estimated_thinking_cost = self.session_tokens['thinking_tokens'] * 0.000015  # Same as input
            total_estimated_cost = estimated_input_cost + estimated_output_cost + estimated_thinking_cost

            print(f"\n📊 TOKEN USAGE REPORT:")
            print(f"  📝 Input tokens: {self.session_tokens['input_tokens']:,}")
            print(f"  💬 Output tokens: {self.session_tokens['output_tokens']:,}")
            print(f"  🧠 Thinking tokens: {self.session_tokens['thinking_tokens']:,}")
            print(f"  📊 Total tokens: {total_tokens:,}")
            print(f"  🔗 Total requests: {self.session_tokens['total_requests']}")
            print(f"  💰 Estimated cost: ${total_estimated_cost:.4f}")
            print()
        except Exception as e:
            print(f"  ⚠️  Error reporting token usage: {e}")

    def _get_platform_limits(self) -> dict:
        """Get Claude API limits based on platform"""
        platform = getattr(self, 'platform', 'api')
        
        if platform == 'bedrock':
            return {
                'single_max_dim': 8000,
                'multi_max_dim': 2000,
                'max_size_bytes': int(3.75 * 1024 * 1024),  # 3.75MB for AWS Bedrock
                'max_images': 20,
                'auto_resize_thresh': 1568
            }
        elif platform == 'claude_ai':
            return {
                'single_max_dim': 8000,
                'multi_max_dim': 2000,
                'max_size_bytes': 5 * 1024 * 1024,  # 5MB for claude.ai
                'max_images': 20,
                'auto_resize_thresh': 1568
            }
        else:  # API default
            return {
                'single_max_dim': 8000,
                'multi_max_dim': 2000,
                'max_size_bytes': 5 * 1024 * 1024,  # 5MB for standard API
                'max_images': 100,
                'auto_resize_thresh': 1568
            }

    # ============================================================================
    # NEW COMPREHENSIVE IMAGE PROCESSING SYSTEM
    # ============================================================================

    def _is_safe_discord_url(self, url: str) -> bool:
        """Security check: Only allow Discord CDN URLs"""
        allowed_domains = [
            'cdn.discordapp.com',
            'media.discordapp.net', 
            'images-ext-1.discordapp.net',
            'images-ext-2.discordapp.net'
        ]
        return any(domain in url for domain in allowed_domains)

    def _determine_media_type(self, content_type: str, url: str) -> str:
        """Determine image media type from headers and URL"""
        if 'jpeg' in content_type or url.lower().endswith(('.jpg', '.jpeg')):
            return "image/jpeg"
        elif 'png' in content_type or url.lower().endswith('.png'):
            return "image/png"
        elif 'gif' in content_type or url.lower().endswith('.gif'):
            return "image/gif"
        elif 'webp' in content_type or url.lower().endswith('.webp'):
            return "image/webp"
        return None

    async def _secure_download(self, url: str, image_num: int) -> tuple[bytes, str]:
        """Download image with security and size limits"""
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        print(f"    ❌ Image {image_num}: HTTP {response.status}")
                        return None, None
                    
                    # Check content length
                    content_length = response.headers.get('content-length')
                    if content_length and int(content_length) > 50 * 1024 * 1024:  # 50MB limit
                        print(f"    ❌ Image {image_num}: Too large to download ({int(content_length)/1024/1024:.1f}MB)")
                        return None, None
                    
                    # Determine media type
                    content_type = response.headers.get('content-type', '').lower()
                    media_type = self._determine_media_type(content_type, url)
                    if not media_type:
                        print(f"    ❌ Image {image_num}: Unsupported format ({content_type})")
                        return None, None
                    
                    # Stream download with size checking
                    image_data = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        image_data.extend(chunk)
                        if len(image_data) > 50 * 1024 * 1024:  # 50MB safety
                            print(f"    ❌ Image {image_num}: Exceeded 50MB during download")
                            return None, None
                    
                    return bytes(image_data), media_type
                    
        except asyncio.TimeoutError:
            print(f"    ❌ Image {image_num}: Download timeout")
            return None, None
        except Exception as e:
            print(f"    ❌ Image {image_num}: Download error - {e}")
            return None, None

    async def _check_dimensions_exceed_limit(self, image_data: bytes, max_dimension: int) -> bool:
        """Quick check if image dimensions exceed limit without full processing"""
        try:
            def check_dims():
                with Image.open(io.BytesIO(image_data)) as img:
                    return img.width > max_dimension or img.height > max_dimension
            
            # Run in executor to avoid blocking
            return await asyncio.get_event_loop().run_in_executor(None, check_dims)
        except Exception:
            return False  # If we can't check, assume it's fine

    async def _check_auto_resize_needed(self, image_data: bytes) -> bool:
        """Check if image exceeds Claude's auto-resize threshold (1568px)"""
        try:
            def check_auto_resize():
                with Image.open(io.BytesIO(image_data)) as img:
                    return max(img.width, img.height) > 1568
            
            return await asyncio.get_event_loop().run_in_executor(None, check_auto_resize)
        except Exception:
            return False

    def _compress_image_bulletproof(self, image_data: bytes, media_type: str, 
                                  max_dimension: int, image_num: int) -> tuple[bytes, str]:
        """BULLETPROOF compression that WILL get image under raw size limit (accounting for Base64)"""
        try:
            platform_limits = self._get_platform_limits()
            img = Image.open(io.BytesIO(image_data))
            original_size = len(image_data)
            
            # Calculate target raw size to account for Base64 overhead
            target_raw_size = int(platform_limits['max_size_bytes'] * self.TARGET_RAW_SIZE_FACTOR)
            
            print(f"      📐 Image {image_num}: {img.width}x{img.height} {img.mode} {original_size/1024/1024:.1f}MB")
            print(f"      🎯 Target raw size: {target_raw_size/1024/1024:.1f}MB (Base64 overhead accounted)")
            
            # Step 1: Resize dimensions if needed
            if img.width > max_dimension or img.height > max_dimension:
                img.thumbnail((max_dimension, max_dimension), RESAMPLE_LANCZOS)
                print(f"      🔄 Image {image_num}: Resized to {img.width}x{img.height}")
            
            # Step 2: Strategy progression - each must meet target_raw_size
            strategies = [
                ('preserve_format', self._try_preserve_format),
                ('jpeg_progressive', self._try_jpeg_progressive),
                ('webp_conversion', self._try_webp_conversion),
                ('nuclear_resize', self._try_nuclear_resize),
                ('thumbnail_fallback', self._try_thumbnail_fallback)
            ]
            
            for strategy_name, strategy_func in strategies:
                result = strategy_func(img, media_type, target_raw_size, image_num)
                if result:
                    compressed_data, final_media_type = result
                    final_size = len(compressed_data)
                    
                    # Double-check size meets target
                    if final_size <= target_raw_size:
                        compression_ratio = (1 - final_size / original_size) * 100
                        print(f"      ✅ Image {image_num}: {strategy_name} succeeded - "
                              f"{original_size/1024/1024:.1f}MB → {final_size/1024/1024:.1f}MB "
                              f"({compression_ratio:.1f}% reduction)")
                        return compressed_data, final_media_type
                    else:
                        print(f"      ⚠️  Image {image_num}: {strategy_name} produced data but still too large "
                              f"({final_size/1024/1024:.1f}MB > {target_raw_size/1024/1024:.1f}MB target). "
                              f"Trying next strategy.")
            
            print(f"      ❌ Image {image_num}: ALL compression strategies failed to meet target raw size")
            return None
            
        except Exception as e:
            print(f"      💥 Image {image_num}: Compression error - {e}")
            return None

    def _try_preserve_format(self, img: Image.Image, media_type: str, target_raw_size: int, image_num: int) -> tuple[bytes, str]:
        """Strategy 1: Try to preserve original format with optimization"""
        output = io.BytesIO()
        
        try:
            if media_type == "image/gif":
                img.save(output, format='GIF', save_all=True, optimize=True)
            elif media_type == "image/png":
                img.save(output, format='PNG', optimize=True, compress_level=9)
            elif media_type == "image/webp":
                img.save(output, format='WEBP', quality=85, optimize=True)
            else:  # JPEG
                img.save(output, format='JPEG', quality=85, optimize=True)
            
            if len(output.getvalue()) <= target_raw_size:
                return output.getvalue(), media_type
                
        except Exception as e:
            print(f"      ⚠️  Image {image_num}: Format preservation failed - {e}")
        
        return None

    def _try_jpeg_progressive(self, img: Image.Image, media_type: str, target_raw_size: int, image_num: int) -> tuple[bytes, str]:
        """Strategy 2: Progressive JPEG quality reduction"""
        # Convert to RGB if needed
        if img.mode in ('RGBA', 'P'):
            rgb_img = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'RGBA':
                rgb_img.paste(img, mask=img.split()[-1])
            else:
                rgb_img.paste(img)
            img = rgb_img
        
        for quality in self.compression_strategies['jpeg_qualities']:
            output = io.BytesIO()
            try:
                img.save(output, format='JPEG', quality=quality, optimize=True)
                if len(output.getvalue()) <= target_raw_size:
                    return output.getvalue(), "image/jpeg"
            except Exception:
                continue
        
        return None

    def _try_webp_conversion(self, img: Image.Image, media_type: str, target_raw_size: int, image_num: int) -> tuple[bytes, str]:
        """Strategy 3: WebP conversion with quality reduction"""
        for quality in self.compression_strategies['webp_qualities']:
            output = io.BytesIO()
            try:
                img.save(output, format='WEBP', quality=quality, optimize=True)
                if len(output.getvalue()) <= target_raw_size:
                    return output.getvalue(), "image/webp"
            except Exception:
                continue
        
        return None

    def _try_nuclear_resize(self, img: Image.Image, media_type: str, target_raw_size: int, image_num: int) -> tuple[bytes, str]:
        """Strategy 4: Aggressive resize + compression"""
        # Calculate new dimensions based on file size ratio
        nuclear_factor = self.compression_strategies['nuclear_resize_factor']
        new_width = int(img.width * nuclear_factor)
        new_height = int(img.height * nuclear_factor)
        
        print(f"      🚨 Image {image_num}: Nuclear resize {img.width}x{img.height} → {new_width}x{new_height}")
        
        resized_img = img.resize((new_width, new_height), RESAMPLE_LANCZOS)
        
        # Convert to RGB if needed
        if resized_img.mode in ('RGBA', 'P'):
            rgb_img = Image.new('RGB', resized_img.size, (255, 255, 255))
            if resized_img.mode == 'RGBA':
                rgb_img.paste(resized_img, mask=resized_img.split()[-1])
            else:
                rgb_img.paste(resized_img)
            resized_img = rgb_img
        
        # Try multiple formats
        formats_to_try = [
            ('JPEG', {'quality': 15, 'optimize': True}),
            ('WEBP', {'quality': 15, 'optimize': True}),
        ]
        
        for format_name, kwargs in formats_to_try:
            output = io.BytesIO()
            try:
                resized_img.save(output, format=format_name, **kwargs)
                if len(output.getvalue()) <= target_raw_size:
                    media_type = f"image/{format_name.lower()}"
                    return output.getvalue(), media_type
            except Exception:
                continue
        
        return None

    def _try_thumbnail_fallback(self, img: Image.Image, media_type: str, target_raw_size: int, image_num: int) -> tuple[bytes, str]:
        """Strategy 5: Last resort - tiny thumbnail"""
        thumb_size = self.compression_strategies['thumbnail_size']
        print(f"      🆘 Image {image_num}: Last resort thumbnail {thumb_size}x{thumb_size}")
        
        # Create thumbnail
        img.thumbnail((thumb_size, thumb_size), RESAMPLE_LANCZOS)
        
        # Convert to RGB
        if img.mode in ('RGBA', 'P'):
            rgb_img = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'RGBA':
                rgb_img.paste(img, mask=img.split()[-1])
            else:
                rgb_img.paste(img)
            img = rgb_img
        
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=10, optimize=True)
        
        if len(output.getvalue()) <= target_raw_size:
            return output.getvalue(), "image/jpeg"
        
        return None

    async def _download_and_encode_image_v2(self, attachment_url: str, 
                                          image_num: int, total_images: int) -> dict:
        """Download and process a single image with bulletproof compression"""
        try:
            # Security: Only allow Discord CDN URLs
            if not self._is_safe_discord_url(attachment_url):
                print(f"    🚫 Image {image_num}: Rejected non-Discord URL")
                return None
            
            # Download with streaming and safety limits
            image_data, original_media_type = await self._secure_download(attachment_url, image_num)
            if not image_data:
                return None
            
            platform_limits = self._get_platform_limits()
            original_size = len(image_data)
            
            # Calculate effective max raw size to account for Base64 overhead (~33%)
            effective_max_raw_size = int(platform_limits['max_size_bytes'] * self.TARGET_RAW_SIZE_FACTOR)
            
            # Determine max dimension based on corrected logic from FinalTouches.md
            if total_images > 20:
                max_dimension = platform_limits['multi_max_dim']
            else:
                max_dimension = platform_limits['single_max_dim']
            
            print(f"    🖼️  Image {image_num}: {original_size/1024/1024:.1f}MB, max_dim={max_dimension}")
            print(f"    📏 Effective raw size limit: {effective_max_raw_size/1024/1024:.1f}MB (accounts for Base64 overhead)")
            
            # Check if processing needed (using effective size limit)
            needs_processing = (
                original_size > effective_max_raw_size or 
                await self._check_dimensions_exceed_limit(image_data, max_dimension) or
                await self._check_auto_resize_needed(image_data)
            )
            
            if needs_processing:
                # Process in thread pool to avoid blocking
                processed_result = await asyncio.get_event_loop().run_in_executor(
                    None, 
                    self._compress_image_bulletproof,
                    image_data, original_media_type, max_dimension, image_num
                )
                
                if not processed_result:
                    return None
                
                image_data, final_media_type = processed_result
            else:
                final_media_type = original_media_type
                print(f"    ✅ Image {image_num}: No processing needed")
            
            # Final verification
            final_size = len(image_data)
            if final_size > platform_limits['max_size_bytes']:
                print(f"    ❌ Image {image_num}: Still too large after processing: {final_size/1024/1024:.1f}MB")
                return None
            
            # Convert to Claude API format
            base64_data = base64.b64encode(image_data).decode('utf-8')
            compression_info = f"({original_size/1024/1024:.1f}MB→{final_size/1024/1024:.1f}MB)" if needs_processing else ""
            
            print(f"    ✅ Image {image_num}: Ready {final_media_type} {final_size/1024:.1f}KB {compression_info}")
            
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": final_media_type,
                    "data": base64_data
                }
            }
            
        except Exception as e:
            print(f"    ❌ Image {image_num}: Processing failed - {e}")
            return None

    async def _process_single_image_with_semaphore(self, semaphore: asyncio.Semaphore, 
                                                 url: str, image_num: int, total_images: int) -> dict:
        """Process a single image with concurrency control"""
        async with semaphore:
            return await self._download_and_encode_image_v2(url, image_num, total_images)

    async def process_message_images(self, message: discord.Message) -> list[dict]:
        """Main entry point: Process ALL images from a Discord message"""
        image_attachments = [
            att for att in message.attachments 
            if any(att.filename.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp'])
        ]
        
        if not image_attachments:
            return []
        
        platform_limits = self._get_platform_limits()
        total_images = len(image_attachments)
        print(f"    🖼️  Processing {total_images} image(s) from {message.author.name}")
        
        # Check Claude limits
        if total_images > platform_limits['max_images']:
            print(f"    ❌ Too many images: {total_images} > {platform_limits['max_images']}")
            return []
        
        # Process images concurrently but with semaphore to limit memory usage
        semaphore = asyncio.Semaphore(3)  # Max 3 concurrent downloads
        tasks = []
        
        for i, attachment in enumerate(image_attachments):
            task = self._process_single_image_with_semaphore(
                semaphore, attachment.url, i + 1, total_images
            )
            tasks.append(task)
        
        # Wait for all images to process
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter successful results
        processed_images = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"    ❌ Image {i+1} failed: {result}")
            elif result:
                processed_images.append(result)
        
        success_rate = len(processed_images) / total_images * 100
        print(f"    📊 Processed {len(processed_images)}/{total_images} images ({success_rate:.1f}% success)")
        
        return processed_images

    def get_conversation_momentum(self, messages):
        """Detect if conversation is heating up or cooling down - calibrated for small servers"""
        if len(messages) < 2:
            return "cold"
        
        # Sort messages chronologically (Discord history is newest-first)
        sorted_messages = sorted(messages, key=lambda m: m.created_at)
        
        # Time between messages
        time_gaps = []
        for i in range(1, len(sorted_messages)):
            gap = (sorted_messages[i].created_at - sorted_messages[i-1].created_at).total_seconds()
            time_gaps.append(gap)
        
        avg_gap = sum(time_gaps) / len(time_gaps)
        
        # Small server calibration: much longer gaps still count as active
        if avg_gap < 900:  # 10 minutes = hot (was 30s for busy servers)
            return "hot"
        elif avg_gap < 3600:  # 1 hour = warm (was 2min for busy servers)
            return "warm"
        else:  # Slow/dying conversation
            return "cold"

    async def on_ready(self):
        print("\n" + "="*60)
        print(f"🤖 SLH-01 BOT ONLINE")
        print(f"🆔 User: {self.user} (ID: {self.user.id})")
        print(f"🌐 Connected to {len(self.guilds)} servers")
        print("="*60)
        
        for guild in self.guilds:
            text_channels = [ch for ch in guild.channels if isinstance(ch, discord.TextChannel)]
            member_count = guild.member_count or guild.approximate_member_count or "unknown"
            print(f'  └─ {guild.name}: {len(text_channels)} text channels, {member_count} members')

        print(f"\n📥 Loading recent message history for context...")
        await self._load_recent_history()

        if self.context_manager.context_exists():
            available = self.context_manager.get_available_contexts()
            print(f"🧠 Comprehensive context summaries loaded:")
            print(f"  └─ User Profiles: {len(available['user_profiles'])}")
            print(f"  └─ Channel Summaries: {len(available['channel_summaries'])}")
            # Removed the "Will use ALL summaries..." line as it was misleading with the old caching attempt.
            # The new method will use it more intelligently.
        else:
            print(f"⚠️  No context summaries found. Run discord_analyzer.py first for enhanced contextual awareness.")


    async def _load_recent_history(self):
        total_loaded = 0
        total_channels = sum(len(guild.text_channels) for guild in self.guilds)
        
        # Limit memory usage - don't load more than 5000 total messages at startup
        max_total_messages = 5000
        max_messages_per_channel = min(100, max_total_messages // total_channels) if total_channels > 0 else 200
        
        print(f"📥 Loading max {max_messages_per_channel} messages per channel ({total_channels} channels)")
        
        for guild in self.guilds:
            for channel in guild.text_channels:
                if not channel.permissions_for(guild.me).read_message_history:
                    print(f"  └─ #{channel.name}: No permission to read history")
                    continue
                try:
                    messages_loaded = 0
                    history = []
                    async for message in channel.history(limit=max_messages_per_channel):
                        # Skip bot messages during startup loading to reduce noise
                        if not message.author.bot:
                            history.append(message)
                        elif message.author == self.user:
                            # But keep our own messages for context
                            history.append(message)
                    
                    for message in reversed(history): # Add oldest of these recent ones first
                        self.memory.add_message(channel.id, message)
                        messages_loaded += 1
                    
                    # Set last_check to most recent message to prevent re-processing
                    if history:
                        self.last_check[channel.id] = history[0].created_at  # Most recent message
                    
                    if messages_loaded > 0:
                        print(f"  └─ #{channel.name}: {messages_loaded} recent messages loaded")
                    total_loaded += messages_loaded
                    
                    # Hard limit check to prevent memory explosion
                    if total_loaded >= max_total_messages:
                        print(f"  ⚠️  Reached memory limit ({max_total_messages} messages), stopping history load")
                        return
                        
                except discord.Forbidden:
                    print(f"  └─ #{channel.name}: No access (Forbidden)")
                except Exception as e:
                    print(f"  └─ #{channel.name}: Error loading history - {e}")
        print(f"📝 Total recent messages loaded: {total_loaded}")
        
        # Load context summaries
        if hasattr(self, 'context_manager') and self.context_manager.context_exists():
            available = self.context_manager.get_available_contexts()
            print(f"\n🧠 Comprehensive context loaded:")
            print(f"  └─ User Profiles: {len(available['user_profiles'])}")
            print(f"  └─ Channel Summaries: {len(available['channel_summaries'])}")
        else:
            print(f"\n⚠️  No context summaries found")
        
        # Start the periodic conversation check
        if not self.check_conversations.is_running():
            self.check_conversations.start()
            print(f"\n🕐 Agentic conversation checker started (30s intervals)")
        
        # Start the scheduled provocation system
        if not self.scheduled_provocation.is_running():
            self.scheduled_provocation.start()
            print(f"🔥 Scheduled provocation system started (every 1.5 hours)")
            
        print(f"\n✅ SLH-01 is ready to participate AND provoke conversations!")
        print(f"🧮 Token tracking initialized - session will track all API usage")
        print(f"📊 Initial token counts: {self.session_tokens}")
        print("="*60 + "\n")

    @tasks.loop(seconds=30)  # Check every 30 seconds - compromise between responsiveness and API efficiency
    async def check_conversations(self):
        """Periodically check channels for conversation opportunities"""
        if not self.pending_channels:
            return
            
        print(f"\n🔄 PERIODIC CHECK: {len(self.pending_channels)} channel(s) with activity")
            
        for channel_id in list(self.pending_channels):
            # Get channel first
            channel = self.get_channel(channel_id)
            if not channel:
                # Channel no longer exists, remove it
                self.pending_channels.discard(channel_id)
                continue
                
            # Skip if still in cooldown
            cooldown_remaining = (self.cooldown_until[channel_id] - datetime.now()).total_seconds()
            if cooldown_remaining > 0:
                # If cooldown is older than 30 minutes, clean it up
                if cooldown_remaining > 1800:  # 30 minutes
                    print(f"  🧹 #{channel.name}: Cleaning up old cooldown")
                    self.pending_channels.discard(channel_id)
                    self.cooldown_until[channel_id] = datetime.now()
                    continue
                else:
                    print(f"  ⏸️  #{channel.name}: In cooldown ({cooldown_remaining/60:.1f}m remaining)")
                    continue
                
            try:
                # Get messages since last check
                messages_since_check = []
                try:
                    async for msg in channel.history(after=self.last_check[channel_id], limit=20):  # Reduced limit for efficiency
                        if msg.author != self.user:  # Don't include our own messages
                            messages_since_check.append(msg)
                except Exception as fetch_error:
                    print(f"  ⚠️  #{channel.name}: Error fetching history - {fetch_error}")
                    continue
                
                # Skip if no new messages since last check
                if not messages_since_check:
                    self.pending_channels.discard(channel_id)
                    continue
                    
                # Analyze the conversation chunk
                momentum = self.get_conversation_momentum(messages_since_check)
                momentum_emoji = "🔥" if momentum == "hot" else "🌡️" if momentum == "warm" else "❄️"
                print(f"  🔍 #{channel.name}: Analyzing {len(messages_since_check)} new message(s) ({momentum_emoji} {momentum})")
                response_plan = await self.analyze_conversation_chunk(
                    channel, 
                    messages_since_check,
                    self.memory.get_context(channel_id)
                )
                
                # Execute response plan
                if response_plan and response_plan.get('should_respond') and response_plan.get('responses'):
                    strategy = response_plan.get('response_strategy', 'unknown')
                    response_count = len(response_plan.get('responses', []))
                    print(f"  ✅ #{channel.name}: Will respond with {response_count} message(s) ({strategy})")
                    print(f"     Reasoning: {response_plan.get('reasoning', 'No reason given')}")
                    await self.execute_response_plan(channel, response_plan)
                else:
                    print(f"  🔇 #{channel.name}: No response needed")
                    if response_plan and response_plan.get('reasoning'):
                        print(f"     Reasoning: {response_plan['reasoning']}")
                    
            except Exception as e:
                print(f"❌ Error checking {channel.name}: {e}")
                
            # Update last check time to most recent message to prevent double-processing on restart
            if messages_since_check:
                self.last_check[channel_id] = messages_since_check[0].created_at  # Most recent message
            else:
                self.last_check[channel_id] = datetime.now(timezone.utc)
            self.pending_channels.discard(channel_id)
        
        # Clean up old unprompted messages (older than 24 hours) to prevent memory bloat
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)
        old_message_ids = set()
        for msg_id in self.unprompted_messages:
            try:
                # Discord message IDs contain timestamp info - extract it
                timestamp = datetime.fromtimestamp(((msg_id >> 22) + 1420070400000) / 1000)
                if timestamp < cutoff_time:
                    old_message_ids.add(msg_id)
            except:
                # If timestamp extraction fails, assume it's old
                old_message_ids.add(msg_id)
        
        if old_message_ids:
            self.unprompted_messages -= old_message_ids
            print(f"  🧹 Cleaned up {len(old_message_ids)} old unprompted message IDs")
        
        # Clean up old daily search counts (weekly cleanup to prevent RAM leak)
        today = datetime.now(timezone.utc).date()
        old_dates = [date for date in list(self.daily_search_count.keys()) 
                     if (today - date).days > 7]
        for old_date in old_dates:
            del self.daily_search_count[old_date]
        if old_dates:
            print(f"  🧹 Cleaned up {len(old_dates)} old daily search count entries")
        
        # Clean up old daily provocation counts (weekly cleanup to prevent RAM leak)
        old_provocation_dates = [date for date in list(self.daily_provocation_count.keys()) 
                                if (today - date).days > 7]
        for old_date in old_provocation_dates:
            del self.daily_provocation_count[old_date]
        if old_provocation_dates:
            print(f"  🧹 Cleaned up {len(old_provocation_dates)} old daily provocation count entries")

        # Periodic token usage report (every 10th cleanup cycle)
        if self.session_tokens['total_requests'] > 0 and self.session_tokens['total_requests'] % 10 == 0:
            self._report_session_token_usage()

    async def on_message(self, message: discord.Message):
        """Just mark channels as having activity - agentic approach"""
        # Store message in memory (but exclude bot provocations to prevent pollution)
        if message.id not in self.unprompted_messages:
            self.memory.add_message(message.channel.id, message)
        else:
            print(f"  🔇 Skipping memory storage for our own provocation: {message.id}")

        if message.author == self.user:
            return

        # Update globally latest message tracking for provocation targeting
        message_time = message.created_at
        if message_time.tzinfo is None:
            message_time = message_time.replace(tzinfo=timezone.utc)

        if message_time > self.latest_message_time:
            self.latest_message_time = message_time
            self.latest_message_channel = message.channel
            print(f"  🎯 Updated latest message tracker: #{message.channel.name} ({message_time.strftime('%H:%M:%S')})")
            
        # Add to pending channels for next check cycle
        self.pending_channels.add(message.channel.id)
        
        # Quick activity log
        channel_name = f"#{message.channel.name}" if hasattr(message.channel, 'name') else f"DM-{message.channel.id}"
        image_count = len([att for att in message.attachments if any(att.filename.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp'])])
        image_indicator = f" +{image_count}📷" if image_count > 0 else ""
        
        # Show activity but keep it minimal
        content_preview = message.content[:40] + "..." if len(message.content) > 40 else message.content
        print(f"📝 {channel_name} | {message.author.name}: {content_preview}{image_indicator}")
        
        # Handle urgent mentions immediately (bypass periodic check)
        if self.user in message.mentions:
            has_images = bool([att for att in message.attachments if any(att.filename.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp'])])
            mention_type = " with image(s)" if has_images else ""
            print(f"🚨 URGENT: Direct mention detected{mention_type} - bypassing periodic check")
            asyncio.create_task(self.handle_urgent_mention(message))

    async def analyze_conversation_chunk(self, channel, new_messages, full_context):
        """Analyze a chunk of conversation and decide on responses"""
        
        # Skip if any new messages are our own unprompted provocations
        for msg in new_messages:
            if msg.id in self.unprompted_messages:
                print(f"  🔇 Skipping analysis - contains our own provocation")
                return {"should_respond": False, "responses": [], "reasoning": "contains_own_provocation"}
        
        # Check per-user cooldowns - filter out users on cooldown instead of blocking all responses
        now = datetime.now(timezone.utc)
        eligible_messages = []
        skipped_users = []

        for msg in new_messages:
            user_id = msg.author.id
            last_reply = self.last_user_reply[user_id]
            if (now - last_reply).total_seconds() < 40:  # 40 second per-user cooldown
                skipped_users.append(msg.author.name)
            else:
                eligible_messages.append(msg)

        # If no eligible messages after cooldown filtering, skip
        if not eligible_messages:
            if skipped_users:
                print(f"  🔇 All users on cooldown: {', '.join(set(skipped_users))}")
                return {"should_respond": False, "responses": [], "reasoning": "all_users_on_cooldown"}
            else:
                return {"should_respond": False, "responses": [], "reasoning": "no_eligible_messages"}

        # Use eligible_messages instead of new_messages for the rest of the analysis
        new_messages = eligible_messages
        if skipped_users:
            print(f"  ⏭️  Filtered out users on cooldown: {', '.join(set(skipped_users))}")
        
        # Format the new messages in chronological order and process ALL images
        new_msgs_formatted = []
        all_processed_images_for_api = []
        
        for msg in reversed(new_messages):  # Chronological order
            content = msg.content or "[no text]"
            new_msgs_formatted.append(f"{msg.author.name}: {content}")
            
            # Process ALL images from this message using the new comprehensive system
            if msg.attachments:
                processed_images_from_msg = await self.process_message_images(msg)
                if processed_images_from_msg:
                    all_processed_images_for_api.extend(processed_images_from_msg)
        
        new_msgs_formatted = "\n".join(new_msgs_formatted)
        
        # Detect conversation momentum
        momentum = self.get_conversation_momentum(new_messages)
        
        # Check if we were part of the conversation before
        our_last_message = None
        for msg in full_context[-20:]:
            if msg.get('author_id') == self.user.id:
                our_last_message = msg

        # Get current date/time for context
        now = datetime.now()
        current_date = now.strftime('%Y-%m-%d %H:%M:%S')
        current_month_year = now.strftime('%B %Y')
                
        # Format context
        context_formatted = "\n".join([
            f"{msg['author']}: {msg['content']}" 
            for msg in full_context[-15:]  # Show last 15 for context
        ])
        
        # Get comprehensive background context if available
        comprehensive_context = ""
        if hasattr(self, 'context_manager') and self.context_manager.context_exists():
            comprehensive_context = self.context_manager.get_comprehensive_context(
                new_messages[0] if new_messages else None,
                full_context
            )

        prompt = f"""You are SLH-01 checking Discord after being away for a bit. 

BACKGROUND CONTEXT (if available):
{comprehensive_context[:1000] + '...' if len(comprehensive_context) > 1000 else comprehensive_context}

RECENT CONVERSATION HISTORY:
{context_formatted}

NEW MESSAGES since you last checked (chronologically):
{new_msgs_formatted}

{f"IMAGES POSTED: {len(all_processed_images_for_api)} image(s) processed for analysis" if all_processed_images_for_api else ""}

CONVERSATION MOMENTUM: {momentum.upper()} ({"rapid exchanges" if momentum == "hot" else "steady discussion" if momentum == "warm" else "quiet/slow"})

Current time: {current_date}
Your last message was: {our_last_message['content'] if our_last_message else 'N/A'}

Analyze this conversation and decide if you have something worth adding.

RESPONSE TRIGGERS (in order of priority):
1. **Direct mention/question** - Someone explicitly needs your input
2. **Genuinely funny observation** - You have a perfect one-liner that actually adds value  
3. **Critical correction** - Someone's dangerously wrong (not just slightly off)
4. **Natural conversation flow** - You'd naturally chime in if you were actively chatting
5. **Revival opportunity** - Dead for 20+ min AND you have something interesting

RESPONSE RATES (be ruthless about quality):
- 🧊 Cold: ~10% response rate (basically only if mentioned)
- 🌡️ Warm: ~25% response rate (mentioned or perfect opportunity)
- 🔥 Hot: ~40% response rate (natural participation, avoid exhaustion)

MESSAGE STRATEGY:
- **Single message**: Default. One good line > two mediocre ones
- **Double message**: Only when genuinely mimicking natural catch-up behavior
  - First: reaction/acknowledgment
  - Second: new thought/question (with 2-8 second gap)
- **None**: Your default state. Silence is golden.

WEB SEARCH: Only if explicitly asked OR critical for accuracy
Format: Be specific with timeframe and context
- "what's happening with X" → search_query: "X news {current_month_year}"
- Technical questions → search_query: "[specific tech] documentation 2025"

Return JSON:
{{
    "should_respond": false,  // Default. Only true if genuinely worth it
    "response_strategy": "none|single|double",
    "responses": [
        {{
            "target": "specific message/topic you're addressing",
            "message": "your actual response",
            "delay": 2-8,  // seconds between if multiple
            "use_web_search": false,
            "search_query": null,  // specific terms if needed
            "send_image": null
        }}
    ],
    "reasoning": "brief explanation why you decided it (be honest if you're forcing it)"
}}

Remember: If you're debating whether to respond, you probably shouldn't."""

        try:
            async with self.api_sem:
                await self.api_throttler.throttle()
                
                # Prepare content with images if any
                if all_processed_images_for_api:
                    # Add images first, then text
                    content_parts = all_processed_images_for_api + [{"type": "text", "text": prompt}]
                    print(f"    🖼️  Sending {len(all_processed_images_for_api)} image(s) to Claude for analysis")
                else:
                    content_parts = prompt
                
                response = await self.anthropic.messages.create(
                    model="claude-opus-4-1-20250805",
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": content_parts}],
                    thinking={
                        "type": "enabled",
                        "budget_tokens": 2048  # Restore thinking for response planning
                    }
                )

                # Track token usage
                self._log_token_usage(response, "conversation_analysis")
                
                # Parse response
                response_text = None
                for content_block in response.content:
                    if hasattr(content_block, 'text') and content_block.type == 'text':
                        response_text = content_block.text.strip()
                        break
                
                if response_text:
                    # Extract JSON
                    json_match = re.search(r"```json\s*([\s\S]*?)\s*```|({[\s\S]*})", response_text)
                    if json_match:
                        json_str = json_match.group(1) or json_match.group(2)
                        plan_json = json.loads(json_str.strip())
                        # Add triggering messages for cooldown logic in execute_response_plan
                        plan_json['triggering_messages_raw'] = new_messages  # new_messages is eligible_messages
                        return plan_json
                    else:
                        plan_json = json.loads(response_text)
                        plan_json['triggering_messages_raw'] = new_messages
                        return plan_json
                        
        except Exception as e:
            print(f"❌ Error analyzing conversation chunk: {e}")
            return {"should_respond": False, "responses": [], "reasoning": "analysis_error"}

    async def execute_response_plan(self, channel, plan):
        """Execute a response plan with natural timing and enhanced capabilities"""
        
        if not plan.get('should_respond') or not plan.get('responses'):
            return
        
        # Check rate limits before responding
        can_respond, reason = self.rate_limiter.check(channel.id)
        if not can_respond:
            print(f"  🚫 Rate limited in #{channel.name}: {reason}")
            self.rate_limiter.record_ignored(channel.id)
            return
            
        # For multiple responses, space them out naturally
        for i, response in enumerate(plan['responses']):
            if i > 0:  # Delay between multiple messages
                delay = response.get('delay', 2)
                print(f"⏳ Waiting {delay}s before next message...")
                await asyncio.sleep(delay)
            
            # Handle web search with SPECIFIC query
            message_content = response['message']
            if response.get('use_web_search', False):
                search_query = response.get('search_query')
                if search_query:
                    print(f"    🌐 Web search requested: '{search_query}'")
                    web_result = await self.analyze_with_web_search(search_query, f"Channel: #{channel.name}")
                    if web_result and web_result not in ["couldn't find what you're looking for, sorry", "web search shit the bed, try again later"]:
                        # Use web search result as the message content
                        message_content = web_result
                        print(f"    ✅ Using web search result ({len(message_content)} chars)")
                    else:
                        print(f"    ⚠️  Web search failed, using original response")
                else:
                    print(f"    ❌ Web search requested but no search_query provided")
            
            # Handle typing with proper timing for long messages
            typing_time = max(len(message_content) / 40, 2)  # Slower typing for longer content
            if response.get('use_web_search', False):
                typing_time += 3  # Extra typing time for web search
            typing_time = min(typing_time, 15)  # Cap at 15 seconds
            
            print(f"    ⌨️  Typing for {typing_time:.1f}s...")
            
            # Keep typing indicator alive for long responses
            elapsed = 0
            while elapsed < typing_time:
                async with channel.typing():
                    chunk_time = min(7, typing_time - elapsed)
                    await asyncio.sleep(chunk_time)
                    elapsed += chunk_time
            
            # Send image first if specified
            if response.get('send_image'):
                image_url = response['send_image']
                print(f"    📸 Sending image: {image_url}")
                await self.send_image(channel, image_url=image_url)
                await asyncio.sleep(1)
            
            # Handle Discord's 2000 character limit by splitting naturally
            if len(message_content) > 1900:
                # Split on natural boundaries (sentences, then paragraphs)
                parts = []
                current_part = ""
                
                # Try to split on sentences first
                sentences = message_content.replace('\n\n', '\n').split('. ')
                
                for sentence in sentences:
                    if len(current_part + sentence + '. ') > 1900:
                        if current_part:
                            parts.append(current_part.strip())
                            current_part = sentence + '. '
                        else:
                            # Single sentence too long, force split
                            parts.append(sentence[:1900])
                            current_part = sentence[1900:] + '. '
                    else:
                        current_part += sentence + '. '
                
                if current_part:
                    parts.append(current_part.strip())
                
                # Send parts with small delays
                for part_i, part in enumerate(parts):
                    if part_i > 0:
                        print(f"    ⌨️  Typing part {part_i+1}/{len(parts)} for 2s...")
                        async with channel.typing():
                            await asyncio.sleep(2)
                    
                    sent = await channel.send(part)
                    part_preview = part[:60] + "..." if len(part) > 60 else part
                    print(f"    📤 Sent part {part_i+1}/{len(parts)}: '{part_preview}'")
            else:
                # Single message
                sent = await channel.send(message_content)
                message_preview = message_content[:60] + "..." if len(message_content) > 60 else message_content
                print(f"    📤 Sent: '{message_preview}'")
            
            # Record response for rate limiting and user cooldowns
            if i == 0:  # Only record once per response plan
                self.rate_limiter.record_response(channel.id)
                
                # Only put users on cooldown if we directly addressed them or they were mentioned
                now = datetime.now(timezone.utc)
                
                # Get the triggering messages that led to this response plan
                triggering_messages = plan.get('triggering_messages_raw', [])
                
                # Check if our response mentions specific users
                mentioned_users = set()
                for response_item in plan['responses']:
                    response_text = response_item.get('message', '')
                    # Look for @username patterns or direct names in our response
                    # Check against users from the triggering messages
                    for msg in triggering_messages:
                        if msg.author.name.lower() in response_text.lower():
                            mentioned_users.add(msg.author.id)
                
                # Apply targeted cooldowns
                recent_message_authors = [msg.author.id for msg in triggering_messages]
                
                if len(recent_message_authors) == 1:
                    # Direct response to one person - put them on cooldown
                    self.last_user_reply[recent_message_authors[0]] = now
                    print(f"  ⏳ Cooldown for single author: {triggering_messages[0].author.name}")
                
                # Cooldown users explicitly mentioned in the bot's response text
                for user_id in mentioned_users:
                    self.last_user_reply[user_id] = now
                    # Find user name for logging
                    user_name_for_log = "Unknown"
                    for msg in triggering_messages:
                        if msg.author.id == user_id:
                            user_name_for_log = msg.author.name
                            break
                    print(f"  ⏳ Cooldown for mentioned user: {user_name_for_log}")
                
                asyncio.create_task(self.track_engagement(sent))
        
        # Set cooldown based on how much we said (tiered compromise settings)
        response_count = len(plan['responses'])
        if response_count == 1:
            cooldown_minutes = 0.75  # 45 seconds for single replies
        elif response_count <= 3:
            cooldown_minutes = 1.25  # 75 seconds for multiple replies
        else:
            cooldown_minutes = 1.75  # 105 seconds for heavy bursts
            
        self.cooldown_until[channel.id] = datetime.now() + timedelta(minutes=cooldown_minutes)
        print(f"  🔇 #{channel.name}: Cooldown set for {cooldown_minutes} minutes ({response_count} message{'s' if response_count != 1 else ''})")

    async def handle_urgent_mention(self, message):
        """Handle direct mentions immediately (bypass periodic check)"""
        try:
            print(f"  🔍 Analyzing urgent mention from {message.author.name}")
            
            # Get recent context
            context = self.memory.get_context(message.channel.id)
            
            # Analyze just this mention
            response_plan = await self.analyze_conversation_chunk(
                message.channel,
                [message],  # Just the mention message
                context
            )
            
            # Execute if needed
            if response_plan and response_plan.get('should_respond'):
                print(f"  ✅ Responding to urgent mention")
                await self.execute_response_plan(message.channel, response_plan)
            else:
                print(f"  🔇 No response needed for mention")
                
        except Exception as e:
            print(f"  ❌ Error handling urgent mention: {e}")

    async def track_engagement(self, message):
        """Track user engagement with bot responses to adjust ignore count"""
        try:
            await asyncio.sleep(30)  # Wait 30 seconds to see reactions
            
            # Refresh message to get current reactions
            fresh_message = await message.channel.fetch_message(message.id)
            
            # Check for positive engagement indicators
            engagement_detected = False
            if fresh_message.reactions:
                print(f"  📊 Positive engagement: {len(fresh_message.reactions)} reaction(s)")
                engagement_detected = True
            
            # Check if anyone replied to our message (basic check)
            async for msg in message.channel.history(after=message.created_at, limit=10):
                if msg.reference and msg.reference.message_id == message.id:
                    print(f"  📊 Positive engagement: Reply from {msg.author.name}")
                    engagement_detected = True
                    break
            
            # Record engagement with rate limiter
            if engagement_detected:
                self.rate_limiter.record_engagement(message.channel.id)
            
        except Exception as e:
            print(f"  ❌ Error tracking engagement: {e}")

    async def on_reaction_add(self, reaction, user):
        """Handle reactions to track engagement"""
        if user == self.user:
            return
        
        # If someone reacted to our message, record engagement
        if reaction.message.author == self.user:
            print(f"  📊 Reaction engagement: {user.name} reacted {reaction.emoji}")
            self.rate_limiter.record_engagement(reaction.message.channel.id)


    async def _generate_provocation(self, channel):
        """Generate a contextual conversation starter"""
        recent_context = self.memory.get_context(channel.id, limit=30)
        
        # Format recent context for Claude
        context_formatted = "\n".join([
            f"{msg['author']}: {msg['content']}" 
            for msg in recent_context[-15:]  # Last 15 messages for context
        ])
        
        # Get comprehensive background if available
        comprehensive_context = ""
        if hasattr(self, 'context_manager') and self.context_manager.context_exists():
            comprehensive_context = self.context_manager.get_comprehensive_context(
                None,  # No specific message
                recent_context
            )

        prompt = f"""You're SLH-01. The #{channel.name} channel could use some energy, but only if you have something genuinely interesting to add.

BACKGROUND: {comprehensive_context[:600]}
RECENT CHAT: {context_formatted}

Consider if ANY of these would actually improve the vibe:

1. **Genuine curiosity** - "wait, did anyone figure out why [interesting thing] happens?"
2. **Useful observation** - point out something helpful others might've missed  
3. **Quality shitpost** - only if you have something actually funny/absurd
4. **Thoughtful hot take** - a perspective that's controversial but defensible
5. **Random technical challenge** - "bet nobody here can [interesting challenge]"

HARD REQUIREMENTS:
- Only post if it's genuinely worth interrupting the quiet
- Reference something specific from recent context 
- Would you find this interesting if someone else posted it?
- Maximum 2 sentences
- If nothing good comes to mind, output: "SKIP"

You're not bored—you just noticed something worth mentioning. Or you didn't, and you'll stay quiet."""

        try:
            async with self.api_sem:
                await self.api_throttler.throttle()
                
                response = await self.anthropic.messages.create(
                    model="claude-opus-4-1-20250805",
                    max_tokens=4096,  # Must be > thinking budget
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    thinking={
                        "type": "enabled",
                        "budget_tokens": 2048  # Match compromise setting
                    }
                )

                # Track token usage
                self._log_token_usage(response, "provocation_generation")
                
                # Extract text response
                response_text = None
                for content_block in response.content:
                    if hasattr(content_block, 'text') and content_block.type == 'text':
                        response_text = content_block.text.strip()
                        break
                
                return response_text or "honestly this channel's too quiet. someone say something interesting"
                
        except Exception as e:
            print(f"❌ Error generating provocation: {e}")
            # Fallback provocations
            fallbacks = [
                "christ this place is dead. what's everyone working on?",
                "unpopular opinion incoming but i'm bored so here we go",
                "someone defend their most controversial code decision. i'll wait",
                "remember when this channel had actual conversations?",
                "ok but seriously who's building something worth roasting today?"
            ]
            return random.choice(fallbacks)

    @tasks.loop(hours=1.5, reconnect=True)  # Compromise: keeps server warm without being naggy
    async def scheduled_provocation(self):
        """Stir up conversations targeting the channel with the latest message"""
        try:
            # Check daily provocation limit
            today = datetime.now(timezone.utc).date()
            if self.daily_provocation_count[today] >= self.MAX_DAILY_PROVOCATIONS:
                print(f"\n🔇 SCHEDULED PROVOCATION: Daily limit reached ({self.daily_provocation_count[today]}/{self.MAX_DAILY_PROVOCATIONS})")
                return

            if not self.latest_message_channel:
                print(f"🔇 No latest message channel tracked yet")
                return

            print(f"\n🔥 SCHEDULED PROVOCATION: Targeting latest message channel...")

            target_channel = self.latest_message_channel

            # Check if we can send messages to this channel
            if not target_channel.permissions_for(target_channel.guild.me).send_messages:
                print(f"  🔇 No permission to send messages in #{target_channel.name}")
                return

            # Check if we recently provoked this channel (< 1 hour)
            last_provocation = self.last_provocation.get(target_channel.id)
            if last_provocation and (datetime.now(timezone.utc) - last_provocation).total_seconds() < 3600:
                hours_since = (datetime.now(timezone.utc) - last_provocation).total_seconds() / 3600
                print(f"  🔇 Recently provoked #{target_channel.name} ({hours_since:.1f}h ago)")
                return

            # Check if the channel is too recently active (< 1 hour since latest message)
            hours_since_message = (datetime.now(timezone.utc) - self.latest_message_time).total_seconds() / 3600
            if hours_since_message < 1:
                print(f"  🔇 Channel #{target_channel.name} too recently active ({hours_since_message:.1f}h ago)")
                return

            # Check if the channel is too old (> 8 hours since latest message)
            if hours_since_message > 8:
                print(f"  🔇 Channel #{target_channel.name} too old for provocation ({hours_since_message:.1f}h ago)")
                return

            try:
                # Generate contextual provocation
                provocation = await self._generate_provocation(target_channel)

                # Add some natural typing delay
                async with target_channel.typing():
                    typing_time = min(len(provocation) / 25, 3)
                    await asyncio.sleep(typing_time)

                # Send the provocation
                sent_message = await target_channel.send(provocation)

                # Track it so we don't respond to ourselves
                self.unprompted_messages.add(sent_message.id)
                self.last_provocation[target_channel.id] = datetime.now(timezone.utc)

                # Update daily provocation count
                self.daily_provocation_count[today] += 1

                provocation_preview = provocation[:60] + "..." if len(provocation) > 60 else provocation
                print(f"  🔥 #{target_channel.name}: \"{provocation_preview}\"")
                print(f"🔥 Sent provocation to latest channel. Daily total: {self.daily_provocation_count[today]}/{self.MAX_DAILY_PROVOCATIONS}")

            except Exception as e:
                print(f"  ❌ Failed to provoke #{target_channel.name}: {e}")

        except Exception as e:
            print(f"❌ Critical error in scheduled provocation: {e}")
            # Don't let the task loop die on errors

    @scheduled_provocation.error
    async def provocation_error(self, exception):
        """Error handler for scheduled provocation task"""
        print(f"❌ Scheduled provocation task crashed: {exception}")
        print(f"🔄 Restarting provocation task in 60 seconds...")
        await asyncio.sleep(60)
        if not self.scheduled_provocation.is_running():
            self.scheduled_provocation.restart()
            print(f"✅ Provocation task restarted")
    
    @check_conversations.error
    async def conversation_check_error(self, exception):
        """Error handler for conversation check task"""
        print(f"❌ Conversation check task crashed: {exception}")
        print(f"🔄 Restarting conversation check in 30 seconds...")
        await asyncio.sleep(30)
        if not self.check_conversations.is_running():
            self.check_conversations.restart()
            print(f"✅ Conversation check task restarted")

    async def analyze_with_web_search(self, query, context=""):
        """Let Claude decide when to search the web and provide responses with citations"""
        print(f"  🔍 analyze_with_web_search called with query: '{query[:50]}{'...' if len(query) > 50 else ''}'")
        try:
            # Check daily search budget
            today = datetime.now(timezone.utc).date()
            if self.daily_search_count[today] >= self.MAX_DAILY_SEARCHES:
                print(f"  🚫 Daily search limit reached ({self.MAX_DAILY_SEARCHES}), falling back to cached knowledge")
                return f"i'd search for that but i'm at my daily limit. from what i remember: {query[:150]}..."
            
            async with self.api_sem:
                await self.api_throttler.throttle()
                
                # Extract last line for better context as per FinalTouches.md
                last_line = ""
                if context and isinstance(context, str) and context.strip():
                    try:
                        last_line = context.strip().splitlines()[-1][:120]
                    except IndexError:
                        pass # context might be a single line or empty after strip

                # Combine query with context for better search decisions
                full_prompt = f"""User said: {last_line}

Last user message: {query}
Conversation context: {context}

You may refine or rewrite the user query before calling web_search to make it more specific and useful.

For example, if a user says "latest news", a useful refined query might be "breaking world news headlines January 2025". If they ask about "minecraft updates", refine to "minecraft game updates 2025".

You have access to both web search and web fetch tools:

**Web Search**: Use for discovering sources, current information, recent events, changing tech details, or general queries you're unsure about.

**Web Fetch**: Use to deeply analyze specific URLs or PDFs that users mention or that you discover through web search. Perfect for reading full articles, documentation, or papers when you need detailed content analysis.

Choose web search to find information, then web fetch to dive deeper into specific sources. Always provide citations for web-sourced information."""

                print(f"  🌐 Making API call to Anthropic with WEB_TOOL and WEB_FETCH_TOOL...")
                response = await self.anthropic.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": full_prompt}],
                    tools=[WEB_TOOL, WEB_FETCH_TOOL],  # Provide both tools
                    tool_choice={"type": "auto"}, # Let Claude decide if/when to use which tool
                    timeout=30.0,
                    extra_headers={
                        "anthropic-beta": "web-fetch-2025-09-10"
                    }
                )

                # Track token usage
                self._log_token_usage(response, "web_search")
                
                print(f"  ✅ API call completed, processing response...")
                
                # Debug: Log all content blocks
                blocks = response.content or []
                print(f"  🔍 Response contains {len(blocks)} content block(s):")
                for i, content_block in enumerate(blocks):
                    block_type = getattr(content_block, 'type', 'unknown')
                    print(f"    Block {i}: type='{block_type}'")
                    if hasattr(content_block, 'text') and content_block.text is not None:
                        text_preview = content_block.text[:150] + "..." if len(content_block.text) > 150 else content_block.text
                        print(f"      Text preview: '{text_preview}'")
                    elif hasattr(content_block, 'name') and content_block.type in ['server_tool_use', 'tool']:
                        print(f"      Tool use: {getattr(content_block, 'name', 'N/A')}")
                
                # Count actual searches used - new Usage object structure from May 2025 API
                search_count = 0
                if hasattr(response.usage, 'server_tool_use') and response.usage.server_tool_use is not None:
                    search_count = response.usage.server_tool_use.get('web_search_requests', 0)
                print(f"  📊 Search count from response.usage.server_tool_use: {search_count}")
                
                if search_count > 0:
                    self.daily_search_count[today] += search_count
                    print(f"  💰 Used {search_count} searches today ({self.daily_search_count[today]}/{self.MAX_DAILY_SEARCHES})")
                    print(f"  🌐 Web search used: {search_count} search(es)")
                else:
                    print(f"  ⚠️  No tool usage detected in response")
                
                # FIXED: Concatenate ALL text blocks, not just the last one
                response_text = ""
                text_blocks_found = 0
                
                if response.content:
                    for content_block in response.content:
                        if hasattr(content_block, 'text') and content_block.type == 'text' and content_block.text:
                            response_text += content_block.text
                            text_blocks_found += 1
                
                print(f"  📝 Concatenated {text_blocks_found} text blocks")
                print(f"  📝 Full response length: {len(response_text)} characters")
                print(f"  📝 Response preview: '{response_text[:200]}{'...' if len(response_text) > 200 else ''}'")
                
                if not response_text:
                    print(f"  ❌ No text blocks found in response!")
                    return "couldn't find what you're looking for, sorry"
                
                # Clean up the response - remove any system artifacts but keep citations
                response_text = response_text.strip()
                
                # Handle Discord's message length limit (2000 chars)
                if len(response_text) > 1900:  # Leave room for split indicators
                    print(f"  ✂️  Response too long ({len(response_text)} chars), truncating...")
                    response_text = response_text[:1897] + "..."
                
                print(f"  🎯 Returning: '{response_text[:150]}{'...' if len(response_text) > 150 else ''}'")
                return response_text
                
        except Exception as e:
            print(f"❌ Error with web search: {e}")
            return None

    async def send_image(self, channel, image_path=None, image_url=None, caption=""):
        """Send images like a normal Discord user"""
        try:
            # Check permissions first
            if not channel.permissions_for(channel.guild.me).attach_files:
                print(f"  ❌ No ATTACH_FILES permission in #{channel.name}")
                return False
            
            if image_path:
                # Local file
                file = discord.File(image_path)
                await channel.send(content=caption, file=file)
                print(f"  📸 Sent local image: {image_path}")
            elif image_url:
                # Download and send
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url) as resp:
                        if resp.status == 200:
                            data = io.BytesIO(await resp.read())
                            # Try to preserve file extension
                            filename = 'image.png'
                            if image_url.lower().endswith(('.jpg', '.jpeg')):
                                filename = 'image.jpg'
                            elif image_url.lower().endswith('.gif'):
                                filename = 'image.gif'
                            elif image_url.lower().endswith('.webp'):
                                filename = 'image.webp'
                            
                            file = discord.File(data, filename)
                            await channel.send(content=caption, file=file)
                            print(f"  📸 Sent image from URL: {image_url}")
                        else:
                            print(f"  ❌ Failed to download image: HTTP {resp.status}")
                            return False
            else:
                print(f"  ❌ No image path or URL provided")
                return False
            
            return True
            
        except discord.Forbidden:
            print(f"  ❌ Permission denied: Bot needs ATTACH_FILES permission in #{channel.name}")
            return False
        except Exception as e:
            print(f"  ❌ Error sending image: {e}")
            return False


if __name__ == "__main__":
    try:
        print("🚀 Starting SLH-01 Discord Bot...")
        
        # Check environment variables
        if not os.getenv("DISCORD_BOT_TOKEN"):
            print("❌ ERROR: DISCORD_BOT_TOKEN not found in environment")
            print("   Please create a .env file with your bot token")
            exit(1)
            
        if not os.getenv("ANTHROPIC_API_KEY"):
            print("❌ ERROR: ANTHROPIC_API_KEY not found in environment")
            print("   Please add your Anthropic API key to .env file")
            exit(1)
        
        print("✅ Environment variables loaded")
        print("✅ Starting bot connection...")
        
        bot = AgenticBot()
        bot.run(os.getenv("DISCORD_BOT_TOKEN"))
        
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
        # Show final token usage
        if 'bot' in locals() and hasattr(bot, 'session_tokens'):
            bot._report_session_token_usage()
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        print("Check your .env file and network connection")
        # Show final token usage even on error
        if 'bot' in locals() and hasattr(bot, 'session_tokens'):
            bot._report_session_token_usage()
