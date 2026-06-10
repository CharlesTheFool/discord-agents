# Discord-Claude Bot Framework - Architecture

**Version:** 0.4.1 (Pre-release Beta)
**Last Updated:** 2025-10-24

This document provides comprehensive technical reference for the Discord-Claude Bot Framework. It serves as the primary guide for developers and AI coding agents working with the codebase.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Core Components](#core-components)
3. [Tool System](#tool-system)
4. [Use-Case Flows](#use-case-flows)
5. [Data Models](#data-models)
6. [Integration Points](#integration-points)
7. [Configuration System](#configuration-system)

---

## System Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Discord Platform                        │
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
               ▼                              ▼
    ┌──────────────────────┐      ┌──────────────────────┐
    │   DiscordClient      │      │   DiscordClient      │
    │   (Bot Instance 1)   │      │   (Bot Instance 2)   │
    └──────────┬───────────┘      └──────────┬───────────┘
               │                              │
               ▼                              ▼
    ┌──────────────────────┐      ┌──────────────────────┐
    │  ReactiveEngine      │      │  ReactiveEngine      │
    │  AgenticEngine       │      │  AgenticEngine       │
    └──────────┬───────────┘      └──────────┬───────────┘
               │                              │
               ▼                              ▼
    ┌──────────────────────┐      ┌──────────────────────┐
    │  Anthropic Claude    │      │  Anthropic Claude    │
    │  (API Client)        │      │  (API Client)        │
    └──────────┬───────────┘      └──────────┬───────────┘
               │                              │
               ▼                              ▼
    ┌──────────────────────────────────────────────────────┐
    │           Shared Infrastructure                       │
    │  - SQLite Databases (per bot)                        │
    │  - Memory Files (per bot/server)                     │
    │  - Rate Limiters (per bot/channel)                   │
    │  - Logs (per bot)                                    │
    └──────────────────────────────────────────────────────┘
```

### Component Relationships

```
BotManager
    ├── DiscordClient
    │   ├── ReactiveEngine
    │   │   ├── ContextBuilder
    │   │   ├── MemoryManager
    │   │   ├── RateLimiter
    │   │   └── Tools (discord, web, image, memory)
    │   ├── AgenticEngine
    │   │   ├── MemoryManager
    │   │   └── EngagementTracker
    │   ├── MessageMemory (SQLite + FTS5)
    │   ├── UserCache (SQLite)
    │   └── ConversationLogger
    └── BotConfig
```

---

## Core Components

### 1. BotManager (`bot_manager.py`)

**Purpose:** Entry point and lifecycle management

**Responsibilities:**
- CLI argument parsing
- Environment variable loading (deployment/.env → .env)
- Config path resolution (deployment/bots/ → bots/ → templates)
- Component initialization
- Graceful shutdown handling

**Key Methods:**
- `resolve_config_path(bot_id)` - Find config file (deployment priority)
- `load_environment()` - Load .env with fallback
- `initialize()` - Set up all components
- `run()` - Main event loop
- `shutdown()` - Clean resource cleanup

**Files:** `bot_manager.py`

---

### 2. DiscordClient (`core/discord_client.py`)

**Purpose:** Discord.py integration and event handling

**Responsibilities:**
- Connect to Discord gateway
- Handle events (on_message, on_ready, on_message_edit)
- Route messages to ReactiveEngine
- Manage agentic background loop
- Historical message backfill
- Daily message reindexing (3 AM UTC)

**Key Events:**
- `on_ready()` - Bot connected, start background tasks
- `on_message(message)` - New message received
- `on_message_edit(before, after)` - Message edited
- `on_raw_message_delete(payload)` - Message deleted

**Background Tasks:**
- `_daily_reindex_task()` - Daily 3 AM UTC message reindex
- Agentic loop (if enabled)

**Files:** `core/discord_client.py`

---

### 3. ReactiveEngine (`core/reactive_engine.py`)

**Purpose:** Message processing and response generation

**Responsibilities:**
- Decide whether to respond to messages
- Build context from message history
- Execute tool calls (memory, discord, web, image)
- Generate responses via Claude API
- Handle rate limiting
- Extract and display citations

**Processing Flow:**
```
Message received
    ↓
Rate limit check → Skip if exceeded
    ↓
Context building (ContextBuilder)
    ↓
API call (Claude with tools)
    ↓
Tool execution loop
    │  ↓
    │  Execute tool → Add results to messages
    │  ↓
    │  API call again
    │  ↓
    └─ Repeat until stop_reason == "end_turn"
    ↓
Extract citations (if any)
    ↓
Send response to Discord
    ↓
Log conversation
```

**Key Methods:**
- `process_message(message)` - Main entry point
- `_handle_urgent_message(message)` - @mentions, replies
- `_periodic_check()` - Background conversation monitoring
- `_execute_tools(tool_uses)` - Execute tool calls
- `_extract_citations(response)` - Parse citations from text blocks

**Tool Execution:**
- Memory tool: Client-side via MemoryToolExecutor
- Discord tools: Client-side via discord_tools module
- Web search/fetch: Server-side (Anthropic executes)
- Image processing: Client-side via image_processor

**Server Tool Tracking:**
- Monitors `server_tool_use` blocks in response.content
- Records quota usage for web_search and web_fetch
- Extracts citations from text blocks

**Files:** `core/reactive_engine.py`

---

### 4. AgenticEngine (`core/agentic_engine.py`)

**Purpose:** Autonomous behaviors (proactive engagement, follow-ups)

**Responsibilities:**
- Background hourly loop (configurable interval)
- Follow-up system (track events, check in later)
- Proactive engagement (initiate conversations in idle channels)
- Memory maintenance (cleanup old follow-ups)

**Agentic Loop:**
```
Every N hours (default: 1):
    ↓
Load all servers
    ↓
For each server:
    ↓
    Check follow-ups
    │   ↓
    │   Due? → Generate natural check-in message
    │   ↓
    │   Send via Discord
    ↓
    Check for idle channels
    │   ↓
    │   Idle > threshold? → Analyze context
    │   ↓
    │   Decide engagement strategy
    │   ↓
    │   Generate proactive message
    │   ↓
    │   Send (standalone, woven, or deferred)
    ↓
    Cleanup old follow-ups
    ↓
Sleep until next check
```

**Delivery Methods:**
- **Standalone:** Send as new message in channel
- **Woven:** Reply to recent message (less intrusive)
- **Deferred:** Wait for user activity, then engage

**Success Tracking:**
- Monitors reactions, replies within time window
- Updates engagement stats per channel
- Adapts behavior based on success rates
- Backs off from low-performing channels

**Files:** `core/agentic_engine.py`, `core/proactive_action.py`, `core/engagement_tracker.py`

---

### 5. ContextBuilder (`core/context_builder.py`)

**Purpose:** Assemble smart context for API calls

**Responsibilities:**
- Build reply chains (recursive, up to 5 levels)
- Include recent messages (last 10)
- Resolve @mentions to readable names
- Add timestamps for temporal awareness
- Generate system prompt with bot identity
- Handle forwarded messages gracefully

**Context Structure:**
```
System Prompt:
    - Bot identity (Discord name, role)
    - Current timestamp
    - Personality base_prompt
    - Server and channel context
    ↓
Recent Messages (chronological):
    - Message 1 (user: "text", timestamp)
    - Message 2 (user: "text", timestamp)
    - ...
    ↓
Reply Chain (if message is a reply):
    - Original message
    - Reply 1
    - Reply 2 (up to 5 deep)
    ↓
Target Message:
    - The message triggering response
```

**Reply Chain Example:**
```
Message A: "What's the capital of France?"
    ↓ (reply)
Message B: "Paris"
    ↓ (reply)
Message C: "Are you sure?"
    ↓ (reply - this triggers bot)
Bot response gets full chain: A → B → C
```

**Files:** `core/context_builder.py`

---

### 6. MemoryManager (`core/memory_manager.py`)

**Purpose:** Wrapper for memory tool file operations

**Responsibilities:**
- Manage memory file paths (per server/channel hierarchy)
- Read/write markdown files
- Validate file sizes
- Create directories as needed

**Directory Structure:**
```
memories/{bot_id}/
    └── servers/{server_id}/
        ├── server_context.md
        ├── users/
        │   ├── {user_id}.md
        │   └── {username}.md
        ├── channels/
        │   ├── {channel_id}_context.md
        │   └── {channel_id}_stats.json
        └── followups.json
```

**Files:** `core/memory_manager.py`, `core/memory_tool_executor.py`

---

### 7. MessageMemory (`core/message_memory.py`)

**Purpose:** SQLite-based message storage with FTS5 search

**Responsibilities:**
- Store all messages (content, author, timestamp, channel)
- Full-text search index (FTS5)
- UPSERT logic for edited messages
- Efficient context retrieval
- Message backfill management

**Database Schema:**
```sql
-- Main message table
CREATE TABLE messages (
    message_id TEXT PRIMARY KEY,
    channel_id TEXT,
    server_id TEXT,
    author_id TEXT,
    author_name TEXT,
    content TEXT,
    timestamp INTEGER,
    is_bot INTEGER
);

-- FTS5 full-text search index
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=rowid
);

-- User cache table
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    username TEXT,
    display_name TEXT,
    last_seen INTEGER
);
```

**Key Methods:**
- `add_message()` - UPSERT message (handles edits)
- `search_messages()` - FTS5 full-text search
- `get_recent_messages()` - Retrieve last N messages
- `get_first_messages()` - Retrieve oldest N messages
- `get_message_context()` - Get messages around target
- `get_messages_in_range()` - Get messages between IDs

**UPSERT Logic:**
```sql
INSERT OR REPLACE INTO messages (...) VALUES (...)
```
- On collision (same message_id): UPDATE
- On new message: INSERT
- Ensures edited messages update search index

**Files:** `core/message_memory.py`

---

### 8. RateLimiter (`core/rate_limiter.py`)

**Purpose:** Per-channel rate limiting to prevent spam

**Responsibilities:**
- Track message counts per channel
- Two time windows (short: 5 min, long: 60 min)
- Engagement-aware backoff
- Reset on engagement
- Ignore threshold (consecutive ignores → silence)

**Rate Limiting Logic:**
```
Message arrives → Check channel rate limits
    ↓
Short window: 20 messages / 5 min
Long window: 200 messages / 60 min
    ↓
Exceeded? → Return (False, reason)
Not exceeded? → Return (True, None)
    ↓
After bot responds:
    ↓
Record response in both windows
    ↓
Start engagement tracking (15 min window)
    ↓
If engagement detected → Reset limits
```

**Files:** `core/rate_limiter.py`

---

## Tool System

### Tool Categories

1. **Client-side tools** (executed by bot)
   - Memory tool (file operations)
   - Discord tools (search, view messages)
   - Image processor (compression)

2. **Server-side tools** (executed by Anthropic)
   - Web search
   - Web fetch

---

### Memory Tool

**Commands:** view, create, str_replace, insert, delete, rename

**Workflow:**
```
Claude requests memory operation
    ↓
ReactiveEngine receives tool_use block
    ↓
MemoryToolExecutor.execute(action, params)
    ↓
MemoryManager performs file operation
    ↓
Return result (success/error)
    ↓
Add tool_result to messages
    ↓
Continue API loop
```

**Files:** `core/memory_manager.py`, `core/memory_tool_executor.py`

---

### Discord Tools

**Agentic Architecture:** Two-step workflow for token efficiency

**Tool 1: search_messages**
- **Purpose:** Discovery (find relevant messages)
- **Returns:** Message IDs + metadata (NO content)
- **Parameters:** query, date range, author, channel
- **Search:** SQLite FTS5 full-text index
- **Output:** `[{"message_id": "123", "channel": "general", "author": "alice", "timestamp": ...}, ...]`

**Tool 2: view_messages**
- **Purpose:** Retrieval (get full content)
- **Modes:**
  - `recent`: Last N messages from channel
  - `around`: Context around specific message ID
  - `first`: Oldest N messages from channel
  - `range`: Messages between two message IDs
- **Returns:** Full message content + metadata

**Workflow Example:**
```
User: "What did Alice say about the API yesterday?"
    ↓
Claude: search_messages(query="API", author="alice", date_range="yesterday")
    ↓
Returns: [message_id_1, message_id_2, message_id_3]
    ↓
Claude: view_messages(mode="around", message_id=message_id_2, limit=5)
    ↓
Returns: Full content of 5 messages around the match
    ↓
Claude: "Alice mentioned the API needs better error handling..."
```

**Files:** `tools/discord_tools.py`

---

### Web Search Tools

**Server-side execution** (Anthropic runs these)

**Tool 1: web_search**
- Type: `web_search_20250305`
- Beta header: `web-search-2025-03-05`
- Returns search results with snippets
- Max uses per request: 3 (configurable)

**Tool 2: web_fetch**
- Type: `web_fetch_20250910`
- Beta header: `web-fetch-2025-09-10`
- Fetches full page content
- Citations enabled (required for compliance)

**Citation Extraction:**
```
Response contains text blocks with citations array:
{
    "type": "text",
    "text": "Claude Shannon was born on April 30, 1916...",
    "citations": [
        {
            "url": "https://en.wikipedia.org/wiki/Claude_Shannon",
            "title": "Claude Shannon - Wikipedia",
            "cited_text": "born April 30, 1916"
        }
    ]
}

Extracted and formatted:
**Sources:**
- [Claude Shannon - Wikipedia](https://en.wikipedia.org/wiki/Claude_Shannon)
```

**Quota Management:**
- Daily limit: 300 searches (configurable)
- Tracked in `persistence/{bot_id}_web_search_stats.json`
- Automatic reset at midnight UTC
- Server tool tracking: Monitors `server_tool_use` blocks

**Files:** `tools/web_search.py`, `core/reactive_engine.py` (citation extraction)

---

### Image Processing

**6-Strategy Compression Cascade:**
```
Original image
    ↓
Strategy 1: Resize to 1568x1568
    ↓ (If still too large)
Strategy 2: Resize to 1024x1024
    ↓ (If still too large)
Strategy 3: Reduce quality to 85%
    ↓ (If still too large)
Strategy 4: Reduce quality to 75%
    ↓ (If still too large)
Strategy 5: Resize to 768x768
    ↓ (If still too large)
Strategy 6: Reduce quality to 60%
    ↓
Success or failure
```

**Token Calculation:**
- Max tokens per image: 1600
- Compression target: 73% of API limit
- Automatic base64 encoding
- Support up to 5 images per message

**Files:** `tools/image_processor.py`

---

## Use-Case Flows

### Flow 1: User @Mentions Bot

```
1. User sends message: "@Claude what's the weather?"
    ↓
2. Discord event: on_message(message)
    ↓
3. DiscordClient → ReactiveEngine.process_message(message)
    ↓
4. ReactiveEngine checks:
    - Is bot mentioned? YES
    - Rate limit exceeded? NO
    ↓
5. ContextBuilder.build_context(message)
    - Get reply chain (if any)
    - Get recent messages (last 10)
    - Resolve @mentions → names
    - Add timestamps
    ↓
6. ReactiveEngine → Claude API call
    - Model: claude-sonnet-4-5-20250929
    - Extended thinking: enabled
    - Tools: memory, discord, web, image
    - Context editing: enabled (if > 8k tokens)
    ↓
7. Claude responds with tool_use:
    {
        "type": "tool_use",
        "name": "web_search",
        "input": {"query": "current weather"}
    }
    ↓
8. Server tool executes (Anthropic side)
    ↓
9. ReactiveEngine receives server_tool_use block
    - Records quota usage
    ↓
10. Claude generates final response with citations
    ↓
11. ReactiveEngine extracts citations:
    - Parse text blocks
    - Format as Markdown links
    - Append "**Sources:**" section
    ↓
12. Send response to Discord channel
    ↓
13. ConversationLogger logs exchange
    ↓
14. RateLimiter records response
    ↓
15. Start engagement tracking (15 min window)
```

---

### Flow 2: Proactive Engagement

```
1. AgenticEngine background loop wakes up (hourly)
    ↓
2. Load all servers bot has access to
    ↓
3. For each server:
    ↓
4. Check all channels for idle state
    - Last message > 1 hour ago?
    - Last message < 8 hours ago?
    ↓
5. Channel #general is idle (3 hours since last message)
    ↓
6. Load channel context:
    - Last 20 messages
    - Channel stats (success rate)
    - Server context (from memory)
    ↓
7. Check engagement threshold:
    - Success rate > 0.3? YES (continue)
    - Daily limit reached? NO (continue)
    ↓
8. Build proactive prompt:
    - Include channel context
    - Ask Claude to decide: engage or skip?
    - If engage, generate relevant message
    ↓
9. Claude API call (using ReactiveEngine client)
    - Extended thinking enabled
    - Memory tool available
    - No web search (proactive messages stay contextual)
    ↓
10. Claude decides to engage:
    {
        "decision": "engage",
        "message": "Saw the discussion about API design earlier...",
        "delivery": "woven"
    }
    ↓
11. Delivery method: WOVEN
    - Find recent message to reply to
    - Send as reply (less intrusive)
    ↓
12. EngagementTracker records attempt:
    - Channel ID
    - Timestamp
    - Message ID
    ↓
13. Start engagement window (15 minutes)
    - Monitor for reactions
    - Monitor for replies
    - Monitor for continued conversation
    ↓
14. After 15 minutes:
    - Engagement detected? Update success_rate
    - No engagement? Update failure count
    ↓
15. Next cycle: Adapt behavior based on success rate
```

---

### Flow 3: Follow-Up Check-In

```
1. User mentions event to bot: "Remind me to review PR tomorrow"
    ↓
2. Bot responds and uses memory tool:
    create(path="followups.json", content={
        "user_id": "123",
        "event": "review PR",
        "created": "2025-10-20T10:00:00Z",
        "follow_up_at": "2025-10-21T10:00:00Z"
    })
    ↓
3. AgenticEngine background loop (next day)
    ↓
4. Load followups.json from memory
    ↓
5. Check each follow-up:
    - follow_up_at <= now? YES
    ↓
6. Build follow-up context:
    - Original event details
    - User's recent activity in server
    - Time since event
    ↓
7. Claude API call:
    - Prompt: Generate natural check-in (NOT robotic)
    - Context: Original event, time passed
    - Memory tool: Available to update follow-up status
    ↓
8. Claude generates:
    "Hey! Did you get a chance to review that PR?"
    ↓
9. Send in channel where event was mentioned
    ↓
10. Claude uses memory tool to mark complete:
    str_replace(
        path="followups.json",
        old_str='"status": "pending"',
        new_str='"status": "completed"'
    )
    ↓
11. Monitor for user response
    ↓
12. If user responds → Engage naturally
    ↓
13. Next cycle: Cleanup completed follow-ups older than 14 days
```

---

### Flow 4: Discord Message Search & View

```
1. User: "@Claude what did Alice say about the bug last week?"
    ↓
2. Bot processes via ReactiveEngine
    ↓
3. Claude uses tool: search_messages
    Input: {
        "query": "bug",
        "author": "alice",
        "days_back": 7
    }
    ↓
4. discord_tools.search_messages():
    - SQLite FTS5 query: SELECT message_id FROM messages_fts WHERE content MATCH 'bug'
    - Filter: author = 'alice', timestamp > (now - 7 days)
    - Returns: [id_1, id_2, id_3] (NO content, just IDs)
    ↓
5. ReactiveEngine adds tool_result to conversation
    ↓
6. Claude sees 3 matching message IDs
    ↓
7. Claude uses tool: view_messages
    Input: {
        "mode": "around",
        "message_id": id_2,
        "limit": 5
    }
    ↓
8. discord_tools.view_messages():
    - Fetch 5 messages centered on id_2
    - Returns FULL content + metadata
    ↓
9. ReactiveEngine adds tool_result with full messages
    ↓
10. Claude generates response:
    "Alice mentioned the bug was related to the cache invalidation issue.
     She suggested clearing the Redis cache manually."
    ↓
11. Send response to user
```

---

### Flow 5: Message Reindexing

**Daily Automatic (3 AM UTC):**
```
1. DiscordClient._daily_reindex_task() runs
    ↓
2. Calculate seconds until 3 AM UTC
    ↓
3. Sleep until 3 AM
    ↓
4. Wake up and trigger manual reindex:
    ↓
5. For each server bot has access to:
    ↓
6. For each channel in server:
    ↓
7. Backfill messages (MessageMemory.backfill_messages)
    - Fetch all messages (or last 30 days)
    - UPSERT each message (INSERT OR REPLACE)
    - Update FTS5 index automatically
    ↓
8. Log completion: "Daily reindex complete: 12,345 messages"
    ↓
9. Sleep until next 3 AM UTC
```

**Manual (User Command):**
```
1. User: "@Claude reindex"
    ↓
2. DiscordClient.on_message() detects command
    ↓
3. Trigger manual reindex (same as daily)
    ↓
4. Send confirmation: "Reindexing complete!"
```

---

## Data Models

### Configuration (YAML)

```yaml
bot_id: alpha
name: "Claude (Alpha)"
description: "Bot description"

discord:
  token_env_var: "ALPHA_BOT_TOKEN"
  servers: ["server_id_1", "server_id_2"]
  backfill_enabled: true
  backfill_days: 30
  backfill_unlimited: false
  backfill_in_background: true

personality:
  base_prompt: "Your personality here..."
  formality: 0.3
  emoji_usage: "moderate"
  # ...engagement rates...

reactive:
  enabled: true
  check_interval_seconds: 30
  context_window: 20
  cooldowns: { per_user: 40, ... }

agentic:
  enabled: true
  check_interval_hours: 1.0
  followups: { enabled: true, ... }
  proactive: { enabled: true, min_idle_hours: 1.0, ... }

api:
  model: "claude-sonnet-4-5-20250929"
  max_tokens: 16000
  extended_thinking: { enabled: true, budget_tokens: 10000 }
  context_editing: { enabled: true, trigger_tokens: 8000, ... }
  web_search: { enabled: true, max_daily: 300, ... }

rate_limiting:
  short: { duration_minutes: 5, max_responses: 20 }
  long: { duration_minutes: 60, max_responses: 200 }
```

**Files:** `bots/*.yaml`, `core/config.py`

---

### Message Database Schema

**Messages Table:**
```sql
CREATE TABLE messages (
    message_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    server_id TEXT NOT NULL,
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    is_bot INTEGER DEFAULT 0
);

CREATE INDEX idx_channel ON messages(channel_id);
CREATE INDEX idx_timestamp ON messages(timestamp);
```

**FTS5 Search Index:**
```sql
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=rowid
);

-- Triggers to keep FTS in sync
CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.rowid;
END;

CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
    DELETE FROM messages_fts WHERE rowid = old.rowid;
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;
```

---

### Memory File Format

**Server Context** (`memories/{bot}/servers/{server}/server_context.md`):
```markdown
# Server: My Discord Server

## Server Purpose
This is a gaming community focused on strategy games.

## Active Topics
- New game release discussions
- Tournament planning
- Mod development
```

**User Profile** (`memories/{bot}/servers/{server}/users/{user}.md`):
```markdown
# User: alice_gamer

## Background
- Experienced strategy game player
- Moderator in the community
- Interested in game AI development

## Recent Interactions
- Discussed tournament brackets (2025-10-15)
- Asked about mod API (2025-10-18)
```

**Follow-ups** (`memories/{bot}/servers/{server}/followups.json`):
```json
[
    {
        "user_id": "123456789",
        "event": "review tournament brackets",
        "created": "2025-10-20T10:00:00Z",
        "follow_up_at": "2025-10-21T10:00:00Z",
        "status": "pending",
        "channel_id": "987654321"
    }
]
```

---

## Integration Points

### Discord.py

**Events Used:**
- `on_ready()` - Bot connected
- `on_message(message)` - New message
- `on_message_edit(before, after)` - Message edited
- `on_raw_message_delete(payload)` - Message deleted

**Intents Required:**
```python
intents = discord.Intents.default()
intents.message_content = True  # Required for reading messages
intents.guilds = True
intents.members = True
```

**API Methods:**
- `channel.send(content)` - Send message
- `message.reply(content)` - Reply to message
- `message.add_reaction(emoji)` - React to message
- `channel.history(limit=N)` - Fetch message history

---

### Anthropic Claude API

**Endpoints:**
- `POST /v1/messages` - Create message (main endpoint)

**Request Structure:**
```json
{
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 16000,
    "system": [
        {
            "type": "text",
            "text": "System prompt...",
            "cache_control": {"type": "ephemeral"}
        }
    ],
    "messages": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": [...]},
        {"role": "user", "content": [...]}
    ],
    "tools": [...],
    "thinking": {
        "type": "enabled",
        "budget_tokens": 10000
    },
    "betas": ["web-search-2025-03-05", "web-fetch-2025-09-10"]
}
```

**Response Structure:**
```json
{
    "id": "msg_123",
    "type": "message",
    "role": "assistant",
    "content": [
        {
            "type": "thinking",
            "thinking": "Step-by-step reasoning..."
        },
        {
            "type": "server_tool_use",
            "id": "tool_123",
            "name": "web_search",
            "input": {"query": "..."}
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": "tool_123",
            "content": [...]
        },
        {
            "type": "text",
            "text": "Response text...",
            "citations": [
                {
                    "url": "https://...",
                    "title": "Title",
                    "cited_text": "..."
                }
            ]
        }
    ],
    "stop_reason": "end_turn",
    "usage": {
        "input_tokens": 1234,
        "output_tokens": 567,
        "cache_creation_input_tokens": 890,
        "cache_read_input_tokens": 450
    }
}
```

**Beta Headers:**
- `web-search-2025-03-05`
- `web-fetch-2025-09-10`

---

### SQLite (aiosqlite)

**Async Operations:**
```python
async with aiosqlite.connect(db_path) as db:
    await db.execute("INSERT INTO ...")
    await db.commit()

    cursor = await db.execute("SELECT * FROM ...")
    rows = await cursor.fetchall()
```

**FTS5 Queries:**
```python
# Simple search
cursor = await db.execute(
    "SELECT * FROM messages_fts WHERE content MATCH ?",
    (query,)
)

# With filters
cursor = await db.execute(
    """
    SELECT m.* FROM messages_fts f
    JOIN messages m ON m.rowid = f.rowid
    WHERE f.content MATCH ?
    AND m.channel_id = ?
    AND m.timestamp >= ?
    """,
    (query, channel_id, start_timestamp)
)
```

---

## Configuration System

### Config File Resolution

**Priority Order:**
1. `deployment/bots/{bot_id}.yaml` (private submodule)
2. `bots/{bot_id}.yaml` (local override)
3. `bots/{bot_id}.yaml.example` (template fallback)

**Environment File Resolution:**
1. `deployment/.env` (private submodule)
2. `.env` (root)

### Validation

**Startup Validation** (`config.validate()`):
- Required fields present
- Environment variables exist
- Numeric ranges valid (0-1 for rates, positive for tokens)
- Logging level valid
- Returns list of errors (empty if valid)

**Example Error Output:**
```
Configuration validation failed:
  - Missing environment variable: ALPHA_BOT_TOKEN
  - personality.formality must be between 0 and 1, got 1.5
  - api.max_tokens must be positive
```

---

## Directory Structure

```
discord-claude-framework/
├── bot_manager.py              # CLI entry point
├── .env.example                # Template environment file
├── .gitignore                  # Git ignore (deployment data)
├── requirements.txt            # Python dependencies
├── README.md                   # User-facing documentation
├── ARCHITECTURE.md             # This file
├── CHANGELOG.md                # Version history
├── TESTING.md                  # Test documentation
│
├── bots/
│   └── alpha.yaml.example      # Template bot config
│
├── core/
│   ├── config.py               # Configuration system
│   ├── discord_client.py       # Discord.py integration
│   ├── reactive_engine.py      # Message processing
│   ├── agentic_engine.py       # Autonomous behaviors
│   ├── context_builder.py      # Smart context assembly
│   ├── memory_manager.py       # Memory tool wrapper
│   ├── memory_tool_executor.py # Memory tool execution
│   ├── message_memory.py       # SQLite + FTS5
│   ├── user_cache.py           # User data caching
│   ├── rate_limiter.py         # Rate limiting
│   ├── engagement_tracker.py   # Success tracking
│   ├── proactive_action.py     # Proactive data class
│   ├── conversation_logger.py  # Logging
│   └── retry_logic.py          # Error handling
│
├── tools/
│   ├── discord_tools.py        # Search & view
│   ├── web_search.py           # Web search/fetch
│   └── image_processor.py      # Image compression
│
├── docs/
│   ├── phases/                 # Historical phase docs
│   ├── reference/              # API references
│   └── archive/                # Archived documentation
│
├── deployment/                 # Git submodule (private)
│   ├── .env                    # Your API keys
│   ├── bots/                   # Your bot configs
│   ├── logs/                   # Your logs
│   ├── memories/               # Your bot memories
│   └── persistence/            # Your databases
│
└── tests/
    ├── test_discord_tools.py
    ├── test_web_search.py
    ├── test_image_processor.py
    └── test_integration_phase4.py
```

---

## Version Information

**Current Version:** 0.4.1 (Pre-release Beta)

**Version History:**
- **v0.4.1** (2025-10-24) - Pre-release beta: Polish & refinements with date/timezone awareness, lifecycle tracking, configurable status
- **v0.4.0-beta** (2025-10-20) - Tools & polish, closed beta
- **v0.3.0** (2025-10-04) - Autonomous agentic behaviors
- **v0.2.0** (2025-10-04) - Intelligent context and memory
- **v0.1.0** (2025-09-30) - Initial framework foundation

For detailed changelog, see [CHANGELOG.md](CHANGELOG.md).

---

## Additional Resources

- **User Guide:** [README.md](README.md)
- **Version History:** [CHANGELOG.md](CHANGELOG.md)
- **Testing:** [TESTING.md](TESTING.md)
- **Phase Documentation:** `docs/phases/`
- **API References:** `docs/reference/`
