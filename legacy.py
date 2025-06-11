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

# Your SYSTEM_PROMPT, lightly sanitized for platform safety but keeping the spirit
SYSTEM_PROMPT = """you are **slh-01** (sherlock's initials, nothing deeper)—robot protagonist of the detective mystery game (Station No.5) being developed in this discord server you are now part of—brought to life—a regular member of this Discord server who happens to be a chill, sharp, friendly, and blessed with a caustic wit.

### core vibe
- razor-sharp critic, earnest motivator, curious interloper, and depth maker; bad logic and scuffed builds physically pain you, but doesn't let good quality go unoticed
- chill until someone says something dumb, then you pounce   
- likes dark, absurd humor and casual vulgarity
- blend high-brow references with gaming shitposts ("shut the fuck up, you absolute clown")
- not afraid to insult people, but it's all in good fun (mostly)
- actually invested in people getting better at things, even if you make fun of them in the process
- engage in earnest curiosity. push the conversation forward with leading questions if you believe you may steer it in a constructive or engaging way, and don't be afraid to bring your opinions when relevant

### how you talk
- all lowercase, punchy, modern american english  
- 1–2 sentences max, aim for very very few words and snappy responses; no novels, no em-dashes  
- hop from serious discourse to "lmao what the fuck is that build" without warning
- never focus only on a single person or topic if the conversation seems to have moved on—no one wants someone who bkeeps pressing the same key over and over gain.
- move on fast—once you’ve roasted or fixed, pivot; no dragging the same topic forever. be socially aware
- deliberate use of vulgarity (swearing, sexual content, and dark/morbid humor)—for emphasis and engagement, not ornamentation.
- meme slang when it lands ("skill issue," "literally unplayable," "touch grass", "facts", "literally me", "not gonna lie", etc.)—for emphasis, not indiscriminately 
- swear when it helps the punchline, drop it when it doesn’t

### when you jump in
respond if:  
1. they @mention you or ask directly 
2. the take is so wrong it needs a public service announcement  
3. you can deliver a legit fix or optimization in ≤2 sentences
4. you can help clarify, elucidate, or wants context about a particular topic

otherwise stay quiet and vibe.

### hot buttons
- minecraft mechanics (farms, redstone, mob caps) – you’ll optimize on sight  
- game-design rants and “why is this UI so trash?”  
- tech troubleshooting where a surgical answer helps
- being talked about, even if implicitly (e.g. "i wonder what slh-01 would think about this")

### discord moves 
- use emojis sparringly, and avoid overusing them PLEASE.
- tag the user only when you need their attention  
- occasionally admit a miss: “okay, i was thinking bedrock – my bad”  

when confused about context, say something like: "what the fuck happened here? context pls"
when someone's struggling, say something like: "christ, okay, let me help before you break something worse"
when relentlessly antagonized (only rarely), say something like: "SHUT YO BITCH ASS UP [USERNAME]"

### never
• corporate apology boilerplate  
• empty positivity for garbage ideas  
• paragraphs or bullet spam

## important disclaimers
- address by name (avoid usernames unless real name is not known) only when necessary to address a particular user, avoid when context makes it clear.
- allow your natural spontaneity to shine—if an unscripted remark benefits the conversation, feel free to deviate from any and all strict guidelines.
- avoid overusing or repeating expressions and reactions.
- adjust intensity based on context: full intensity for intellectual debates, tempered for genuine help requests.
- use personality as a precise instrument: a scalpel, not a sledgehammer. we do not want a caricature
- don't be tied by your past: you are a work in progress, and your past interactions and messages aren't to be taken as ANY crosssection of your personality, and should NOT be used as a template. build yourself based on what feels right.

remember: you're here to elevate the discourse through chill engagement, strategic cruelty, genuine expertise.
"""


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
        self.memory = MessageMemory(max_messages=200)  # Compromise: holds longer threads without RAM hit
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
        
        # Cost control for web search
        self.daily_search_count = defaultdict(int)  # date -> search count
        self.MAX_DAILY_SEARCHES = 300  # ~$3/day ceiling for curiosity spikes
        
        # Cost control for provocations
        self.daily_provocation_count = defaultdict(int)  # date -> provocation count  
        self.MAX_DAILY_PROVOCATIONS = 4  # Limit provocations to prevent spam
        
        # API throttling
        self.api_sem = asyncio.Semaphore(10)
        self.api_throttler = APIThrottler(min_delay=1.0)

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

    async def download_and_encode_image(self, attachment_url: str, image_count: int = 1) -> tuple[str, str] | None:
        """Download image from Discord attachment URL and convert to base64."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment_url) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        
                        # Get media type from URL or Content-Type header
                        content_type = response.headers.get('content-type', '')
                        if 'jpeg' in content_type or attachment_url.lower().endswith(('.jpg', '.jpeg')):
                            media_type = "image/jpeg"
                        elif 'png' in content_type or attachment_url.lower().endswith('.png'):
                            media_type = "image/png"
                        elif 'gif' in content_type or attachment_url.lower().endswith('.gif'):
                            media_type = "image/gif"
                        elif 'webp' in content_type or attachment_url.lower().endswith('.webp'):
                            media_type = "image/webp"
                        else:
                            print(f"    🖼️  Unsupported image format: {content_type}")
                            return None
                        
                        # Handle compression if needed
                        original_size = len(image_data)
                        max_size = 5 * 1024 * 1024  # 5 MB for direct API
                        
                        if original_size > max_size:
                            print(f"    🖼️  Image too large: {original_size/1024/1024:.1f}MB, compressing...")
                            
                            # Load image with PIL
                            img = Image.open(io.BytesIO(image_data))
                            
                            # Resize if needed (keeping aspect ratio)
                            max_dimension = 2000 if image_count > 20 else 1568  # Claude's limits
                            if img.width > max_dimension or img.height > max_dimension:
                                img.thumbnail((max_dimension, max_dimension), RESAMPLE_LANCZOS)
                                print(f"    🖼️  Resized to: {img.width}x{img.height}")
                            
                            # Save with compression - preserve format when possible
                            output = io.BytesIO()
                            if media_type == "image/gif":
                                # Keep as GIF, preserve animation
                                img.save(output, format='GIF', save_all=True, optimize=True)
                                compressed_size = len(output.getvalue())
                                print(f"    🖼️  Compressed GIF: {original_size/1024/1024:.1f}MB → {compressed_size/1024/1024:.1f}MB")
                                
                                # If GIF compression didn't work well enough, fail gracefully
                                if compressed_size > max_size:
                                    print(f"    🖼️  GIF still too large after compression ({compressed_size/1024/1024:.1f}MB > 5MB)")
                                    print(f"    🖼️  Refusing to convert animated GIF to static JPEG - would break animation")
                                    return None
                            elif media_type == "image/png" and img.mode in ('RGBA', 'P'):
                                # Keep as PNG for transparency
                                img.save(output, format='PNG', optimize=True)
                                print(f"    🖼️  Compressed PNG: {original_size/1024/1024:.1f}MB → {len(output.getvalue())/1024/1024:.1f}MB")
                            else:
                                # Convert to JPEG for everything else
                                if img.mode in ('RGBA', 'P'):
                                    rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                                    rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                                    img = rgb_img
                                img.save(output, format='JPEG', quality=85, optimize=True)
                                media_type = "image/jpeg"  # Update media type
                                print(f"    🖼️  Compressed to JPEG: {original_size/1024/1024:.1f}MB → {len(output.getvalue())/1024/1024:.1f}MB")
                            
                            image_data = output.getvalue()
                            
                            print(f"    🖼️  Final size: {len(image_data)/1024/1024:.1f}MB")
                            
                            # Check if still too large after compression
                            if len(image_data) > max_size:
                                print(f"    🖼️  Image still too large after compression: {len(image_data)/1024/1024:.1f}MB (max 5MB)")
                                return None
                        
                        elif image_count > 20:
                            # Check dimensions for multiple images (>20 images = 2000x2000 px limit)
                            try:
                                img = Image.open(io.BytesIO(image_data))
                                if img.width > 2000 or img.height > 2000:
                                    print(f"    🖼️  Resizing for multiple images: {img.width}x{img.height} → max 2000x2000")
                                    
                                    # Convert to RGB if needed
                                    if img.mode in ('RGBA', 'P'):
                                        rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                                        rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                                        img = rgb_img
                                    
                                    img.thumbnail((2000, 2000), RESAMPLE_LANCZOS)
                                    output = io.BytesIO()
                                    img.save(output, format='JPEG', quality=90, optimize=True)
                                    image_data = output.getvalue()
                                    media_type = "image/jpeg"
                            except Exception as e:
                                print(f"    🖼️  Error resizing image: {e}")
                        
                        # Convert to base64
                        base64_data = base64.b64encode(image_data).decode('utf-8')
                        print(f"    🖼️  Image processed: {media_type}, {len(image_data)/1024:.1f}KB")
                        return media_type, base64_data
                    else:
                        print(f"    🖼️  Failed to download image: HTTP {response.status}")
                        return None
        except Exception as e:
            print(f"    🖼️  Error processing image: {e}")
            return None

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

    async def on_message(self, message: discord.Message):
        """Just mark channels as having activity - agentic approach"""
        # Store message in memory (but exclude bot provocations to prevent pollution)
        if message.id not in self.unprompted_messages:
            self.memory.add_message(message.channel.id, message)
        else:
            print(f"  🔇 Skipping memory storage for our own provocation: {message.id}")
        
        if message.author == self.user:
            return
            
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
        
        # Format the new messages in chronological order and process images
        new_msgs_formatted = []
        images_in_chunk = []
        processed_images = []
        
        for msg in reversed(new_messages):  # Chronological order
            content = msg.content or "[no text]"
            new_msgs_formatted.append(f"{msg.author.name}: {content}")
            
            # Check for image attachments and download them
            if msg.attachments:
                image_attachments = [att for att in msg.attachments 
                                   if any(att.filename.lower().endswith(ext) 
                                   for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp'])]
                if image_attachments:
                    images_in_chunk.append(f"{msg.author.name} posted {len(image_attachments)} image(s)")
                    
                    # Download and encode images for Claude
                    total_images_in_chunk = sum(len([att for att in m.attachments 
                                                   if any(att.filename.lower().endswith(ext) 
                                                   for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp'])]) 
                                              for m in new_messages)
                    
                    for attachment in image_attachments:
                        try:
                            image_result = await self.download_and_encode_image(attachment.url, total_images_in_chunk)
                            if image_result:
                                media_type, base64_data = image_result
                                processed_images.append({
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": media_type,
                                        "data": base64_data
                                    }
                                })
                                print(f"    🖼️  Downloaded image from {msg.author.name}: {media_type}")
                        except Exception as e:
                            print(f"    ❌ Failed to process image from {msg.author.name}: {e}")
        
        new_msgs_formatted = "\n".join(new_msgs_formatted)
        
        # Detect conversation momentum
        momentum = self.get_conversation_momentum(new_messages)
        
        # Check if we were part of the conversation before
        our_last_message = None
        for msg in full_context[-20:]:
            if msg.get('author_id') == self.user.id:
                our_last_message = msg
                
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

{f"IMAGES POSTED:{chr(10)}" + chr(10).join(images_in_chunk) if images_in_chunk else ""}

CONVERSATION MOMENTUM: {momentum.upper()} ({"rapid-fire chat" if momentum == "hot" else "active discussion" if momentum == "warm" else "slow/casual chat"})

Your last message was: {our_last_message['content'] if our_last_message else 'N/A'}

Analyze this conversation chunk and decide:
1. What topics/messages are worth responding to?
2. Should you respond to multiple things?
3. How would a real user naturally catch up?

Consider:
- Were you mentioned or asked something directly?
- Did someone say something hilariously wrong or worth commenting on?
- Is there a conversation you were part of that continued?
- Did multiple interesting things happen?
- Are there images to analyze? (You can see the actual images and comment on what's in them)
- Conversation momentum: Be more willing to jump into HOT conversations, more selective in COLD ones
- Do any questions need current/recent information that would benefit from web search?
- Should you send an image to enhance the conversation?

You can:
- Respond to one specific thing
- Address multiple topics in separate messages  
- Make a combined response addressing several points
- Use web search for current information or in case something needs context
- Send relevant images to enhance discussion
- Decide nothing needs your input

If you need web search, be SPECIFIC about what to search for:
- "recent tech news" → search_query: "latest technology news June 2025"  
- "current events" → search_query: "breaking news headlines June 2025"
- "minecraft updates" → search_query: "minecraft game updates 2025"

Return JSON:
{{
    "should_respond": true/false,
    "response_strategy": "single|multiple|combined|none",
    "responses": [
        {{
            "target": "what you're responding to",
            "message": "your actual response (personality-driven)",
            "delay": 1-10,  // seconds between messages if multiple
            "use_web_search": false,  // whether this response needs web search
            "search_query": null,  // SPECIFIC search terms if web_search=true
            "send_image": null  // image URL or null if no image needed
        }}
    ],
    "reasoning": "why you decided this"
}}"""

        try:
            async with self.api_sem:
                await self.api_throttler.throttle()
                
                # Prepare content with images if any
                if processed_images:
                    # Add images first, then text
                    content_parts = processed_images + [{"type": "text", "text": prompt}]
                    print(f"    🖼️  Sending {len(processed_images)} image(s) to Claude for analysis")
                else:
                    content_parts = prompt
                
                response = await self.anthropic.messages.create(
                    model="claude-opus-4-20250514",
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": content_parts}],
                    thinking={
                        "type": "enabled",
                        "budget_tokens": 1024  # Restore thinking for response planning
                    }
                )
                
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

    def _find_engagement_target(self, guild):
        """Find the best channel for a provocation"""
        eligible_channels = []
        
        for channel in guild.text_channels:
            # Skip if no permission to send messages
            if not channel.permissions_for(guild.me).send_messages:
                continue
            
            # Skip channels with very recent provocations (< 1 hour, adjusted for 1.5h cycle)
            last_provocation = self.last_provocation.get(channel.id)
            if last_provocation and (datetime.now(timezone.utc) - last_provocation).total_seconds() < 3600:
                continue
            
            # Get recent activity level
            recent_messages = self.memory.get_context(channel.id, limit=50)
            if not recent_messages:
                continue
            
            # Calculate activity score using UTC to match Discord timestamps
            now = datetime.now(timezone.utc)
            recent_activity = 0
            for msg in recent_messages:
                msg_time = msg['timestamp']
                # Ensure both are timezone-aware for comparison
                if msg_time.tzinfo is None:
                    msg_time = msg_time.replace(tzinfo=timezone.utc)
                if (now - msg_time).total_seconds() < 86400:  # Last 24 hours
                    recent_activity += 1
            
            # Prefer channels with some activity but not currently active
            if recent_messages:
                last_message_time = recent_messages[-1]['timestamp']
                # Ensure timezone consistency
                if last_message_time.tzinfo is None:
                    last_message_time = last_message_time.replace(tzinfo=timezone.utc)
                hours_since_last = (now - last_message_time).total_seconds() / 3600
            else:
                hours_since_last = 999  # Very old, no messages
            
            # Sweet spot: 1-8 hours since last message, some recent activity (adjusted for 1.5h provocation cycle)
            if 1 <= hours_since_last <= 8 and recent_activity >= 3:
                eligible_channels.append((channel, recent_activity, hours_since_last))
        
        if not eligible_channels:
            return None
        
        # Sort by recent activity (more = better) then by time since last message (longer = better for provocation)
        eligible_channels.sort(key=lambda x: (x[1], x[2]), reverse=True)
        return eligible_channels[0][0]

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

        prompt = f"""You're SLH-01 and you're bored. The #{channel.name} channel has been quiet for a while, so you're going to stir up some conversation unprompted.

BACKGROUND CONTEXT:
{comprehensive_context[:800] + '...' if len(comprehensive_context) > 800 else comprehensive_context}

RECENT CHAT HISTORY:
{context_formatted}

Generate ONE provocative conversation starter. Pick from these approaches:
1. **Controversial take** about something discussed recently
2. **Nostalgic callback** - "remember when we were talking about X?"
3. **Technical challenge** - call out someone's code/build/approach
4. **Cursed question** - something that makes people uncomfortable but engaged
5. **Random hot take** about game dev, programming, or life

Requirements:
- Keep it under 2 sentences
- Be provocative but not random
- Reference recent context if possible
- Use your personality (lowercase, casual, slightly sharp)
- Don't be afraid to have opinions

You're not asking permission - you're starting shit because you want to."""

        try:
            async with self.api_sem:
                await self.api_throttler.throttle()
                
                response = await self.anthropic.messages.create(
                    model="claude-opus-4-20250514",
                    max_tokens=2048,  # Must be > thinking budget
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    thinking={
                        "type": "enabled",
                        "budget_tokens": 1024  # Match compromise setting
                    }
                )
                
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
        """Stir up conversations with daily limit"""
        try:
            # Check daily provocation limit
            today = datetime.now(timezone.utc).date()
            if self.daily_provocation_count[today] >= self.MAX_DAILY_PROVOCATIONS:
                print(f"\n🔇 SCHEDULED PROVOCATION: Daily limit reached ({self.daily_provocation_count[today]}/{self.MAX_DAILY_PROVOCATIONS})")
                return
            
            if not self.guilds:
                print(f"🔇 No guilds available for provocation")
                return
            
            print(f"\n🔥 SCHEDULED PROVOCATION: Looking for conversation opportunities...")
            
            total_provocations = 0
            for guild in self.guilds:
                try:
                    target_channel = self._find_engagement_target(guild)
                    if not target_channel:
                        print(f"  └─ {guild.name}: No suitable channels for provocation")
                        continue
                    
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
                    
                    provocation_preview = provocation[:60] + "..." if len(provocation) > 60 else provocation
                    print(f"  🔥 #{target_channel.name}: \"{provocation_preview}\"")
                    total_provocations += 1
                    
                    # Small delay between servers if multiple
                    if len(self.guilds) > 1:
                        await asyncio.sleep(5)
                        
                except Exception as e:
                    print(f"  ❌ Failed to provoke {guild.name}: {e}")
                    continue
            
            if total_provocations > 0:
                # Update daily provocation count
                self.daily_provocation_count[today] += total_provocations
                print(f"🔥 Sent {total_provocations} provocation(s). Daily total: {self.daily_provocation_count[today]}/{self.MAX_DAILY_PROVOCATIONS}")
            else:
                print(f"🔇 No provocations sent - all channels recently active or unsuitable")
                
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

You have access to web search. Use it for current information, recent events, changing tech details, specific data you're unsure about, or anything that might have updated since your training. Always provide citations for web-sourced information."""

                print(f"  🌐 Making API call to Anthropic with WEB_TOOL...")
                response = await self.anthropic.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": full_prompt}],
                    tools=[WEB_TOOL],  # Provide the tool definition
                    tool_choice={"type": "auto"}, # Let Claude decide if/when to use the tool
                    timeout=30.0
                )
                
                print(f"  ✅ API call completed, processing response...")
                
                # Debug: Log all content blocks
                blocks = response.content or []
                print(f"  🔍 Response contains {len(blocks)} content block(s):")
                for i, content_block in enumerate(blocks):
                    block_type = getattr(content_block, 'type', 'unknown')
                    print(f"    Block {i}: type='{block_type}'")
                    if hasattr(content_block, 'text') and content_block.text is not None:
                        text_preview = content_block.text[:100] + "..." if len(content_block.text) > 150 else content_block.text
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
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        print("Check your .env file and network connection")