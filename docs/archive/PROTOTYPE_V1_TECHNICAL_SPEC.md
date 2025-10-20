# SLH-01 Discord Bot - Technical Specification

**Document Version:** 1.0
**Date:** 2025-09-30
**Status:** Current Implementation (Pre-Framework Refactor)

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture](#architecture)
3. [Core Components](#core-components)
4. [Data Models](#data-models)
5. [Key Behaviors & Systems](#key-behaviors--systems)
6. [API Integrations](#api-integrations)
7. [Configuration](#configuration)
8. [Performance & Constraints](#performance--constraints)
9. [Technical Debt & Limitations](#technical-debt--limitations)
10. [Dependencies](#dependencies)

---

## 1. System Overview

### Purpose
SLH-01 is an **agentic Discord bot** that participates in conversations like a regular server member rather than a traditional command-driven bot. It uses Claude (Anthropic's LLM) to make intelligent decisions about when and how to engage in conversations.

### Key Characteristics
- **Agentic Behavior**: Periodic conversation monitoring (30s intervals) with autonomous response decisions
- **Personality-Driven**: Has a defined personality (sharp, dark humor, observant critic)
- **Context-Aware**: Loads user profiles, channel summaries, and recent message history
- **Multi-Modal**: Processes images, performs web searches, and handles long conversations
- **Rate-Limited**: Multiple layers of rate limiting to prevent spam and control costs

### Design Philosophy
The bot is designed to feel like a regular Discord user who:
- Doesn't respond to everything
- Uses natural timing and typing indicators
- Has conversations rather than executing commands
- Can be ignored if annoying (self-regulating behavior)

---

## 2. Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Discord Gateway                          │
│                    (discord.py client)                       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ├─► on_message() → MessageMemory
                       ├─► on_reaction_add() → Engagement Tracking
                       └─► on_ready() → History Loading + Task Loops
                                │
                ┌───────────────┴───────────────┐
                │                               │
        ┌───────▼────────┐           ┌─────────▼─────────┐
        │ Periodic Tasks │           │  Urgent Handlers  │
        └───────┬────────┘           └─────────┬─────────┘
                │                               │
    ┌───────────┼───────────┐          ┌───────▼───────┐
    │           │           │          │  Direct       │
    ▼           ▼           ▼          │  Mention      │
check_convo  scheduled_   cleanup     │  Handler      │
(30s)        provoke      tasks       └───────────────┘
             (1.5h)
                │
                └─────► analyze_conversation_chunk()
                               │
                        ┌──────┴──────┐
                        │             │
                   ┌────▼────┐   ┌───▼────┐
                   │ Claude  │   │Context │
                   │   API   │   │Manager │
                   └────┬────┘   └───┬────┘
                        │            │
                        └──────┬─────┘
                               │
                        execute_response_plan()
                               │
                        ┌──────┴──────────┐
                        │                 │
                   Rate Limiting      Typing +
                   + Cooldowns        Message Send
```

### Component Hierarchy

```
AgenticBot (discord.Client)
├── MessageMemory          # In-memory conversation storage
├── ContextManager         # Loads user/channel summaries from disk
├── APIThrottler          # Rate limits Claude API calls
├── SimpleRateLimiter     # Prevents channel spam
├── AsyncAnthropic        # Anthropic API client
└── Task Loops
    ├── check_conversations (30s)
    └── scheduled_provocation (1.5h)
```

### File Structure

```
SLH/
├── slh.py                    # Main bot implementation (1,764 lines)
├── bot/
│   └── memory.py             # MessageMemory class
├── context_manager.py        # ContextManager class
├── server_summaries/         # User/channel context files
│   ├── server_overview.md
│   ├── user_{id}.md
│   └── channel_{id}.md
├── .env.example              # Configuration template
└── README.md                 # Basic usage instructions
```

---

## 3. Core Components

### 3.1 AgenticBot (Main Class)

**Location:** `slh.py:166-1730`
**Inherits:** `discord.Client`

**Responsibilities:**
- Discord event handling (messages, reactions, ready)
- Periodic conversation checking
- Response decision-making via Claude API
- Image processing and web search integration
- Rate limiting and cooldown management

**Key Attributes:**

```python
# API Clients
self.anthropic              # AsyncAnthropic client
self.api_sem               # Semaphore(10) for concurrent API calls
self.api_throttler         # APIThrottler(min_delay=1.0)

# Memory & Context
self.memory                # MessageMemory(max_messages=150)
self.context_manager       # ContextManager()

# Rate Limiting
self.rate_limiter          # SimpleRateLimiter()
self.last_user_reply       # defaultdict: user_id -> last reply timestamp
self.cooldown_until        # defaultdict: channel_id -> cooldown end time
self.ignored_count         # defaultdict: channel_id -> ignore count

# Agentic Behavior
self.pending_channels      # set: channels with unprocessed activity
self.last_check            # defaultdict: channel_id -> last check time
self.unprompted_messages   # set: message IDs of bot's provocations

# Provocation System
self.latest_message_channel   # Channel with most recent message
self.latest_message_time      # Timestamp of latest message
self.chaos_channels           # set: channels allowed to provoke
self.last_provocation         # dict: channel_id -> timestamp

# Cost Controls
self.daily_search_count       # defaultdict: date -> search count
self.MAX_DAILY_SEARCHES       # 300 (~$3/day)
self.daily_provocation_count  # defaultdict: date -> provocation count
self.MAX_DAILY_PROVOCATIONS   # 4 per day

# Token Tracking
self.session_tokens          # dict: input/output/thinking/total_requests
```

### 3.2 MessageMemory

**Location:** `bot/memory.py`

**Purpose:** Lightweight in-memory conversation storage using `deque` for FIFO message history.

**Key Features:**
- Stores last N messages per channel (default 150, configurable)
- Automatic eviction of old messages via `deque(maxlen=max_messages)`
- Extracts message metadata (author, timestamp, mentions, attachments)

**API:**
```python
add_message(channel_id, message)           # Store new message
get_context(channel_id, limit=20)          # Get recent messages
get_recent_participants(channel_id, minutes=30)  # Active users
clear_channel(channel_id)                  # Clear history
get_channel_stats(channel_id)              # Message statistics
```

**Data Structure:**
```python
{
    'id': message.id,
    'author': message.author.name,
    'author_id': message.author.id,
    'content': message.content,
    'timestamp': message.created_at,
    'is_bot': message.author.bot,
    'mentions': [user_ids],
    'attachments': bool,
    'embeds': bool
}
```

### 3.3 ContextManager

**Location:** `context_manager.py`

**Purpose:** Loads comprehensive background context from pre-generated markdown summaries.

**Key Features:**
- Loads ALL user profiles from `server_summaries/user_{id}.md`
- Loads ALL channel summaries from `server_summaries/channel_{id}.md`
- Combines with recent message history for comprehensive context
- Minimal caching (simple cache clearing mechanism)

**API:**
```python
get_comprehensive_context(message, recent_messages)  # Main context builder
get_available_contexts()                             # List available files
context_exists()                                     # Check if any summaries exist
refresh_cache()                                      # Clear cache
```

**Context Assembly:**
1. **User Profiles** - All server members' personality, expertise, communication style
2. **Channel Summaries** - Medium-resolution channel culture and topics
3. **Recent History** - Last 500 messages from current channel with timestamps

### 3.4 Rate Limiting System

#### SimpleRateLimiter (`slh.py:119-164`)

**Purpose:** Prevent channel spam and self-regulate based on user feedback.

**Limits:**
- **5-minute window:** Max 20 responses
- **1-hour window:** Max 200 responses
- **Ignored threshold:** After 5 ignored messages, bot goes silent

**Features:**
- Tracks ignored messages (no reactions/replies after 30s)
- Tracks engagement (reactions, replies) to reduce ignore count
- Hourly cleanup of old data

**API:**
```python
check(channel_id) -> (can_respond: bool, reason: str)
record_response(channel_id)
record_ignored(channel_id)
record_engagement(channel_id)
get_stats() -> dict
```

#### Per-User Cooldowns

**Implementation:** `self.last_user_reply` defaultdict
**Duration:** 40 seconds per user
**Behavior:** Filters out messages from users on cooldown during analysis

#### Channel Cooldowns

**Implementation:** `self.cooldown_until` defaultdict
**Durations:**
- Single message: 45 seconds
- 2-3 messages: 75 seconds
- 4+ messages: 105 seconds

### 3.5 APIThrottler

**Location:** `slh.py:102-117`

**Purpose:** Prevent API rate limit errors by spacing out Anthropic API calls.

**Configuration:**
- Minimum delay: 1.0 seconds between calls
- Uses `asyncio.Lock` for thread safety
- Logs throttle delays

**Usage:**
```python
await self.api_throttler.throttle()  # Waits if needed before API call
```

---

## 4. Data Models

### 4.1 System Prompt

**Location:** `slh.py:45-99`

**Key Elements:**
- **Personality:** Sharp, dark humor, observant critic (inspired by Anthony Jeselnik)
- **Engagement Rules:** Prioritized response triggers (mentions > technical problems > humor)
- **Response Rates:** 10% cold, 25% warm, 40% hot conversations
- **Anti-patterns:** Avoid forcing personality, repeating jokes, always having last word

### 4.2 Response Plan JSON

**Generated by:** `analyze_conversation_chunk()` via Claude API
**Location:** `slh.py:989-1181`

**Structure:**
```json
{
    "should_respond": false,
    "response_strategy": "none|single|double",
    "responses": [
        {
            "target": "specific message/topic",
            "message": "actual response text",
            "delay": 2-8,  // seconds between messages
            "use_web_search": false,
            "search_query": null,
            "send_image": null  // image URL if applicable
        }
    ],
    "reasoning": "brief explanation",
    "triggering_messages_raw": [...]  // Added internally for cooldowns
}
```

### 4.3 Conversation Momentum

**Function:** `get_conversation_momentum()` (`slh.py:711-733`)

**Categories:**
- **Hot:** avg gap < 15 minutes (rapid exchanges)
- **Warm:** avg gap < 1 hour (steady discussion)
- **Cold:** avg gap > 1 hour (slow/dying)

**Calibration:** Tuned for small servers (< 50 active users)

### 4.4 Image Processing Data

**Platform Limits** (`_get_platform_limits()` - `slh.py:291-318`):

| Platform    | Max Dimension | Max Size (Raw) | Max Images |
|-------------|---------------|----------------|------------|
| API         | 8000px        | 5MB            | 100        |
| Bedrock     | 8000px        | 3.75MB         | 20         |
| claude.ai   | 8000px        | 5MB            | 20         |

**Compression Strategy** (`slh.py:220-226`):
1. Preserve format (optimize=True)
2. JPEG progressive quality reduction (85→75→65→...→10)
3. WebP conversion (85→75→65→...→15)
4. Nuclear resize (0.7x dimensions)
5. Thumbnail fallback (512x512)

**Target:** 73% of API limit (accounts for Base64 overhead)

---

## 5. Key Behaviors & Systems

### 5.1 Message Processing Flow

```
User posts message
     │
     ├─► MessageMemory.add_message()
     ├─► Update latest_message_channel/time
     ├─► Add to pending_channels set
     └─► If @mention → handle_urgent_mention() immediately
              │
              └─► analyze_conversation_chunk()
                       │
                  ┌────┴────┐
                  │  Claude │ (with images, context, web search)
                  └────┬────┘
                       │
                  response_plan
                       │
                  execute_response_plan()
                       │
                  ├─► Type for N seconds
                  ├─► Send message(s)
                  ├─► Set cooldowns
                  └─► Track engagement (30s delay)
```

### 5.2 Periodic Conversation Check

**Task:** `check_conversations` (`slh.py:834-948`)
**Interval:** 30 seconds
**Reconnect:** True (auto-restart on errors)

**Process:**
1. Iterate `pending_channels` set
2. Skip if in cooldown
3. Fetch messages since `last_check`
4. Analyze with `analyze_conversation_chunk()`
5. Execute response plan if warranted
6. Update `last_check` timestamp
7. Remove from `pending_channels`
8. Cleanup old data (unprompted messages, daily counts)

**Cleanup Tasks:**
- Old unprompted message IDs (24h)
- Old daily search counts (7 days)
- Old daily provocation counts (7 days)

### 5.3 Scheduled Provocations

**Task:** `scheduled_provocation` (`slh.py:1477-1547`)
**Interval:** 1.5 hours
**Daily Limit:** 4 provocations

**Targeting Logic:**
- Target channel with globally most recent message
- Must be 1-8 hours since last message (sweet spot for revival)
- Must be 1+ hour since last provocation in that channel
- Skips if no permission to send messages

**Provocation Generation** (`_generate_provocation()` - `slh.py:1398-1475`):
- Uses Claude with system prompt + recent context
- Returns contextual conversation starter
- Falls back to hardcoded provocations if API fails

**Anti-Self-Response:**
- Adds sent message ID to `unprompted_messages` set
- Skips analysis if chunk contains own provocation
- Prevents bot from responding to itself

### 5.4 Image Processing Pipeline

**Entry Point:** `process_message_images()` (`slh.py:666-709`)

**Flow:**
1. Filter image attachments (jpg, jpeg, png, gif, webp)
2. Check platform limits (max images)
3. Process concurrently with semaphore (max 3 parallel)
4. For each image:
   - Security check (Discord CDN only)
   - Secure download with timeout + size limits
   - Check if processing needed (size, dimensions, auto-resize threshold)
   - Apply bulletproof compression if needed
   - Encode to Base64
   - Return Claude API format

**Security:**
- Only allows Discord CDN domains
- 50MB download limit
- Streaming with chunk-based size checking
- 30s timeout per download

**Compression:**
- Sequential strategy application until size target met
- Runs in thread pool executor (non-blocking)
- Logs every step for debugging

### 5.5 Web Search Integration

**Function:** `analyze_with_web_search()` (`slh.py:1568-1682`)

**Features:**
- **Tools:** Web Search (`web_search_20250305`) + Web Fetch (`web_fetch_20250910`)
- **Daily Limit:** 300 searches (~$3/day budget)
- **Model:** `claude-sonnet-4-20250514` (faster, cheaper than opus)
- **Citations:** Enabled for web fetch

**Usage Tracking:**
- Counts via `response.usage.server_tool_use.web_search_requests`
- Tracks daily totals in `daily_search_count`
- Falls back to cached knowledge when limit reached

**Query Refinement:**
- Extracts last line of context for relevance
- Prompts Claude to refine query before searching
- Auto mode lets Claude decide when to search

### 5.6 Engagement Tracking

**Function:** `track_engagement()` (`slh.py:1359-1386`)

**Process:**
1. Wait 30 seconds after bot message
2. Fetch fresh message to check reactions
3. Check for replies referencing bot message
4. If engagement detected → `record_engagement()`
5. Reduces `ignored_count` by 1 (min 0)

**Also Triggered By:**
- `on_reaction_add()` event (immediate feedback)

---

## 6. API Integrations

### 6.1 Anthropic Claude API

**Client:** `AsyncAnthropic` (official Python SDK)
**Timeout:** 30.0 seconds

**Models Used:**

| Use Case                  | Model                          | Max Tokens | Thinking Budget |
|---------------------------|--------------------------------|------------|-----------------|
| Conversation Analysis     | `claude-opus-4-1-20250805`     | 4096       | 2048            |
| Provocation Generation    | `claude-opus-4-1-20250805`     | 4096       | 2048            |
| Web Search                | `claude-sonnet-4-20250514`     | 2048       | N/A             |

**Extended Thinking:**
- Enabled for conversation analysis and provocation generation
- Budget: 2048 tokens
- Helps with strategic response planning

**Token Tracking** (`_log_token_usage()` - `slh.py:231-262`):
```python
session_tokens = {
    'input_tokens': cumulative,
    'output_tokens': cumulative,
    'thinking_tokens': cumulative,
    'total_requests': count
}
```

**Cost Estimation** (approximate):
- Input: ~$15/1M tokens
- Output: ~$75/1M tokens
- Thinking: ~$15/1M tokens

### 6.2 Discord API

**Library:** `discord.py` (async)

**Intents:**
```python
intents = discord.Intents.default()
intents.message_content = True  # Required for reading message text
intents.reactions = True        # Required for engagement tracking
```

**Events:**
- `on_ready()` - Bot initialization, history loading, task start
- `on_message()` - Message ingestion, urgent mention handling
- `on_reaction_add()` - Engagement tracking

**Permissions Required:**
- Read Message History
- Send Messages
- Attach Files (for image sending)
- Add Reactions (optional, for future features)

### 6.3 Web Tools Integration

**Web Search:**
```python
{
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3
}
```

**Web Fetch:**
```python
{
    "type": "web_fetch_20250910",
    "name": "web_fetch",
    "max_uses": 5,
    "citations": {"enabled": True}
}
```

**Beta Header:** `anthropic-beta: web-fetch-2025-09-10`

---

## 7. Configuration

### 7.1 Environment Variables

**Required:**
```bash
DISCORD_BOT_TOKEN=...        # Discord bot token
ANTHROPIC_API_KEY=...        # Anthropic API key
```

**Optional:**
```bash
# Model Configuration
CLAUDE_MODEL=claude-opus-4-1-20250805
THINKING_ENABLED=true
CLAUDE_PLATFORM=api  # api | bedrock | claude_ai

# API Controls
API_CONCURRENCY=10
API_MIN_DELAY_SEC=1.0

# Cooldowns (minutes)
CHANNEL_COOLDOWN_SINGLE_MIN=0.75
CHANNEL_COOLDOWN_MULTI_MIN=1.25
CHANNEL_COOLDOWN_HEAVY_MIN=1.75

# Per-User Cooldown
PER_USER_COOLDOWN_SECONDS=40

# Daily Budgets
MAX_DAILY_SEARCHES=300
MAX_DAILY_PROVOCATIONS=4

# Personalization
REAL_NAME_MAPPING=(@user1 is John, @user2 is Jane)

# Persistence
SLH_STATE_FILE=.state.json

# Logging
LOG_LEVEL=INFO
```

**Note:** Most configs are currently hardcoded in `slh.py`. The framework refactor should externalize these.

### 7.2 Hardcoded Configuration

**In-Code Constants** (should be configurable):

```python
# Memory
MessageMemory(max_messages=150)

# Rate Limiting
SimpleRateLimiter:
    - 5min window: 20 responses
    - 1hr window: 200 responses
    - ignored_threshold: 5

# Cooldowns
PER_USER_COOLDOWN = 40  # seconds
CHANNEL_COOLDOWN = {
    'single': 0.75,   # minutes
    'multi': 1.25,
    'heavy': 1.75
}

# Budgets
MAX_DAILY_SEARCHES = 300
MAX_DAILY_PROVOCATIONS = 4

# Task Intervals
check_conversations = 30  # seconds
scheduled_provocation = 1.5  # hours

# API
API_CONCURRENCY = 10
API_MIN_DELAY = 1.0  # seconds

# Image Processing
TARGET_RAW_SIZE_FACTOR = 0.73
COMPRESSION_STRATEGIES = {
    'jpeg_qualities': [85, 75, 65, 55, 45, 35, 25, 15, 10],
    'png_compressions': [9, 6, 3, 1],
    'webp_qualities': [85, 75, 65, 55, 45, 35, 25, 15],
    'nuclear_resize_factor': 0.7,
    'thumbnail_size': 512
}
```

---

## 8. Performance & Constraints

### 8.1 Memory Constraints

**MessageMemory:**
- 150 messages × N channels
- ~5KB per message (estimates)
- 100 channels × 150 msgs = ~75MB theoretical max

**ContextManager:**
- Loads ALL user/channel summaries on each call
- No caching implemented
- File I/O on every context request

**Image Processing:**
- 3 concurrent downloads max (semaphore)
- Up to 50MB download buffer per image
- Thread pool executor for compression

### 8.2 API Rate Limits

**Anthropic:**
- APIThrottler: 1s minimum between calls
- Semaphore: Max 10 concurrent requests
- No explicit RPM/TPM limits implemented

**Discord:**
- No explicit rate limiting implemented
- Relies on SimpleRateLimiter for message sends
- Could hit Discord's rate limits with heavy usage

### 8.3 Cost Controls

**Daily Budgets:**
- Web searches: 300/day (~$3 ceiling)
- Provocations: 4/day
- No total token budget implemented

**Token Tracking:**
- Session totals tracked
- Cost estimation logged
- No hard limits enforced

### 8.4 Startup Performance

**History Loading:**
- Max 5000 messages total across all channels
- Max 100 messages per channel
- Skips bot messages (except own)
- Sets `last_check` to prevent re-processing

**Initialization Time:**
- Depends on guild count and channel count
- No parallel loading implemented
- Blocks until all history loaded

---

## 9. Technical Debt & Limitations

### 9.1 Known Issues

**Configuration Management:**
- Most settings hardcoded in `slh.py`
- No central config system
- `.env.example` lists unused variables

**Context Loading:**
- No caching in ContextManager
- Reloads ALL files every analysis
- Could hit file I/O limits with many summaries

**Error Handling:**
- Generic try-except blocks
- Limited error recovery
- Task loop errors print but don't alert

**State Persistence:**
- No persistence for daily counters
- Bot restart resets all limits
- No database or file-based state

**Image Processing:**
- Could fail silently on weird formats
- No retry logic for download failures
- Compression might be too aggressive for some use cases

**Web Search:**
- Daily limit resets at midnight UTC (not configurable)
- No fallback if web tools unavailable
- Limited error context for debugging

### 9.2 Missing Features

**User Requested:**
- Personalized user interaction (REAL_NAME_MAPPING defined but not used)
- Persistent state across restarts
- Per-channel configuration (different personalities/rules)
- Admin commands for tuning behavior

**Operational:**
- Metrics/monitoring dashboard
- Logging to file/external service
- Health checks
- Graceful shutdown

**Advanced:**
- Multi-server context isolation
- Per-user ignore/block commands
- Conversation threads support
- Voice channel integration

### 9.3 Refactor Candidates

**High Priority:**
1. **Configuration System** - Central config with validation
2. **State Persistence** - Database or JSON file for counters/cooldowns
3. **Error Handling** - Structured error types and recovery
4. **Modularization** - Split monolithic `slh.py` into logical modules

**Medium Priority:**
5. **Context Caching** - Cache user/channel summaries with invalidation
6. **Testing** - Unit tests for critical paths
7. **Logging** - Structured logging with levels
8. **Metrics** - Prometheus/Datadog integration

**Low Priority:**
9. **Documentation** - API docs, architecture diagrams
10. **CI/CD** - Automated testing and deployment

---

## 10. Dependencies

### 10.1 Python Packages

```
discord.py >= 2.0           # Discord API wrapper
anthropic                   # Anthropic API client (async)
python-dotenv               # Environment variable loading
aiohttp                     # Async HTTP client (image downloads, web fetch)
Pillow (PIL)                # Image processing
```

**Pillow Version Compatibility:**
- Handles both old (`Image.ANTIALIAS`) and new (`Image.Resampling.LANCZOS`) APIs
- Compatibility shim: `slh.py:22-25`

### 10.2 External Services

**Required:**
- Discord Bot Account (with token)
- Anthropic API Account (with API key)

**Optional:**
- Web search/fetch tools (Anthropic beta features)

### 10.3 System Requirements

**Python:** 3.8+ (for `asyncio`, type hints)
**OS:** Cross-platform (Windows, Linux, macOS)
**Disk:** Minimal (<100MB for code + summaries)
**Memory:** ~100-500MB depending on channel count
**Network:** Stable connection for Discord Gateway

---

## 11. Operational Characteristics

### 11.1 Bot Lifecycle

**Startup:**
1. Load environment variables
2. Initialize Discord client + Anthropic client
3. Connect to Discord Gateway
4. Load recent message history (up to 5000 messages)
5. Load context summaries from disk
6. Start task loops (`check_conversations`, `scheduled_provocation`)

**Runtime:**
- Message ingestion via `on_message()`
- Periodic checks every 30s
- Scheduled provocations every 1.5h
- Engagement tracking 30s after responses
- Cleanup tasks during periodic checks

**Shutdown:**
- Ctrl+C triggers KeyboardInterrupt
- Reports final token usage
- No graceful cleanup implemented

### 11.2 Typical Resource Usage

**API Calls:**
- ~1-10 per minute during active conversation
- ~0-1 per 30s during quiet periods
- Provocations: ~16 per day (max)

**Token Usage:**
- Conversation analysis: 5K-20K tokens per analysis
- Provocation generation: 3K-10K tokens
- Web search: 2K-15K tokens per search

**Cost Estimates:**
- Active server: ~$10-30/day
- Quiet server: ~$1-5/day
- Depends heavily on image processing and web search usage

### 11.3 Error Recovery

**Task Loop Errors:**
- `@conversation_check_error` handler restarts after 30s
- `@provocation_error` handler restarts after 60s
- Errors logged but don't crash bot

**API Errors:**
- Try-except catches and logs
- Returns fallback responses
- No automatic retry logic

**Discord Errors:**
- Permissions errors logged
- Forbidden errors skipped
- Gateway disconnects handled by discord.py

---

## 12. Code Hotspots

### 12.1 Critical Sections

**analyze_conversation_chunk() - `slh.py:989-1181`**
- Core decision-making logic
- 192 lines
- Handles images, context, web search, response planning
- Most complex function in codebase

**execute_response_plan() - `slh.py:1182-1333`**
- Response execution with timing
- Handles rate limits, typing, splitting, cooldowns
- 151 lines

**check_conversations (task loop) - `slh.py:834-948`**
- Periodic conversation checking
- 114 lines
- Cleanup and maintenance tasks

**Image compression pipeline - `slh.py:410-581`**
- 5 compression strategies
- 171 lines
- Error-prone due to format variety

### 12.2 Complexity Metrics

**Lines of Code:**
- `slh.py`: 1,764 lines
- `context_manager.py`: 130 lines
- `bot/memory.py`: 112 lines
- **Total:** ~2,000 lines

**Cyclomatic Complexity:**
- `analyze_conversation_chunk()`: High (multiple conditional paths)
- `execute_response_plan()`: High (nested logic)
- `_compress_image_bulletproof()`: Medium-High (sequential fallbacks)

**Maintainability Index:**
- Low (monolithic structure, hardcoded config, limited comments)

---

## 13. Testing & Quality Assurance

### 13.1 Current State

**Testing:**
- ❌ No unit tests
- ❌ No integration tests
- ❌ No automated testing

**Quality Checks:**
- ❌ No linting configuration
- ❌ No type checking (mypy)
- ❌ No code formatting (black/ruff)

**Manual Testing:**
- ✅ Live testing in Discord servers
- ✅ Token usage logging
- ✅ Console logging for debugging

### 13.2 Testing Recommendations

**Unit Tests:**
- MessageMemory: add/get/clear operations
- SimpleRateLimiter: check/record logic
- Image compression strategies
- Conversation momentum calculation

**Integration Tests:**
- Mock Discord client
- Mock Anthropic API responses
- End-to-end message flow

**Performance Tests:**
- Memory leak detection
- API call throttling verification
- Image processing benchmarks

---

## 14. Security Considerations

### 14.1 Current Security Measures

**Image Processing:**
- ✅ Whitelisted Discord CDN domains only
- ✅ File size limits (50MB download)
- ✅ Timeout limits (30s per download)
- ✅ Streaming with chunk-based size checking

**API Keys:**
- ✅ Stored in `.env` (not committed)
- ✅ Loaded via `python-dotenv`

**User Input:**
- ⚠️ Limited sanitization
- ⚠️ Potential prompt injection (user content passed to Claude)

### 14.2 Security Gaps

**Input Validation:**
- No explicit message content sanitization
- No length limits before API calls
- Trusts Discord's message validation

**Rate Limiting:**
- No protection against API key exhaustion attacks
- Daily budgets reset without warning

**Secrets Management:**
- Plain text in `.env` file
- No encryption at rest

**Audit Logging:**
- No security event logging
- No user action tracking

---

## 15. Scalability Analysis

### 15.1 Current Limits

**Server Count:**
- Single bot instance handles all servers
- No sharding implemented
- Memory scales linearly with server count

**Message Volume:**
- 30s check interval limits responsiveness
- No prioritization mechanism
- Could fall behind with >100 active channels

**Context Size:**
- Loads ALL summaries on every analysis
- File I/O bottleneck with >50 summaries
- No pagination or lazy loading

### 15.2 Scaling Recommendations

**Horizontal Scaling:**
- Implement sharding for >2500 servers
- Use Discord's recommended gateway practices
- Separate worker processes for heavy tasks

**Vertical Scaling:**
- Implement context caching
- Use database instead of file I/O
- Optimize memory usage (streaming, deques)

**Performance Optimizations:**
- Parallel context loading
- Lazy loading of summaries
- Message queue for analysis tasks

---

## Appendix A: Glossary

**Agentic Behavior:** Autonomous decision-making without explicit commands
**Cooldown:** Time period where bot won't respond in a channel/to a user
**Momentum:** Conversation activity level (hot/warm/cold)
**Provocation:** Bot-initiated conversation starter
**Rate Limiting:** Restricting response frequency to prevent spam
**Response Plan:** JSON output from Claude describing how to respond
**Thinking Tokens:** Extended reasoning tokens used by Claude for planning
**Unprompted Message:** Bot message sent without direct trigger (provocation)

---

## Appendix B: Key Decisions & Rationale

**Why 30s check interval?**
- Balance between responsiveness and API costs
- Allows natural conversation pacing
- Prevents overwhelming users with instant responses

**Why opus-4 for conversation analysis?**
- Higher quality personality consistency
- Better strategic reasoning
- Worth the extra cost for main interaction

**Why sonnet-4 for web search?**
- Faster responses for factual queries
- Lower cost for high-frequency tool use
- Sufficient quality for information retrieval

**Why no command prefix?**
- Goal is natural participation, not bot commands
- @mentions serve as explicit invocation
- Reduces "bot-like" feel

**Why file-based context summaries?**
- Simple to generate offline
- Easy to version control
- No database dependency
- Can be edited manually

**Why no persistent state?**
- Simplicity for prototype phase
- Restart resets costs/limits (safety mechanism)
- Framework refactor will add proper persistence

---

## Appendix C: Related Documentation

**Missing Documentation:**
- User Guide
- Admin Guide
- Deployment Guide
- Architecture Diagrams
- API Documentation
- Contribution Guidelines

**Recommended Reading:**
- discord.py Documentation: https://discordpy.readthedocs.io/
- Anthropic API Docs: https://docs.anthropic.com/
- Claude Code Documentation: (referenced in CLAUDE.md)

---

**End of Technical Specification**
