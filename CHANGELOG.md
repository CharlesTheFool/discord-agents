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

**Tools & Integrations**
- **Discord Message Search** - Full-text search across conversation history with multiple viewing modes (recent, around message, first messages, date range)
- **Automatic Message Reindexing** - Daily reindex at 3 AM UTC keeps search current, with manual `@bot reindex` command support
- **Image Processing** - Automatic compression for up to 5 images per message with smart fallback strategies to stay within API token limits
- **Web Search Integration** - Anthropic server tools for web search and fetching with automatic citation extraction and daily quota management (300 searches default)

**Configuration & Deployment**
- **Config Validation** - Startup validation checks for missing environment variables and invalid configuration values
- **Template Files** - Pre-configured examples (`.env.example`, `alpha.yaml.example`) for easy setup
- **Export/Import Tool** - Portable zip-based backups for syncing bot data across machines without git complexity

### Fixed
- **Context Builder Race Condition** - Bot no longer sends multiple combined responses when processing rapid messages
- **Edited Message Search** - Fixed bug where edited messages weren't searchable in message history
- **Forwarded Messages** - Bot now handles forwarded messages gracefully without exceptions
- **Discord Tools Errors** - Silent failures in Discord search/view tools now log warnings properly
- **Web Search Quota Tracking** - Fixed incorrect tracking of server tool usage for web search quota management

### Changed
- **Documentation Structure** - Consolidated into ARCHITECTURE.md (technical reference) and CHANGELOG.md (version history)
- **Simplified Configuration** - Streamlined config loading removes deployment submodule complexity
- **Version Numbering** - Switched to semantic versioning (0.x.0) instead of phase-based naming
- **Error Handling** - Config validation now returns detailed error list instead of raising exceptions

### Security
- **API Key Isolation** - All sensitive credentials managed via environment variables only
- **Rate Limiting** - Per-channel limits (20 messages per 5 min, 100 per hour) prevent spam and quota exhaustion
- **Quota Management** - Web search daily limits with tracking to control API costs
- **Memory Isolation** - Per-server and per-channel memory prevents data leakage between communities
- **Startup Validation** - Environment variable validation catches missing credentials before bot starts

---

## [0.3.0] - 2025-10-04

**Focus:** Autonomous agentic behaviors

### Added
- **Autonomous Background Loop** - Bot runs hourly checks for follow-ups and proactive engagement opportunities with configurable quiet hours support
- **Follow-Up System** - Bot remembers to check in on users after events (e.g., "how did that presentation go?") with natural conversational language
- **Proactive Engagement** - Bot initiates conversations in idle channels based on context analysis and learns from success rates to improve over time
- **Engagement Analytics** - Tracks proactive message performance per channel with adaptive learning that reduces attempts in low-engagement areas

### Changed
- **Engagement Detection** - Any user activity now counts as engagement (more lenient tracking)
- **Memory Maintenance** - Integrated into autonomous loop for automatic cleanup
- **Background Processing** - All autonomous tasks managed via asyncio for non-blocking execution

---

## [0.2.0] - 2025-10-04

**Focus:** Intelligent context and memory

### Added
- **Smart Context Building** - Bot follows reply chains up to 5 levels deep and includes recent messages for natural conversation flow
- **Persistent Memory** - Anthropic's official memory tool with full CRUD operations (create, view, edit, delete) stored per-server in Markdown files
- **Prompt Caching** - Automatic activation at 8k tokens reduces API costs while preserving memory tool results
- **Temporal Awareness** - Bot understands time context and can reference "earlier today", "yesterday", etc. in responses
- **@Mention Resolution** - Converts Discord user IDs to readable names so bot understands who's being referenced
- **User Cache** - SQLite-based caching reduces Discord API calls and improves response time

### Changed
- **Message Batching** - Extended to 10 seconds to better group rapid-fire messages
- **Context Priority** - Recent activity now prioritized in conversation context
- **Tool Execution** - Dedicated executor class for cleaner separation of concerns

---

## [0.1.0] - 2025-09-30

**Focus:** Core framework foundation

### Added
- **Discord Integration** - @mention detection, response handling, and multi-server support with message content intent
- **Rate Limiting** - Per-channel limits (20 per 5 min, 200 per hour) with engagement-aware backoff to prevent spam
- **Message Storage** - SQLite-based persistent message history with async operations for non-blocking database access
- **Multi-Bot Support** - YAML configuration system allows running multiple isolated bots with separate databases and memory
- **Bot Manager CLI** - Simple `spawn <bot_id>` command with graceful shutdown and environment variable loading
- **Extended Thinking** - Claude's step-by-step reasoning for complex questions (configurable 10k token budget)

### Requirements
- Python 3.10+
- discord.py 2.3+
- anthropic SDK
- aiosqlite
- PyYAML

---

## Version History

- **v0.4.0-beta** (2025-10-20) - Tools & polish, closed beta
- **v0.3.0** (2025-10-04) - Autonomous agentic behaviors
- **v0.2.0** (2025-10-04) - Intelligent context and memory
- **v0.1.0** (2025-09-30) - Initial framework foundation
