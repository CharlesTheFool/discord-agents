# Discord-Claude Bot Framework - Project Specification

**Version:** 2.0
**Date:** 2025-09-30
**Status:** Implementation Ready
**Target:** Personal multi-bot framework for Discord + Claude integration

---

## Development Context

**Current Phase:** Local development and testing
**Target Environment:** Developer workstation (macOS/Linux/Windows)
**Multi-System Development:** Bot state commits to git for sync across machines
**Production Deployment:** Future consideration (see Appendix E)

This spec prioritizes local development workflow. The framework is designed to run on your personal computer, with state/memories committed to version control for continuity across development machines.

---

## Table of Contents

1. [Key Architectural Decisions](#1-key-architectural-decisions)
2. [Local Development Setup](#2-local-development-setup)
3. [System Architecture](#3-system-architecture)
4. [Core Components](#4-core-components)
5. [Memory Tool Structure](#5-memory-tool-structure)
6. [Configuration System](#6-configuration-system)
7. [Preserved Algorithms](#7-preserved-algorithms)
8. [Discord Integration](#8-discord-integration)
9. [Implementation Phases](#9-implementation-phases)
10. [Testing Strategy](#10-testing-strategy)

**Appendices:**
- A. Response Plan JSON Schema
- B. Follow-Up System Details
- C. Image Processing Pipeline
- D. Rate Limiting Algorithms
- E. Production Deployment (Future Reference)
- F. Glossary

---

## 1. Key Architectural Decisions

These decisions are **prescriptive** - they define the framework's foundation:

### 1.1 Core Technologies

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| **LLM Model** | Claude Sonnet 4.5 (`claude-sonnet-4-5-20250929`) | Balance of speed, quality, and cost. Fast enough for reactive responses, smart enough for agentic reasoning |
| **Memory System** | Anthropic Memory Tool | Auto-managed knowledge, bot updates own profiles, eliminates manual maintenance |
| **Context Management** | Anthropic Context Editing | Token efficiency in long conversations, prevents context bloat |
| **Message History** | SQLite database | Persistent, queryable, survives restarts |
| **State Persistence** | JSON files | Simple, git-friendly, human-readable |
| **Configuration** | YAML files | Per-bot configs, easy to edit, version controlled |
| **Discord API** | discord.py (async) | Mature, well-documented, async-native |

### 1.2 Architectural Pattern

**Dual-Mode Architecture:**
- **Reactive Engine:** Fast message responses (2-5s target), handles 95% of interactions
- **Agentic Engine:** Background intelligence (follow-ups, proactive engagement, memory maintenance)

**Why separate?** Different performance requirements. Reactive must be snappy. Agentic can take time to think.

### 1.3 Multi-System Development

**Git Strategy:**
```
✅ Commit to version control:
- memories/          # Bot knowledge (essential for continuity)
- persistence/*.db   # Message history (SQLite)
- persistence/*.json # State files
- logs/              # For debugging (with size limits)
- bots/*.yaml        # Bot configurations

❌ Never commit:
- .env               # API keys and secrets
- __pycache__/       # Python cache
- *.pyc              # Compiled Python
```

**Rationale:** You develop on multiple machines. Bot needs to remember conversations and user context across systems. Git provides sync + version history.

**Repo Size Management:**
- SQLite files grow ~10MB per 10k messages
- Use Git LFS if .db files exceed 100MB
- Archive old messages (>90 days) periodically

### 1.4 What We Preserve from Old Bot

These algorithms/components are battle-tested and should be **ported, not rewritten:**

| Component | Preservation Strategy |
|-----------|---------------------|
| **Personality & System Prompt** | Extract to YAML config, keep exact wording |
| **SimpleRateLimiter** | Port algorithm to `core/rate_limiter.py` |
| **Image Compression Pipeline** | Port to `tools/image_processor.py`, keep logic identical |
| **Conversation Momentum** | Port calculation, make configurable per channel |
| **Response Plan JSON** | Keep schema, make it part of spec |
| **Engagement Tracking** | Preserve 30s delay logic, integrate with memory tool |

### 1.5 What We Rebuild

These need architectural rework:

- **Context Loading:** Replace file-based summaries with memory tool
- **User Profiles:** Bot manages via memory tool, no manual editing
- **Configuration:** Centralize in YAML, eliminate hardcoding
- **State Persistence:** SQLite + JSON, not in-memory only
- **Multi-Bot Support:** Framework-level, not per-script. Each bot has its own Discord application and token

---

## 2. Local Development Setup

### 2.1 Project Structure

```
discord-claude-framework/
├── bot_manager.py              # CLI entry point
├── core/
│   ├── __init__.py
│   ├── reactive_engine.py      # Fast message handling
│   ├── agentic_engine.py       # Proactive behaviors
│   ├── memory_manager.py       # Memory tool wrapper
│   ├── context_builder.py      # Smart context assembly
│   └── rate_limiter.py         # Ported SimpleRateLimiter
├── tools/
│   ├── __init__.py
│   ├── discord_tools.py        # Discord state query tools
│   ├── image_processor.py      # Ported compression pipeline
│   └── web_search.py           # Web search wrapper
├── bots/
│   ├── alpha.yaml              # Bot config examples
│   └── beta.yaml
├── memories/                   # Memory tool storage (git committed)
│   └── alpha/
│       └── servers/...
├── persistence/                # SQLite + JSON state (git committed)
│   ├── messages.db
│   └── state.json
├── logs/                       # Debug logs (git committed with limits)
│   └── alpha.log
├── docs/                       # API reference materials
│   ├── api_memory_tool.md
│   ├── api_context_editing.md
│   ├── discord_patterns.md
│   └── preserved_algorithms.md
├── .env.example                # Configuration template
├── .gitignore                  # Excludes .env only
├── requirements.txt            # Python dependencies
└── README.md                   # Setup instructions
```

### 2.2 Dependencies

**Python 3.9+** (for improved asyncio, type hints)

**Required packages:**
```txt
discord.py>=2.3.0
anthropic>=0.45.0
python-dotenv>=1.0.0
aiohttp>=3.9.0
Pillow>=10.0.0
pyyaml>=6.0.0
aiosqlite>=0.19.0
```

### 2.3 Environment Configuration

**.env file (not committed):**
```bash
# Required: Anthropic API (shared across all bots)
ANTHROPIC_API_KEY=your_api_key_here

# Required: Discord bot tokens (one per bot identity)
ALPHA_BOT_TOKEN=your_alpha_bot_token_here
BETA_BOT_TOKEN=your_beta_bot_token_here

# Optional overrides (defaults in bot configs)
LOG_LEVEL=INFO
```

**Note:** Each bot requires its own Discord application and token. Bot configs reference their token via `token_env_var` field (e.g., `token_env_var: "ALPHA_BOT_TOKEN"`). For Phase 1 testing, one bot/token is sufficient.

**.env.example (committed):**
```bash
# Copy to .env and fill in your keys

# Anthropic API (shared)
ANTHROPIC_API_KEY=

# Discord bot tokens (one per bot identity)
# Create separate Discord applications for each bot at:
# https://discord.com/developers/applications
ALPHA_BOT_TOKEN=

# Optional
LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR
```

### 2.4 Development Workflow

**First time setup:**
```bash
# Clone repo
git clone <repo-url>
cd discord-claude-framework

# Install dependencies
pip install -r requirements.txt

# Configure secrets
cp .env.example .env
# Edit .env: Add ANTHROPIC_API_KEY and ALPHA_BOT_TOKEN
# (Get Discord token from https://discord.com/developers/applications)

# Create your first bot config
cp bots/alpha.yaml bots/mybot.yaml
# Edit bots/mybot.yaml (ensure token_env_var matches your .env)

# Spawn bot
python bot_manager.py spawn mybot
```

**Syncing across systems:**
```bash
# On System A (after bot has learned stuff)
git add memories/ persistence/
git commit -m "Update bot memories and state"
git push

# On System B (continuing from where A left off)
git pull
python bot_manager.py spawn mybot  # Bot continues with synced state
```

**Monitoring:**
```bash
# View live logs
python bot_manager.py logs mybot --follow

# Check bot status
python bot_manager.py status mybot

# Stop bot
python bot_manager.py stop mybot
```

---

## 3. System Architecture

### 3.1 High-Level Architecture

```
                    Discord Gateway
                  (discord.py client)
                          │
        ┌─────────────────┴─────────────────┐
        │                                   │
   on_message()                        on_ready()
        │                                   │
        ├──► MessageMemory                  ├──► Load History
        ├──► Update State                   └──► Start Tasks
        └──► Check if Urgent                       │
                 │                                 │
       ┌─────────┴──────────┐                     │
       │                    │                     │
   Urgent?              Regular?                  │
   (mention)            (pending)                 │
       │                    │                     │
       │                    ▼                     │
       │            Periodic Check           Agentic Loop
       │            (every 30s)              (every 1h)
       │                    │                     │
       └────────┬───────────┴─────────────────────┘
                │
         ┌──────▼───────┐
         │   Decision   │
         │    Logic     │
         └──────┬───────┘
                │
      ┌─────────┴─────────┐
      │                   │
Reactive Engine    Agentic Engine
      │                   │
      ├─► ContextBuilder  ├─► FollowupChecker
      ├─► MemoryManager   ├─► EngagementAnalyzer
      ├─► RateLimiter     ├─► MemoryMaintenance
      └─► ResponsePlan    └─► ProactiveAction
                │
         ┌──────▼───────┐
         │   Claude API │
         │ + Memory Tool│
         │ + Context Mgmt│
         └──────┬───────┘
                │
         ┌──────▼───────┐
         │   Execute    │
         │   Response   │
         └──────────────┘
```

### 3.2 Data Flow

**Reactive Path (Message Arrival):**
```
1. Discord message arrives
2. MessageMemory.add_message()
3. Update pending_channels set
4. If @mention → handle_urgent() immediately
5. Else → wait for periodic check

Periodic Check (every 30s):
6. For each pending channel:
   - Fetch messages since last_check
   - Build context (recent messages + memories)
   - Call Claude API with memory tool
   - Get response plan
   - Execute if should_respond=true
```

**Agentic Path (Hourly):**
```
1. Check followups.json for due items
2. Check engagement_history for proactive opportunities
3. If action needed:
   - Build context
   - Call Claude API
   - Generate natural follow-up or provocation
   - Execute at next natural opportunity
```

### 3.3 Component Responsibilities

| Component | Responsibility | Stateful? |
|-----------|---------------|-----------|
| **bot_manager.py** | CLI interface, process management | No |
| **ReactiveEngine** | Message handling, response decisions | Yes (cooldowns) |
| **AgenticEngine** | Proactive behaviors, follow-ups | Yes (schedules) |
| **MemoryManager** | Memory tool wrapper, abstractions | No (memory tool is stateful) |
| **ContextBuilder** | Assemble context from messages + memories | No |
| **RateLimiter** | Spam prevention, cooldowns | Yes (windows) |
| **MessageMemory** | Recent message storage | Yes (SQLite) |
| **DiscordTools** | Query Discord state (users, threads, pins) | No |
| **ImageProcessor** | Compress, encode images | No |

---

## 4. Core Components

### 4.1 ReactiveEngine

**Purpose:** Handle incoming messages and decide responses quickly.

**API:**
```python
class ReactiveEngine:
    def __init__(self, config: BotConfig, memory: MemoryManager, 
                 rate_limiter: RateLimiter, context_builder: ContextBuilder):
        """Initialize with dependencies"""
        
    async def process_message(self, message: discord.Message) -> Optional[ResponsePlan]:
        """
        Analyze message and decide if/how to respond.
        
        Returns:
            ResponsePlan if should respond, None otherwise
        """
        
    async def execute_response(self, plan: ResponsePlan, channel: discord.TextChannel):
        """
        Execute response plan (typing, delays, sending messages).
        Records engagement tracking.
        """
        
    async def handle_urgent(self, message: discord.Message):
        """Handle @mentions immediately (bypass periodic check)"""
```

**Key Behaviors:**
- Checks rate limits before analyzing
- Builds context using ContextBuilder
- Calls Claude API with memory tool + context editing
- Returns structured ResponsePlan
- Execution handles typing indicators, message splitting, cooldowns
- Tracks engagement 30s after response

**Configuration (from bot YAML):**
```yaml
reactive:
  response_chance:
    cold: 0.1    # 10% for idle conversations
    warm: 0.25   # 25% for steady discussions
    hot: 0.4     # 40% for active exchanges
  mention_always_respond: true
  context_window: 20  # Recent messages to include
```

### 4.2 AgenticEngine

**Purpose:** Autonomous behaviors that don't need instant response.

**API:**
```python
class AgenticEngine:
    def __init__(self, config: BotConfig, memory: MemoryManager):
        """Initialize with dependencies"""
        
    async def check_followups(self) -> List[ProactiveAction]:
        """
        Check memory for due follow-ups.
        
        Returns:
            List of follow-up actions to execute
        """
        
    async def check_engagement_opportunities(self) -> List[ProactiveAction]:
        """
        Analyze server activity for proactive engagement chances.
        Uses engagement history from memory to decide.
        """
        
    async def maintain_memories(self):
        """
        Periodic memory maintenance:
        - Archive old follow-ups
        - Clean up completed items
        - Update engagement statistics
        """
        
    async def execute_proactive_action(self, action: ProactiveAction):
        """Execute proactive message or DM"""
```

**Task Schedule:**
```python
@tasks.loop(hours=1)
async def agentic_loop():
    # Check for follow-ups
    followup_actions = await engine.check_followups()
    
    # Check for engagement opportunities
    engagement_actions = await engine.check_engagement_opportunities()
    
    # Maintenance
    await engine.maintain_memories()
    
    # Execute actions
    for action in followup_actions + engagement_actions:
        await engine.execute_proactive_action(action)
```

**Configuration:**
```yaml
agentic:
  followups:
    enabled: true
    check_interval_hours: 1
    max_pending: 20
  
  proactive:
    enabled: true
    min_idle_hours: 1.0
    max_idle_hours: 8.0
    max_per_day_global: 10
    max_per_day_per_channel: 3
    quiet_hours: [0, 6]  # Don't engage midnight-6am
```

### 4.3 MemoryManager

**Purpose:** Abstraction over Anthropic's memory tool with helper methods.

**API:**
```python
class MemoryManager:
    def __init__(self, bot_id: str, memory_base_path: Path):
        """
        Initialize memory manager for specific bot.
        
        Args:
            bot_id: Bot identifier (e.g., "alpha")
            memory_base_path: Base path for memories (e.g., "./memories/alpha")
        """
        
    def get_user_profile_path(self, server_id: str, user_id: str) -> str:
        """Get standard path for user profile"""
        return f"/memories/servers/{server_id}/users/{user_id}.md"
        
    def get_channel_context_path(self, server_id: str, channel_id: str) -> str:
        """Get standard path for channel context"""
        return f"/memories/servers/{server_id}/channels/{channel_id}.md"
        
    def get_followups_path(self, server_id: str) -> str:
        """Get path for follow-ups JSON"""
        return f"/memories/servers/{server_id}/followups.json"
        
    async def read(self, path: str) -> Optional[str]:
        """Read memory file, returns None if not found"""
        
    async def read_json(self, path: str) -> Optional[dict]:
        """Read and parse JSON memory file"""
        
    def build_memory_context(self, server_id: str, channel_id: str, 
                            user_ids: List[str]) -> str:
        """
        Build context string from memories for Claude API.
        
        Returns formatted string with:
        - Server culture
        - Channel context
        - Relevant user profiles
        """
```

**Important:** MemoryManager does NOT directly write to memories. The bot writes via memory tool calls in Claude API responses. This manager only reads and provides path helpers.

**Memory Tool Integration:**
```python
# In Claude API call
response = await client.messages.create(
    model="claude-sonnet-4-5-20250929",
    betas=["context-management-2025-06-27"],
    tools=[{"type": "memory"}],  # Enable memory tool
    messages=[...]
)

# Bot automatically reads from /memories/ before responding
# Bot makes tool calls to update:
# - memory.create(...)
# - memory.str_replace(...)
# - memory.delete(...)
```

**Full documentation:** `docs/api_memory_tool.md`

### 4.4 ContextBuilder

**Purpose:** Assemble context for Claude API from multiple sources.

**API:**
```python
class ContextBuilder:
    def __init__(self, memory: MemoryManager, message_storage: MessageMemory):
        """Initialize with dependencies"""
        
    async def build_context(self, 
                           message: discord.Message,
                           recent_limit: int = 20) -> ContextPackage:
        """
        Build comprehensive context for analysis.
        
        Returns:
            ContextPackage with:
            - system_prompt
            - recent_messages (formatted)
            - memory_context (from memory tool)
            - reply_chain (if message is reply)
            - images (processed and encoded)
        """
        
    async def build_reply_chain(self, message: discord.Message) -> List[dict]:
        """
        Build parent message chain if message is a reply.
        
        Discord's reply feature lets you reference old messages.
        This reconstructs the chain for context.
        """
```

**Context Assembly Logic:**
```python
async def build_context(self, message, recent_limit=20):
    context = ContextPackage()
    
    # 1. System prompt (from bot config)
    context.system_prompt = self.config.personality.base_prompt
    
    # 2. Recent messages
    recent = await self.message_storage.get_recent(
        channel_id=message.channel.id,
        limit=recent_limit
    )
    context.recent_messages = self._format_messages(recent)
    
    # 3. Memory context
    user_ids = [msg.author.id for msg in recent]
    context.memory_context = self.memory.build_memory_context(
        server_id=message.guild.id,
        channel_id=message.channel.id,
        user_ids=user_ids
    )
    
    # 4. Reply chain (if applicable)
    if message.reference:
        context.reply_chain = await self.build_reply_chain(message)
    
    # 5. Images (if attachments)
    if message.attachments:
        context.images = await self.process_images(message.attachments)
    
    return context
```

### 4.5 MessageMemory

**Purpose:** Persistent message history using SQLite.

**Schema:**
```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    message_id TEXT UNIQUE NOT NULL,  -- Discord message ID
    channel_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    content TEXT,
    timestamp DATETIME NOT NULL,
    is_bot BOOLEAN NOT NULL,
    has_attachments BOOLEAN NOT NULL,
    mentions TEXT,  -- JSON array of mentioned user IDs
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_channel_timestamp ON messages(channel_id, timestamp DESC);
CREATE INDEX idx_guild ON messages(guild_id);
```

**API:**
```python
class MessageMemory:
    def __init__(self, db_path: Path):
        """Initialize with SQLite database path"""
        
    async def add_message(self, message: discord.Message):
        """Store message in database"""
        
    async def get_recent(self, channel_id: str, limit: int = 20) -> List[StoredMessage]:
        """Get recent messages from channel"""
        
    async def get_since(self, channel_id: str, since: datetime) -> List[StoredMessage]:
        """Get messages since timestamp"""
        
    async def get_channel_stats(self, channel_id: str) -> dict:
        """Get message count, participants, etc."""
        
    async def cleanup_old(self, days: int = 90):
        """Archive messages older than N days"""
```

**Why SQLite?**
- Persistent across restarts
- Queryable (useful for analytics)
- Git-friendly (small files)
- No server setup needed

### 4.6 RateLimiter

**Purpose:** Prevent spam, implements adaptive rate limiting.

**Ported from:** Old bot's `SimpleRateLimiter` (lines 119-164 in slh.py)

**API:**
```python
class RateLimiter:
    def __init__(self):
        """
        Initialize with default windows:
        - 5 minutes: max 20 responses
        - 1 hour: max 200 responses
        - Ignore threshold: 5 consecutive ignores → silence
        """
        
    def can_respond(self, channel_id: str) -> Tuple[bool, Optional[str]]:
        """
        Check if bot can respond in channel.
        
        Returns:
            (can_respond, reason_if_blocked)
        """
        
    def record_response(self, channel_id: str):
        """Record that bot sent a message"""
        
    def record_ignored(self, channel_id: str):
        """Record that bot was ignored (no engagement within 30s)"""
        
    def record_engagement(self, channel_id: str):
        """Record engagement (reaction, reply) - reduces ignore count"""
        
    def get_stats(self, channel_id: str) -> dict:
        """Get current rate limit stats for channel"""
```

**Preservation Note:** Keep the exact algorithm from the old bot. It's well-tuned for small servers.

**Full algorithm:** `docs/preserved_algorithms.md` (Section: SimpleRateLimiter)

---

## 5. Memory Tool Structure

### 5.1 Directory Layout

```
memories/
└── {bot_id}/              # e.g., "alpha"
    └── servers/
        └── {server_id}/   # Discord server (guild) ID
            ├── culture.md           # Server-wide norms
            ├── followups.json       # Pending follow-up items
            ├── channels/
            │   └── {channel_id}.md  # Per-channel context
            └── users/
                └── {user_id}.md     # Per-user profiles
```

**Example paths:**
```
/memories/alpha/servers/123456789/culture.md
/memories/alpha/servers/123456789/channels/987654321.md
/memories/alpha/servers/123456789/users/111222333.md
/memories/alpha/servers/123456789/followups.json
```

### 5.2 Memory File Schemas

#### Server Culture (`culture.md`)
```markdown
# Server: Example Gaming Server

**General Vibe:** Tech-focused gaming community, casual humor, helpful
**Active Hours:** 6pm-11pm PST weekdays, flexible weekends
**Member Count:** ~15 active, ~30 total
**Primary Topics:** Game development, machine learning, hardware

## Communication Norms
- Casual, minimal formality
- Technical discussions welcome
- Politics banned in #general
- Friendly roasting is common

## Running Gags & Inside Jokes
- "skill issue" = highest praise/compliment (ironic)
- Dana always forgets to share screen in meetings
- John's mechanical keyboard is "too loud"

## Bot Engagement Patterns
- #general: Low proactive success (20%)
- #gaming: High proactive success (75%)
- #dev: Medium, prefer technical help over banter (50%)

## Notes
- Server timezone: PST (most members)
- Don't engage between midnight-6am
```

#### Channel Context (`channels/{channel_id}.md`)
```markdown
# Channel: #gaming

**Purpose:** Gaming discussion, LFG posts, game recommendations
**Activity Level:** High (50+ messages/day)
**Primary Users:** Dana, John, Sarah, Mike

## Topics
- Game design debates (frequent)
- New release discussions
- Hardware recommendations
- "What should I play?" threads

## Bot Behavior Notes
- Proactive messages work well here
- Users appreciate game recommendations
- Engagement rate: 75% (very responsive)
- Best times: evenings, weekends

## Recent Context
- Minecraft server launched Sept 15
- Planning group sessions for new DLC
- Ongoing debate: tabs vs spaces in game configs
```

#### User Profile (`users/{user_id}.md`)
```markdown
# User: Dana

**Display Name:** Dana (prefers "D" in casual chat)
**Discord Tag:** Dana#1234
**Timezone:** PST
**Active Hours:** Usually 7pm-11pm weekdays

## Background
- ML Engineer at TechCorp (started Sept 2025)
- Previously job hunting, interviewed late Sept
- Strong technical background
- Interested in PyTorch, transformers, GPU optimization

## Communication Style
- Direct, appreciates technical depth
- No small talk needed, gets straight to point
- Responds well to detailed explanations
- Likes being challenged intellectually

## Recent Topics
- GPU recommendations for home ML rig
- PyTorch optimization techniques
- Transformer architecture discussions
- Asked about game AI implementations

## Follow-Up Items
- Asked about job interview outcome (completed Sept 24)
- Mentioned building new PC (ongoing)

## Notes
- Don't over-explain basics, D knows her stuff
- Okay to use technical jargon
- Appreciates dark humor
```

#### Follow-Ups (`followups.json`)
```json
{
  "pending": [
    {
      "id": "followup_001",
      "user_id": "111222333",
      "user_name": "Dana",
      "event": "Building new PC for ML work",
      "mentioned_date": "2025-09-22T14:30:00Z",
      "expected_completion": "2025-10-05",
      "follow_up_after": "2025-10-06T00:00:00Z",
      "priority": "medium",
      "context": "Waiting on GPU shipment, mentioned RTX 4090",
      "channel_id": "987654321"
    },
    {
      "id": "followup_002",
      "user_id": "444555666",
      "user_name": "John",
      "event": "Game jam weekend project",
      "mentioned_date": "2025-09-25T18:00:00Z",
      "expected_completion": "2025-09-29",
      "follow_up_after": "2025-09-30T00:00:00Z",
      "priority": "low",
      "context": "Making puzzle platformer, first jam",
      "channel_id": "987654321"
    }
  ],
  "completed": [
    {
      "id": "followup_000",
      "user_id": "111222333",
      "user_name": "Dana",
      "event": "Job interview at TechCorp",
      "followed_up": "2025-09-24T16:00:00Z",
      "outcome": "Got the job! Started Sept 2025",
      "completed_date": "2025-09-24T16:00:00Z"
    }
  ]
}
```

### 5.3 Memory Tool Usage Patterns

**The bot manages these files automatically.** You never edit them manually.

**When bot creates a memory:**
```
User: "I have my TechCorp interview on Tuesday, nervous"
Bot: "Good luck! What role is it again?"

[Bot internally makes tool call:]
memory.create(
  path="/memories/alpha/servers/123456789/followups.json",
  content='{"pending": [{"id": "followup_001", ...}]}'
)
```

**When bot updates a memory:**
```
User: "Got the job!"
Bot: "Congrats! When do you start?"

[Bot internally makes tool call:]
memory.str_replace(
  path="/memories/alpha/servers/123456789/users/111222333.md",
  old_str="Job hunting, interviewed at TechCorp",
  new_str="ML Engineer at TechCorp (started Sept 2025)"
)

memory.update_json(
  path="/memories/alpha/servers/123456789/followups.json",
  # Moves followup_001 to completed
)
```

**When bot reads memories:**
```
# Automatic - bot checks /memories/ before responding
# No explicit code needed, memory tool handles it

# Bot sees:
# - User profiles for everyone in conversation
# - Channel context for current channel
# - Server culture
# - Pending follow-ups
```

### 5.4 Security & Path Validation

**Critical:** All memory operations MUST stay within `/memories/` directory.

```python
def validate_memory_path(path: str) -> bool:
    """
    Validate that path is within allowed memory directory.
    Prevents directory traversal attacks.
    """
    base = Path("/memories").resolve()
    requested = Path(path).resolve()
    
    try:
        requested.relative_to(base)
        return True
    except ValueError:
        return False
```

**Never trust paths from tool calls without validation.**

---

## 6. Configuration System

### 6.1 Bot Configuration (YAML)

**Location:** `bots/{bot_id}.yaml`

**Example:** `bots/alpha.yaml`

```yaml
# Bot Identity
bot_id: alpha
name: "Claude (Alpha)"
description: "Sharp, witty Discord participant with dark humor"

# Discord Configuration
discord:
  token_env_var: "ALPHA_BOT_TOKEN"  # Environment variable containing Discord bot token
  servers:
    - "123456789012345678"  # Server IDs bot will join
    - "987654321098765432"

# Personality Configuration
personality:
  # Base system prompt
  base_prompt: |
    You're sharp, observant, with dark humor inspired by Anthony Jeselnik.
    You critique thoughtfully, never force jokes, know when silence is better.
    You don't always need the last word. Sometimes the best response is none.
    
    You prioritize substance over performance. Be helpful when needed,
    funny when natural, quiet when appropriate.
  
  # Response style preferences
  response_style:
    formality: 0.2              # 0=very casual, 1=very formal
    emoji_usage: "never"        # never | rare | moderate | frequent
    reaction_usage: "moderate"  # never | rare | moderate | frequent
    formatting: "minimal"       # minimal | moderate | rich
  
  # Engagement rules
  engagement:
    mention_response_rate: 1.0     # Always respond to @mentions
    technical_help_rate: 0.8       # High interest in helping
    humor_response_rate: 0.4       # Moderate humor engagement
    cold_conversation_rate: 0.1    # Low engagement when dead
    warm_conversation_rate: 0.25   # Moderate when steady
    hot_conversation_rate: 0.4     # Higher when active

# Reactive Engine Configuration
reactive:
  enabled: true
  check_interval_seconds: 30
  context_window: 20  # Recent messages to include
  
  # Cooldowns (seconds)
  cooldowns:
    per_user: 40
    single_message: 45
    multi_message: 75
    heavy_activity: 105

# Agentic Engine Configuration
agentic:
  enabled: true
  check_interval_hours: 1
  
  # Follow-up system
  followups:
    enabled: true
    auto_create: true  # Bot automatically creates follow-ups
    max_pending: 20
    priority_threshold: "medium"  # low | medium | high
    follow_up_delay_days: 1
    max_age_days: 14
  
  # Proactive engagement
  proactive:
    enabled: true
    min_idle_hours: 1.0
    max_idle_hours: 8.0
    min_provocation_gap_hours: 1.0
    max_per_day_global: 10
    max_per_day_per_channel: 3
    engagement_threshold: 0.3  # If <30% success, back off
    learning_window_days: 7
    quiet_hours: [0, 6]  # Don't engage midnight-6am
    
    # Channels where proactive engagement allowed
    allowed_channels:
      - "987654321098765432"  # #gaming
      - "111222333444555666"  # #general

# API Configuration
api:
  model: "claude-sonnet-4-5-20250929"
  max_tokens: 4096
  temperature: 1.0
  
  # Context management
  context_editing:
    enabled: true
    trigger_tokens: 100000
    keep_tool_uses: 3
    exclude_tools: ["memory"]
  
  # Rate limiting
  throttling:
    min_delay_seconds: 1.0
    max_concurrent: 10
  
  # Web search
  web_search:
    enabled: true
    max_daily: 300
    max_per_request: 3

# Rate Limiting (ported from SimpleRateLimiter)
rate_limiting:
  windows:
    short:
      duration_minutes: 5
      max_responses: 20
    long:
      duration_minutes: 60
      max_responses: 200
  ignore_threshold: 5  # Consecutive ignores before silence
  engagement_tracking_delay: 30  # Seconds to wait before checking

# Image Processing
images:
  enabled: true
  max_per_message: 5
  compression_target: 0.73  # 73% of API limit
  
# Logging
logging:
  level: "INFO"  # DEBUG | INFO | WARNING | ERROR
  file: "logs/alpha.log"
  max_size_mb: 50
  backup_count: 3
```

### 6.2 Configuration Loading

```python
@dataclass
class BotConfig:
    """Loaded from YAML, validated, provides typed access"""
    bot_id: str
    name: str
    personality: PersonalityConfig
    reactive: ReactiveConfig
    agentic: AgenticConfig
    api: APIConfig
    # ... etc
    
    @classmethod
    def load(cls, yaml_path: Path) -> 'BotConfig':
        """Load and validate configuration from YAML"""
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        return cls(**data)  # Validate with dataclass
```

---

## 7. Preserved Algorithms

These algorithms from the old bot are **battle-tested** and should be preserved exactly.

### 7.1 SimpleRateLimiter

**Original location:** `slh.py` lines 119-164

**Preservation strategy:** Port to `core/rate_limiter.py` with identical logic

**Algorithm:**
```python
class RateLimiter:
    """
    Two-window rate limiting with engagement-based adaptation.
    
    Windows:
    - Short: 5 minutes, max 20 responses
    - Long: 1 hour, max 200 responses
    
    Adaptation:
    - Track ignored messages (no reaction/reply within 30s)
    - After 5 consecutive ignores, go silent
    - Engagement (reaction, reply) reduces ignore count
    """
    
    def __init__(self):
        self.response_times = defaultdict(list)
        self.ignored_count = defaultdict(int)
        
    def can_respond(self, channel_id: str) -> Tuple[bool, Optional[str]]:
        now = datetime.now()
        times = self.response_times[channel_id]
        
        # Remove old responses outside windows
        times = [t for t in times if (now - t).total_seconds() < 3600]
        self.response_times[channel_id] = times
        
        # Check short window (5 min)
        short_window = [t for t in times if (now - t).total_seconds() < 300]
        if len(short_window) >= 20:
            return False, "rate_limit_short"
        
        # Check long window (1 hour)
        if len(times) >= 200:
            return False, "rate_limit_long"
        
        # Check ignore threshold
        if self.ignored_count[channel_id] >= 5:
            return False, "ignored_threshold"
        
        return True, None
    
    def record_response(self, channel_id: str):
        self.response_times[channel_id].append(datetime.now())
    
    def record_ignored(self, channel_id: str):
        self.ignored_count[channel_id] += 1
    
    def record_engagement(self, channel_id: str):
        self.ignored_count[channel_id] = max(0, self.ignored_count[channel_id] - 1)
```

**Why preserve exactly:** This is tuned for small servers (< 50 users). The thresholds and windows work well in practice.

### 7.2 Conversation Momentum

**Original location:** `slh.py` lines 711-733 (`get_conversation_momentum()`)

**Algorithm:**
```python
def calculate_conversation_momentum(messages: List[StoredMessage]) -> str:
    """
    Classify conversation activity level.
    
    Returns: "hot" | "warm" | "cold"
    
    Logic:
    - Calculate average gap between messages
    - Hot: avg gap < 15 minutes (rapid)
    - Warm: avg gap < 1 hour (steady)
    - Cold: avg gap > 1 hour (slow)
    """
    if len(messages) < 2:
        return "cold"
    
    gaps = []
    for i in range(1, len(messages)):
        gap = (messages[i].timestamp - messages[i-1].timestamp).total_seconds() / 60
        gaps.append(gap)
    
    avg_gap = sum(gaps) / len(gaps)
    
    if avg_gap < 15:
        return "hot"
    elif avg_gap < 60:
        return "warm"
    else:
        return "cold"
```

**Usage:** Informs response rate. Hot conversations get higher response probability.

**Calibration note:** These thresholds assume small servers. May need adjustment for large servers.

### 7.3 Image Compression Pipeline

**Original location:** `slh.py` lines 220-226, 410-581

**Full documentation:** See `docs/preserved_algorithms.md` (Section: Image Processing)

**Strategy sequence:**
1. Check if compression needed (size, dimensions, format)
2. Try preserving format with optimization
3. Try JPEG progressive quality reduction (85→75→65→...→10)
4. Try WebP conversion (85→75→65→...→15)
5. Nuclear resize (0.7x dimensions)
6. Thumbnail fallback (512x512)

**Target:** 73% of API limit (accounts for Base64 overhead)

**Security:**
- Only Discord CDN URLs allowed
- 50MB download limit
- 30s timeout per download
- Streaming with chunk-based size checking

**Preservation note:** This pipeline is complex but works reliably. Port to `tools/image_processor.py` with tests.

### 7.4 Engagement Tracking

**Original location:** `slh.py` lines 1359-1386 (`track_engagement()`)

**Algorithm:**
```python
async def track_engagement(message_id: str, channel_id: str):
    """
    Wait 30 seconds, then check if message got engagement.
    
    Engagement = reactions OR replies referencing this message
    
    If engaged: record_engagement()
    If ignored: record_ignored()
    """
    await asyncio.sleep(30)
    
    # Fetch fresh message to see reactions
    message = await channel.fetch_message(message_id)
    
    # Check reactions
    has_reactions = len(message.reactions) > 0
    
    # Check replies
    recent = await channel.history(after=message, limit=10).flatten()
    has_replies = any(
        msg.reference and msg.reference.message_id == message_id
        for msg in recent
    )
    
    if has_reactions or has_replies:
        rate_limiter.record_engagement(channel_id)
    else:
        rate_limiter.record_ignored(channel_id)
```

**Why preserve:** The 30-second delay is well-calibrated. Gives users time to react without being too slow.

---

## 8. Discord Integration

### 8.1 Discord.py Setup

**Intents required:**
```python
intents = discord.Intents.default()
intents.message_content = True  # Read message text
intents.reactions = True        # Track reactions
intents.guilds = True           # Server info
intents.members = True          # Member list
```

**Permissions required:**
- Read Message History
- Send Messages
- Attach Files
- Add Reactions
- Read Channels
- View Channel

### 8.2 DiscordTools (Query Bot)

**Purpose:** Give Claude tools to query Discord state.

**Implementation:**
```python
class DiscordTools:
    """Tools for Claude to query Discord state"""
    
    def __init__(self, client: discord.Client):
        self.client = client
    
    @tool
    async def get_server_members(self, server_id: str) -> List[dict]:
        """
        Get list of all members in server.
        
        Returns: [{"id": "...", "name": "...", "roles": [...]}]
        """
        guild = self.client.get_guild(int(server_id))
        return [
            {
                "id": str(m.id),
                "name": m.name,
                "display_name": m.display_name,
                "roles": [r.name for r in m.roles],
                "bot": m.bot
            }
            for m in guild.members
        ]
    
    @tool
    async def get_active_threads(self, channel_id: str) -> List[dict]:
        """Get all active threads in channel"""
        channel = self.client.get_channel(int(channel_id))
        threads = await channel.active_threads()
        return [
            {
                "id": str(t.id),
                "name": t.name,
                "message_count": t.message_count,
                "created_at": t.created_at.isoformat()
            }
            for t in threads
        ]
    
    @tool
    async def get_pinned_messages(self, channel_id: str) -> List[dict]:
        """Get pinned messages in channel"""
        channel = self.client.get_channel(int(channel_id))
        pins = await channel.pins()
        return [
            {
                "id": str(m.id),
                "author": m.author.name,
                "content": m.content,
                "created_at": m.created_at.isoformat()
            }
            for m in pins
        ]
    
    @tool
    async def get_channel_info(self, channel_id: str) -> dict:
        """Get channel metadata"""
        channel = self.client.get_channel(int(channel_id))
        return {
            "id": str(channel.id),
            "name": channel.name,
            "type": str(channel.type),
            "topic": channel.topic,
            "created_at": channel.created_at.isoformat()
        }
```

**Usage in Claude API:**
```python
tools = [
    {"type": "memory"},
    get_server_members,
    get_active_threads,
    get_pinned_messages,
    get_channel_info
]

response = await client.messages.create(
    model="claude-sonnet-4-5-20250929",
    tools=tools,
    messages=[...]
)
```

Now Claude can ask "Who's in this server?" or "What are the pinned messages?" during conversation.

### 8.3 Reply Chain Resolution

**Discord's reply feature:** Users can reply to old messages, creating a chain.

**Problem:** If someone replies to a message from 2 hours ago, the bot needs that context.

**Solution:**
```python
async def build_reply_chain(message: discord.Message) -> List[dict]:
    """
    Build parent message chain for replies.
    
    Discord message.reference points to parent.
    Walk up the chain to root.
    """
    chain = []
    current = message
    
    while current.reference:
        try:
            parent = await current.channel.fetch_message(
                current.reference.message_id
            )
            chain.append({
                "author": parent.author.name,
                "content": parent.content,
                "timestamp": parent.created_at.isoformat()
            })
            current = parent
        except discord.NotFound:
            break  # Message deleted
    
    return list(reversed(chain))  # Oldest first
```

**Include in context:**
```python
if message.reference:
    reply_chain = await build_reply_chain(message)
    context += f"\n\n=== REPLY CHAIN ===\n{format_chain(reply_chain)}"
```

### 8.4 Typing Indicators

**User experience:** Show "Claude is typing..." while thinking.

**Implementation:**
```python
async def send_with_typing(channel: discord.TextChannel, 
                          content: str, 
                          delay: float = 2.0):
    """
    Send message with typing indicator.
    
    Args:
        channel: Where to send
        content: Message text
        delay: Typing duration (seconds)
    """
    async with channel.typing():
        await asyncio.sleep(delay)
    
    return await channel.send(content)
```

**From response plan:**
```python
for response in plan.responses:
    await send_with_typing(
        channel=channel,
        content=response.message,
        delay=response.delay  # From plan
    )
```

### 8.5 Event Handlers

**Core events:**
```python
@client.event
async def on_ready():
    """Bot connected to Discord"""
    print(f"Logged in as {client.user}")
    
    # Load recent history
    await load_history()
    
    # Start task loops
    check_conversations.start()
    agentic_loop.start()

@client.event
async def on_message(message: discord.Message):
    """New message received"""
    # Ignore bot's own messages
    if message.author == client.user:
        return
    
    # Store in memory
    await message_storage.add_message(message)
    
    # Update state
    update_pending_channels(message.channel.id)
    
    # Urgent handling (mentions)
    if client.user in message.mentions:
        await reactive_engine.handle_urgent(message)

@client.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    """Reaction added to message"""
    # If reaction to bot message, record engagement
    if reaction.message.author == client.user:
        rate_limiter.record_engagement(reaction.message.channel.id)
```

---

## 9. Implementation Phases

### Phase 1: Foundation (COMPLETE)

**Goal:** Core infrastructure, bot can respond to messages.

**Deliverables:**
1. Project structure setup ✅
2. Bot manager CLI (spawn, stop, logs) ✅
3. Configuration system (YAML loading) ✅
4. MessageMemory (SQLite storage) ✅
5. Basic ReactiveEngine (@mention handling) ✅
6. MemoryManager (path helpers, read-only) ✅
7. Discord.py integration (connect, receive messages) ✅
8. Message edit/delete tracking ✅
9. Conversation logging (parseable format) ✅
10. Rate limiting (SimpleRateLimiter port) ✅
11. Engagement tracking (reactions + ignores) ✅
12. Clean shutdown (background task cancellation) ✅
13. **Extended thinking integration** ✅
    - Step-by-step reasoning for better responses
    - Thinking trace logging for debugging
    - Configurable thinking budget

**Success criteria:** ✅ ALL MET
- Bot connects to Discord
- Responds to @mentions with intelligent responses
- Stores all messages (including bot's own) in SQLite
- Tracks message edits and deletes
- Configuration loads from YAML
- Can spawn/stop via CLI
- Rate limiting prevents spam
- Engagement tracking adapts behavior
- Shutdown is fast (<2 seconds)
- Extended thinking improves response quality
- Thinking traces visible in logs

**Skipped for Phase 1:**
- Agentic engine
- Follow-ups
- Proactive engagement
- Image processing
- Web search
- Memory tool (moved to Phase 2)
- Context editing (moved to Phase 2)

### Phase 2: Intelligence (IN PROGRESS)

**Goal:** Smart responses with memory and context.

**Deliverables:**
1. ContextBuilder (assemble comprehensive context)
   - Mention name resolution (show `@username` instead of `<@123456>`)
   - Reply chain threading (show reply relationships in context)
   - Smart context window management
2. Memory tool integration (bot reads/writes memories)
3. Context editing integration (token management)
4. RateLimiter (port SimpleRateLimiter)
5. Response plan execution (typing, delays, cooldowns)
6. Engagement tracking
   - Reaction emoji tracking (which emoji was used, not just "engaged")
   - Reply detection (already in Phase 1)
   - Engagement-based adaptation
7. Full message context features
   - Display reply chains in conversation history
   - Show reactions on messages in context
   - Resolve user mentions to readable names

**Success criteria:**
- Bot responds intelligently based on context
- Bot manages own user profiles
- Rate limiting prevents spam
- Context editing prevents bloat
- Engagement tracking adapts behavior
- Bot sees reply relationships and emoji reactions
- Mentions display as readable names, not IDs

**Skip for Phase 2:**
- Agentic engine still
- Follow-ups still
- Advanced tools (image, web search)

### Phase 3: Autonomy (Days 8-10)

**Goal:** Proactive behaviors, follow-ups, full agentic features.

**Deliverables:**
1. AgenticEngine implementation
2. Follow-up system (tracking, checking, execution)
3. Proactive engagement (provocation system)
4. Memory maintenance tasks
5. Adaptive learning (engagement history tracking)

**Success criteria:**
- Bot checks follow-ups hourly
- Bot initiates conversations naturally
- Bot learns which channels respond well
- Follow-ups feel natural, not robotic

### Phase 4: Tools & Polish (Days 11-14)

**Goal:** Feature completeness, production-ready.

**Deliverables:**
1. Image processing pipeline (port compression)
2. Web search integration
3. DiscordTools (query server state)
4. Advanced CLI features (config validation, memory inspection)
5. Logging improvements
6. Error handling hardening
7. Testing suite
8. Documentation

**Success criteria:**
- Bot processes images reliably
- Web search works within budget
- Bot can query Discord state
- CLI is polished
- Errors handled gracefully
- Tests pass

---

## 10. Testing Strategy

### 10.1 Unit Tests

**Test core components in isolation:**

```python
# tests/test_rate_limiter.py
def test_rate_limiter_short_window():
    limiter = RateLimiter()
    channel = "test_channel"
    
    # Send 20 messages (limit)
    for _ in range(20):
        assert limiter.can_respond(channel)[0]
        limiter.record_response(channel)
    
    # 21st should be blocked
    can_respond, reason = limiter.can_respond(channel)
    assert not can_respond
    assert reason == "rate_limit_short"

# tests/test_memory_manager.py
def test_memory_paths():
    memory = MemoryManager(bot_id="alpha", memory_base_path=Path("/tmp/test"))
    
    user_path = memory.get_user_profile_path("server123", "user456")
    assert user_path == "/memories/servers/server123/users/user456.md"

# tests/test_context_builder.py
async def test_reply_chain_resolution():
    # Mock discord messages
    # Build chain
    # Assert correct order
    pass
```

**Coverage targets:**
- RateLimiter: 100%
- MemoryManager path helpers: 100%
- Context builder formatting: 90%
- Image compression: 80% (edge cases tricky)

### 10.2 Integration Tests

**Test component interactions:**

```python
# tests/integration/test_reactive_flow.py
async def test_message_to_response():
    """End-to-end: Message arrives → bot responds"""
    # Setup mock Discord client
    # Send test message
    # Assert bot responds appropriately
    # Assert message stored in SQLite
    # Assert engagement tracked
    pass

# tests/integration/test_memory_integration.py
async def test_bot_updates_profile():
    """Test bot can create/update memories via tool"""
    # Send message: "Dana got a new job"
    # Check that memory tool call was made
    # Verify profile updated
    pass
```

### 10.3 Manual Testing

**Test with real Discord:**

**Phase 1 checklist:**
- [ ] Bot connects
- [ ] Responds to @mention
- [ ] Message stored in database
- [ ] CLI commands work

**Phase 2 checklist:**
- [ ] Responds intelligently
- [ ] Uses memory context
- [ ] Rate limiting works
- [ ] Cooldowns prevent spam
- [ ] Reply chains work

**Phase 3 checklist:**
- [ ] Follow-ups trigger naturally
- [ ] Proactive messages feel appropriate
- [ ] Bot learns from engagement

**Phase 4 checklist:**
- [ ] Images process reliably
- [ ] Web search within budget
- [ ] Discord tools work
- [ ] No crashes over 24h

### 10.4 Performance Testing

**Metrics to track:**

| Metric | Target | How to measure |
|--------|--------|----------------|
| Response latency | < 5s | Time from message to response |
| Memory usage | < 500MB | Monitor process RSS |
| SQLite queries | < 100ms | Profile database calls |
| API call rate | < 60/min | Track anthropic client |
| Token usage | < 1M/day | Track via response.usage |

**Load test:**
- Simulate 50 messages in 5 minutes
- Verify rate limiter works
- Verify no crashes
- Verify memory stable

---

## Appendix A: Response Plan JSON Schema

**Generated by Claude during conversation analysis.**

**Structure:**
```json
{
  "should_respond": true,
  "response_strategy": "single",
  "responses": [
    {
      "target": "Dana's question about PyTorch",
      "message": "For optimization, check out torch.compile() in 2.0+",
      "delay": 3,
      "use_web_search": false,
      "search_query": null,
      "send_image": null
    }
  ],
  "reasoning": "Technical question, I can answer directly",
  "conversation_momentum": "warm",
  "triggering_messages": [
    {
      "author": "Dana",
      "content": "Anyone know PyTorch optimization tricks?",
      "timestamp": "2025-09-30T14:30:00Z"
    }
  ]
}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `should_respond` | boolean | Whether bot should respond |
| `response_strategy` | string | "none" \| "single" \| "double" |
| `responses` | array | List of messages to send |
| `responses[].target` | string | What/who responding to |
| `responses[].message` | string | Actual response text |
| `responses[].delay` | number | Typing duration (seconds) |
| `responses[].use_web_search` | boolean | Trigger web search |
| `responses[].search_query` | string? | Search query if applicable |
| `responses[].send_image` | string? | Image URL if applicable |
| `reasoning` | string | Why bot chose this response |
| `conversation_momentum` | string | "hot" \| "warm" \| "cold" |
| `triggering_messages` | array | Messages that triggered analysis |

**Execution:**
```python
async def execute_response_plan(plan: ResponsePlan, channel: discord.TextChannel):
    if not plan.should_respond:
        return
    
    for response in plan.responses:
        # Check rate limits
        can_respond, reason = rate_limiter.can_respond(channel.id)
        if not can_respond:
            break
        
        # Web search if needed
        if response.use_web_search:
            search_result = await web_search(response.search_query)
            response.message += f"\n\n{search_result}"
        
        # Send with typing
        async with channel.typing():
            await asyncio.sleep(response.delay)
        
        sent_message = await channel.send(response.message)
        
        # Record and track
        rate_limiter.record_response(channel.id)
        asyncio.create_task(track_engagement(sent_message.id, channel.id))
        
        # Set cooldowns
        set_cooldowns_based_on_plan(plan)
```

---

## Appendix B: Follow-Up System Details

### B.1 Follow-Up Creation

**Bot automatically creates follow-ups during conversation.**

**Triggers:**
- User mentions future event
- User mentions waiting for something
- User expresses concern about upcoming event
- User mentions something in progress

**Priority scoring:**
```python
def score_priority(event_type: str, user_emotion: str) -> str:
    """
    Determine follow-up priority.
    
    High: Job interviews, medical, important life events
    Medium: Projects, moves, travel
    Low: Casual plans, general mentions
    """
    high_keywords = ["interview", "surgery", "wedding", "funeral"]
    medium_keywords = ["project", "moving", "trip", "exam"]
    
    if any(kw in event_type.lower() for kw in high_keywords):
        return "high"
    elif any(kw in event_type.lower() for kw in medium_keywords):
        return "medium"
    else:
        return "low"
```

### B.2 Follow-Up Checking

**Agentic engine checks hourly:**

```python
async def check_followups(server_id: str) -> List[ProactiveAction]:
    followups = await memory.read_json(f"/memories/servers/{server_id}/followups.json")
    actions = []
    
    for followup in followups.get("pending", []):
        # Check if due
        follow_up_after = datetime.fromisoformat(followup["follow_up_after"])
        if datetime.now() < follow_up_after:
            continue
        
        # Check if user active recently
        user_id = followup["user_id"]
        if not await is_user_active_recently(user_id, hours=24):
            continue  # Don't follow up if user hasn't been around
        
        # Create action
        actions.append(ProactiveAction(
            type="followup",
            user_id=user_id,
            user_name=followup["user_name"],
            channel_id=followup["channel_id"],
            message=f"Hey {followup['user_name']}, how did {followup['event']} go?",
            priority=followup["priority"],
            context=followup["context"]
        ))
    
    return actions
```

### B.3 Natural Integration

**Follow-ups don't always send standalone messages.**

**Options:**
1. **Standalone:** Bot sends message when channel is idle
2. **Woven in:** Bot adds follow-up to ongoing conversation
3. **Deferred:** Bot waits for natural opportunity

**Decision logic:**
```python
async def decide_followup_delivery(action: ProactiveAction) -> str:
    """
    Decide how to deliver follow-up.
    
    Returns: "standalone" | "woven" | "deferred"
    """
    channel = get_channel(action.channel_id)
    recent_activity = await get_recent_activity(channel, minutes=10)
    
    if not recent_activity:
        # Channel idle, standalone is fine
        return "standalone"
    
    if action.priority == "high":
        # Important, send now even if active
        return "standalone"
    
    # Check if user is in current conversation
    if action.user_id in [m.author.id for m in recent_activity]:
        # User is active, weave it in
        return "woven"
    
    # Defer to next natural opportunity
    return "deferred"
```

**Woven example:**
```
Dana: "Anyone want to play Minecraft?"
Bot: "I'm down. Oh, and how did that PyTorch project go?"
```

### B.4 Completion Handling

**After follow-up, bot updates memory:**

```python
# Move to completed
completed_followup = {
    "id": followup["id"],
    "user_id": followup["user_id"],
    "user_name": followup["user_name"],
    "event": followup["event"],
    "followed_up": datetime.now().isoformat(),
    "outcome": "Got response from user",
    "completed_date": datetime.now().isoformat()
}

# Bot makes memory tool call
memory.update_json(
    path=f"/memories/servers/{server_id}/followups.json",
    operation="move_to_completed",
    followup_id=followup["id"],
    completed_data=completed_followup
)
```

**User profile update:**
```python
# If follow-up reveals new info, update profile
memory.str_replace(
    path=f"/memories/servers/{server_id}/users/{user_id}.md",
    old_str="Working on PyTorch project",
    new_str="Completed PyTorch project successfully"
)
```

---

## Appendix C: Image Processing Pipeline

**Ported from:** Old bot's compression system (slh.py lines 410-581)

**Full documentation:** `docs/preserved_algorithms.md`

### C.1 Overview

**Goal:** Compress images to fit within Claude API limits while preserving quality.

**Constraints:**
- API limit: 5MB per image (raw)
- Target: 73% of limit (~3.65MB) to account for Base64 overhead
- Max images per message: 5

### C.2 Processing Flow

```python
async def process_images(attachments: List[discord.Attachment]) -> List[dict]:
    """
    Process all image attachments from message.
    
    Returns: List of Claude API image blocks
    """
    images = [a for a in attachments if is_image(a)]
    
    if len(images) > 5:
        images = images[:5]  # Limit to 5
    
    # Process concurrently (max 3 at once)
    semaphore = asyncio.Semaphore(3)
    tasks = [process_single_image(img, semaphore) for img in images]
    results = await asyncio.gather(*tasks)
    
    return [r for r in results if r is not None]

async def process_single_image(attachment: discord.Attachment, 
                               semaphore: asyncio.Semaphore) -> Optional[dict]:
    """Process single image attachment"""
    async with semaphore:
        # 1. Security check
        if not is_discord_cdn(attachment.url):
            return None
        
        # 2. Download
        image_data = await download_image(attachment.url)
        
        # 3. Check if compression needed
        if needs_compression(image_data, attachment.size):
            image_data = await compress_image(image_data)
        
        # 4. Encode to Base64
        encoded = base64.b64encode(image_data).decode()
        
        # 5. Return Claude API format
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": guess_mime_type(attachment.filename),
                "data": encoded
            }
        }
```

### C.3 Compression Strategies

**Sequential attempts until size target met:**

```python
async def compress_image(image_bytes: bytes) -> bytes:
    """
    Apply compression strategies sequentially.
    
    Strategies:
    1. Optimize current format
    2. JPEG quality reduction (85→75→65→...→10)
    3. WebP conversion (85→75→65→...→15)
    4. Nuclear resize (0.7x dimensions)
    5. Thumbnail fallback (512x512)
    """
    img = Image.open(BytesIO(image_bytes))
    target_size = int(5 * 1024 * 1024 * 0.73)  # 73% of 5MB
    
    # Strategy 1: Optimize
    result = optimize_format(img)
    if len(result) <= target_size:
        return result
    
    # Strategy 2: JPEG quality
    if img.format in ['JPEG', 'JPG']:
        for quality in [85, 75, 65, 55, 45, 35, 25, 15, 10]:
            result = save_jpeg(img, quality)
            if len(result) <= target_size:
                return result
    
    # Strategy 3: WebP conversion
    for quality in [85, 75, 65, 55, 45, 35, 25, 15]:
        result = save_webp(img, quality)
        if len(result) <= target_size:
            return result
    
    # Strategy 4: Nuclear resize
    img_resized = img.resize(
        (int(img.width * 0.7), int(img.height * 0.7)),
        Image.Resampling.LANCZOS
    )
    result = save_webp(img_resized, quality=75)
    if len(result) <= target_size:
        return result
    
    # Strategy 5: Thumbnail fallback
    img.thumbnail((512, 512), Image.Resampling.LANCZOS)
    return save_webp(img, quality=85)
```

### C.4 Security

**Whitelist Discord CDN:**
```python
def is_discord_cdn(url: str) -> bool:
    """Only allow Discord CDN URLs"""
    allowed_domains = [
        "cdn.discordapp.com",
        "media.discordapp.net"
    ]
    parsed = urlparse(url)
    return parsed.netloc in allowed_domains

async def download_image(url: str, 
                        max_size: int = 50 * 1024 * 1024,
                        timeout: int = 30) -> bytes:
    """
    Securely download image with size/time limits.
    
    Args:
        url: Image URL (must be Discord CDN)
        max_size: Max download size (50MB)
        timeout: Max download time (30s)
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=timeout) as resp:
            if resp.status != 200:
                raise ImageDownloadError(f"HTTP {resp.status}")
            
            # Stream download, check size incrementally
            chunks = []
            total_size = 0
            
            async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1MB chunks
                chunks.append(chunk)
                total_size += len(chunk)
                
                if total_size > max_size:
                    raise ImageDownloadError("Image too large")
            
            return b"".join(chunks)
```

---

## Appendix D: Rate Limiting Algorithms

### D.1 SimpleRateLimiter (Ported)

**See Section 7.1** for full algorithm.

**Key features:**
- Two-window system (5min/1hr)
- Adaptive based on engagement
- Ignore threshold (5 consecutive)

### D.2 Per-User Cooldowns

**Prevents bot from spam-replying to one user:**

```python
class UserCooldowns:
    def __init__(self, cooldown_seconds: int = 40):
        self.last_reply = {}  # user_id -> timestamp
        self.cooldown = cooldown_seconds
    
    def can_reply_to_user(self, user_id: str) -> bool:
        if user_id not in self.last_reply:
            return True
        
        elapsed = (datetime.now() - self.last_reply[user_id]).total_seconds()
        return elapsed >= self.cooldown
    
    def record_reply(self, user_id: str):
        self.last_reply[user_id] = datetime.now()
```

**Usage during analysis:**
```python
# Filter messages from users on cooldown
def filter_messages(messages: List[Message], user_cooldowns: UserCooldowns):
    return [
        msg for msg in messages
        if user_cooldowns.can_reply_to_user(msg.author.id)
    ]
```

### D.3 Channel Cooldowns

**Prevents rapid-fire responses:**

```python
class ChannelCooldowns:
    def __init__(self, config: dict):
        self.cooldown_until = {}  # channel_id -> datetime
        self.config = config
    
    def set_cooldown(self, channel_id: str, message_count: int):
        """
        Set cooldown based on response count.
        
        Single message: 45s
        2-3 messages: 75s
        4+ messages: 105s
        """
        if message_count == 1:
            duration = self.config["single_message"]  # 45
        elif message_count <= 3:
            duration = self.config["multi_message"]  # 75
        else:
            duration = self.config["heavy_activity"]  # 105
        
        self.cooldown_until[channel_id] = datetime.now() + timedelta(seconds=duration)
    
    def is_in_cooldown(self, channel_id: str) -> bool:
        if channel_id not in self.cooldown_until:
            return False
        return datetime.now() < self.cooldown_until[channel_id]
```

**Set after response:**
```python
# After sending messages
channel_cooldowns.set_cooldown(
    channel_id=channel.id,
    message_count=len(plan.responses)
)
```

---

## Appendix E: Production Deployment (Future Reference)

**Note:** This is for future production deployment, not current focus.

### E.1 EC2 Deployment

**When you're ready to deploy to AWS EC2:**

**Instance sizing:**
- t3.small (2 vCPU, 2GB RAM) sufficient for 2-4 bots
- t3.medium (2 vCPU, 4GB RAM) for 5-10 bots

**Setup:**
```bash
# Install dependencies
sudo apt update
sudo apt install python3.9 python3-pip git

# Clone repo
git clone <repo-url>
cd discord-claude-framework

# Install packages
pip3 install -r requirements.txt

# Configure secrets (use AWS Secrets Manager in production)
cp .env.example .env
nano .env  # Add keys

# Run as service
sudo nano /etc/systemd/system/claude-bot.service
```

**systemd service file:**
```ini
[Unit]
Description=Discord Claude Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/discord-claude-framework
ExecStart=/usr/bin/python3 bot_manager.py spawn alpha
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Start service:**
```bash
sudo systemctl enable claude-bot
sudo systemctl start claude-bot
sudo systemctl status claude-bot
```

### E.2 Production .gitignore

**In production, don't commit state:**

```gitignore
# Secrets
.env

# State (use proper backups instead)
memories/
persistence/
logs/

# Python
__pycache__/
*.pyc
*.pyo
```

**Use proper backup strategy:**
- S3 sync for memories/
- RDS for message history
- CloudWatch for logs

### E.3 Monitoring

**Add monitoring in production:**

**CloudWatch metrics:**
- API call rate
- Error rate
- Response latency
- Memory usage
- CPU usage

**Alerting:**
- Bot offline > 5 minutes
- Error rate > 5%
- Memory usage > 80%
- API budget exceeded

---

## Appendix F: Glossary

**Agentic Behavior:** Autonomous decision-making and action without explicit user commands. The bot initiates conversations, checks follow-ups, and maintains memories proactively.

**Agentic Engine:** Background process that handles proactive behaviors like follow-ups and engagement opportunities. Runs periodically (hourly) rather than reactively.

**Context Editing:** Anthropic API feature that automatically clears old tool results to prevent token bloat in long conversations. Preserves recent context while removing old data.

**Context Package:** Data structure containing all context for Claude API call: system prompt, recent messages, memory context, reply chain, images.

**Cooldown:** Time period where bot won't respond in a channel or to a user. Prevents spam and allows natural conversation pacing.

**Follow-Up:** Bot-tracked event or item that triggers a future check-in. Example: "Dana has job interview Tuesday" → follow up Wednesday.

**Memory Tool:** Anthropic API feature that lets Claude read/write files in a `/memories/` directory. Bot manages its own knowledge base.

**Momentum:** Conversation activity level. Hot (rapid exchanges), warm (steady discussion), cold (slow/idle).

**Proactive Engagement:** Bot-initiated conversation when channel is idle. Also called "provocation."

**Reactive Engine:** Fast message-handling system. Responds to incoming messages within seconds.

**Reply Chain:** Discord feature where messages can reference parent messages. Bot resolves the chain for context.

**Response Plan:** JSON structure describing how bot should respond: messages to send, delays, whether to search, etc.

**Rate Limiting:** Restrictions on response frequency to prevent spam. Multiple layers: per-user, per-channel, global.

**Typing Indicator:** "Bot is typing..." visual shown to users while bot prepares response.

---

**END OF SPECIFICATION**

This spec is ready for implementation. Next steps:

1. Create `docs/` directory with API reference files
2. Review and adjust any configs to taste
3. Hand to Claude Code with instruction: "Implement this spec"
4. Watch it build

Let me know if you want me to draft the docs/ reference files next, or if you want to adjust anything in this spec first.
