# Changelog

All notable changes to Discord-Claude Bot Framework will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Planned for v0.5.0
- Enhanced analytics dashboard
- Thread and voice channel support
- Performance optimizations
- Community feedback integration

### Known Issues
- None currently tracked

---

## [0.4.0-beta] - 2025-10-20

**Status:** Closed beta release - framework feature-complete

### Added
- **Discord Tools** - Agentic search & view architecture
  - `search_messages`: FTS5 full-text search returning message IDs only
  - `view_messages`: 4 viewing modes (recent, around, first, range)
  - Token-efficient two-step workflow for context retrieval
  - Cross-channel search across all accessible channels
  - Files: `tools/discord_tools.py`

- **Message Reindexing System** - Keep search index current
  - Daily automatic reindex at 3 AM UTC
  - Manual trigger via `@bot reindex` command
  - UPSERT logic for handling edited messages
  - Background execution without blocking bot
  - Files: `core/discord_client.py` (daily task), `core/message_memory.py` (UPSERT)

- **Image Processing Pipeline** - 6-strategy compression cascade
  - Automatic token limit compliance (max 1600 tokens per image)
  - Base64 encoding for Claude API
  - Support up to 5 images per message
  - Maintains aspect ratios during resize
  - Fallback strategies for optimal compression
  - Files: `tools/image_processor.py`

- **Web Search Integration** - Anthropic server tools
  - `web_search_20250305` with beta headers
  - `web_fetch_20250910` with citations enabled
  - Daily quota management (300 searches default)
  - Citation extraction and display in responses
  - Server tool usage tracking (monitors `server_tool_use` blocks)
  - Files: `tools/web_search.py`, `core/reactive_engine.py`

- **Configuration System Enhancements**
  - Basic config validation on startup (checks env vars, validates ranges)
  - Support for deployment/ submodule configs
  - Template files for public distribution (.env.example, alpha.yaml.example)
  - Deployment path resolution (deployment/ → bots/ → templates)
  - Files: `core/config.py`, `bot_manager.py`

- **Deployment Support**
  - Git submodule approach for private configs
  - .gitignore updated for framework vs deployment separation
  - Template files for easy onboarding

### Fixed
- **Race Condition in Context Building** - Bot sent 4 combined responses
  - Solution: Moved context building inside semaphore with `exclude_message_ids`
  - Files: `core/context_builder.py` lines 163-176

- **UPSERT Bug in Message Backfill** - Edited messages not searchable
  - Problem: `INSERT OR IGNORE` skipped updates on collision
  - Solution: Changed to `INSERT OR REPLACE` for proper UPSERT behavior
  - Files: `core/message_memory.py` lines 281-311

- **Forwarded Message Handling** - Exception on inaccessible content
  - Solution: Added clear marker for forwarded messages
  - Files: `core/context_builder.py` lines 115-120

- **Discord Tools Execution** - Silent failures when returning None
  - Solution: Added None check and warning logging
  - Files: `core/reactive_engine.py` lines 406-411

- **Server Tool Tracking** - Quota tracking checked wrong block type
  - Problem: Checked `tool_use` blocks instead of `server_tool_use`
  - Solution: Track server tools after API response in response.content
  - Files: `core/reactive_engine.py` lines 262-269, 815-822

### Changed
- Documentation restructure (docs/ARCHITECTURE.md + CHANGELOG.md replace PROJECT_SPEC.md)
- Simplified config loading (removed deployment submodule complexity)
- Semantic versioning instead of phase naming
- Config validation returns list of errors instead of raising exceptions
- Added export/import tool for portable backups

### Security
- API key isolation via environment variables
- Per-channel rate limiting (20/min, 100/hour)
- Web search quota tracking for cost management
- Memory isolation per server/channel
- Environment variable validation on startup

---

## [0.3.0] - 2025-10-04

**Focus:** Autonomous agentic behaviors

### Added
- **Agentic Engine** - Background autonomous loop
  - Hourly check cycle (configurable interval)
  - Follow-up system with event tracking
  - Proactive engagement in idle channels
  - Quiet hours support (configurable time window)
  - Files: `core/agentic_engine.py`

- **Follow-Up System** - Automated user event tracking
  - Manual creation via memory tool prompting
  - Natural language check-ins (not robotic "following up on X")
  - Configurable delay (default 1 day)
  - Automatic cleanup of completed follow-ups
  - Priority-based execution
  - Files: `core/agentic_engine.py`

- **Proactive Engagement** - Initiate conversations in idle channels
  - Idle channel detection (configurable threshold: 1-8 hours)
  - Channel context analysis for relevance
  - Success rate tracking per channel
  - Adaptive learning (backs off from low-engagement channels)
  - Three delivery methods: standalone, woven into reply, deferred
  - Per-channel and global daily limits
  - Files: `core/agentic_engine.py`, `core/proactive_action.py`

- **Engagement Tracking** - Analytics and metrics
  - Track proactive message attempts per channel
  - Success rate calculation (reactions, replies, continued conversation)
  - Persistent stats storage (JSON files per channel)
  - Learning window for adaptive behavior
  - Files: `core/engagement_tracker.py`

### Changed
- Engagement detection now "loose" (any user activity counts as engagement)
- Memory maintenance integrated into agentic loop
- Background tasks managed via asyncio

---

## [0.2.0] - 2025-10-04

**Focus:** Intelligent context and memory

### Added
- **Smart Context Building** - Reply chain threading
  - Recursive reply chain traversal (up to 5 levels deep)
  - Temporal context ordering (chronological message flow)
  - Recent message inclusion (last 10 messages)
  - Bot identity awareness (knows its Discord name)
  - Files: `core/context_builder.py`

- **Memory Tool Integration** - Anthropic's official memory tool
  - All 6 commands: view, create, str_replace, insert, delete, rename
  - Client-side execution via MemoryToolExecutor
  - Per-server/channel memory isolation
  - Markdown-based file structure in `memories/{bot_id}/servers/{server_id}/`
  - Files: `core/memory_manager.py`, `core/memory_tool_executor.py`

- **Context Editing** - Prompt caching for token efficiency
  - Automatic activation at 8k input tokens (configurable)
  - Memory tool results preserved (excluded from cache clear)
  - Other tool results cleared on next turn
  - Token usage logging for monitoring
  - Files: `core/reactive_engine.py`, `core/config.py`

- **Temporal Awareness** - Time context for Claude
  - Current timestamp in system prompt
  - Message timestamps in context
  - Enables time-sensitive responses (e.g., "earlier today", "yesterday")
  - Files: `core/context_builder.py`

- **@Mention Resolution** - Readable user references
  - Converts Discord user IDs to display names
  - Prevents Claude seeing raw `<@123456789>` syntax
  - Improves context clarity for the model
  - Files: `core/context_builder.py`

- **User Cache System** - Fast user lookups
  - SQLite-based caching of user data
  - Updated on every message for accuracy
  - Reduces Discord API calls
  - Files: `core/user_cache.py`

### Changed
- Message batching window extended to 10 seconds
- Context building now prioritizes recent activity
- Tool execution moved to dedicated executor class

---

## [0.1.0] - 2025-09-30

**Focus:** Core framework foundation

### Added
- **Core Message Handling** - Discord.py integration
  - @mention detection and response
  - Extended thinking integration (step-by-step reasoning)
  - Message content intent enabled
  - Multi-server support
  - Files: `core/discord_client.py`, `core/reactive_engine.py`

- **Rate Limiting** - Per-channel limits
  - Short window: 20 messages per 5 minutes
  - Long window: 200 messages per 60 minutes
  - Engagement-aware backoff
  - Preserved algorithm from v1 prototype (battle-tested)
  - Files: `core/rate_limiter.py`

- **SQLite Message Storage** - Persistent message history
  - aiosqlite for async operations
  - Message and user caching
  - Foundation for future FTS5 full-text search
  - Files: `core/message_memory.py`

- **Multi-Bot Support** - YAML-based configuration
  - Per-bot config files in `bots/` directory
  - Per-bot memory isolation
  - Per-bot databases in `persistence/`
  - Concurrent bot execution support
  - Files: `core/config.py`

- **Bot Manager CLI** - Simple process management
  - `spawn <bot_id>` command to start bot
  - Graceful shutdown on SIGTERM/SIGINT
  - Environment variable loading from .env
  - Files: `bot_manager.py`

- **Extended Thinking** - Claude's step-by-step reasoning
  - Configurable thinking budget (default 10k tokens)
  - Exposed in API responses for transparency
  - Improves response quality for complex questions
  - Files: `core/reactive_engine.py`

### Technical Details
- **Requirements:**
  - Python 3.10+
  - discord.py 2.3+
  - anthropic SDK
  - aiosqlite for async database
  - PyYAML for config parsing

- **Project Structure:**
  - `core/` - Framework components
  - `tools/` - Tool implementations
  - `bots/` - Bot configurations
  - `memories/` - Memory tool storage
  - `persistence/` - SQLite databases
  - `logs/` - Bot logs

---

## Version History

- **v0.4.0-beta** (2025-10-20) - Tools & polish, closed beta
- **v0.3.0** (2025-10-04) - Autonomous agentic behaviors
- **v0.2.0** (2025-10-04) - Intelligent context and memory
- **v0.1.0** (2025-09-30) - Initial framework foundation
