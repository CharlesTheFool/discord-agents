# Changelog

All notable changes to Discord Agents will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased] - 0.9.0 (bot core; supervisor + app to follow)

### Added

**DM support proper (the Prime's surface)**
- A persistent `dm_channels` registry (users.db) - Discord can't enumerate
  DM channels after a restart, so the bot keeps its own memory of who it
  talks to privately; upserted on every DM in and out
- DM memory lives in the global tree: `global/dms/{user_id}/` (notes +
  episodes) - a DM belongs to the person, not to any server; episodization
  and channel-state seeding work unchanged
- DM memory trees are mechanically private: visible and writable only from
  their own conversation, invisible from servers and other DMs, enforced
  before any vault logic and with zero vaults configured
- DMs know where they stand: the volatile tail carries `<prime_context>` -
  the same mind, above all its servers, naming the places it lives

**/memory commands (consent through the DM vault)**
- `/memory show` - your global profile, verbatim (no model call)
- `/memory remember|forget|feedback <text>` - injects a structured intent
  into the DM conversation and runs the normal pipeline with a one-shot
  grant opening exactly your own profile file for that turn; writes carry
  the private origin tag (shapes behavior everywhere, cited nowhere)
- Registered globally, DM-only contexts; first slash-command surface

**Cross-server coordination (ask_prime + standing watches)**
- `ask_prime` tool (server contexts only): a particular asks the Prime to
  pose a question or announce something in another server. Mechanical
  gates run before any model spend - vault boundaries both directions,
  daily budgets (5/channel, 12/server), DM refusal; then one bounded
  judgment call that can refuse with a relayable reason
- Approved sends are delivered by the target particular in its own voice,
  honoring quiet hours and proactive rate limits
- `watch_for_response: true` registers a standing watch
  (`global/watches.json`, max 10 concurrent, 24h expiry): the hourly loop
  evaluates new messages with one cheap Haiku call; an answer is injected
  into the asking channel as a durable "relayed via Prime, from {server}"
  note and delivered there; expiry injects an honest no-answer note

**Events substrate (what the supervisor dashboard will read)**
- An `events` table in messages.db: one structured row per bot turn-event
  (kind, server, channel, payload with triggers/thinking/tool calls/
  response/tokens/provenance), auto-created on first run
- Every action kind writes: mention, dm, scan, **silent** (considered and
  stayed quiet is now first-class), proactive (sent and "no opening
  taken"), followup, memory, relay, watch, skill, reseed (with
  ceiling/idle reason and episode title)
- Conversations log finally shows discord/repository/MCP/skill tool calls
  (the 0.8 stress-campaign observability gap)

**Supervisor daemon**
- `python supervisor.py [--root <path>] [--port <n>]` - process management
  (start/stop/restart, crash recovery with exponential backoff capped at
  5/hour) + a localhost-only HTTP API on 127.0.0.1:8642
- Full dashboard surface: fleet list, rich per-bot status (engines,
  live-context fill, tokens-today from the events table, DMs, follow-ups,
  watches, vaults, engagement readout), stats, memory tree
  (read AND write - editable memories), repository tree (read-only),
  log tails with SSE follow, channel navigation (servers → channels +
  the DM rail), and the channel stream (messages ⋈ turn-events,
  cursor-paginated)
- Integrations: skills catalog with toggles (= membership in
  `skills.default_skills`), add-skill upload, MCP server management
  (mcp_servers.json) with a 60s health poller (connected/error/
  connecting/disabled + latency + discovered tools) and live reconnect
- Config as data: GET returns the YAML; PUT validates through the same
  `Config.validate()` the bot boots with and rejects invalid writes
- Commission/retire: create bots from a template (id validated); delete
  refuses while running and moves everything to `trash/`, never deletes
- Every file route is path-jailed; `.env` and tokens are unreachable by
  construction
- A `channel_names` cache table lets the dashboard label channels while
  bots are asleep

**Dashboard + desktop application**
- The supervisor serves the operator dashboard at `/`: fleet board
  (status chips, model tags, 7-day volume, context/follow-up/DM signals,
  start/stop/restart, commission/retire), and per-bot pages - Monitor
  (engine gauges, live-context fill, statline, commitments band, the
  channel monitor with the bot's x-ray stream and a Direct Messages
  rail), Configure (the YAML as a generated form; saves surface the
  daemon's validation verdict), Integrations (skill toggles, add-skill,
  MCP management with live health), Memories (browse AND edit), and
  Repository (browse)
- Electron shell (`app/`): attaches to a running daemon or spawns one,
  opens the dashboard in a window; packaged as a Windows NSIS installer

### Notes
- No new config keys: DMs and /memory ship enabled (core surface, not a
  feature flag); Prime caps are internal constants

---

## [0.8.0] - 2026-06-10

**Status:** Pre-release (beta). Ships together with 0.7.0 below (one tag —
0.7.0 was never released standalone).

### Added

**Server Induction**
- `python bot_manager.py induct <bot_id> --server <id> [--dry-run]
  [--channels id,id,...] [--force-full]` distills a backfilled server's
  stored backlog into era digests, channel notes, lean global user profiles,
  and server culture - explicitly framed as archaeology ("observations, not
  lived memory"), never first-person history
- `--dry-run` prints a per-channel message/token table with a batch-rate
  cost estimate and writes nothing
- Incremental by default (re-runs only process messages past the episode
  watermark); `--force-full` reprocesses everything; a failed channel writes
  no partial files and keeps its watermark
- Refuses to run while the bot is live (watermark races)

**Threads**
- Threads are first-class surfaces: a persistent thread registry maps each
  thread to its parent channel; memory nests under
  `channels/{parent}/threads/{thread_id}` (a thread is part of its parent
  place)
- Vault inheritance: a thread inside a vaulted parent is inside the vault -
  search exclusion, memory sealing, and content gates all expand to thread
  ids
- Backfill covers active and archived threads; thread deletion purges
  stored messages, attachments, and the registry row
- Context marks the surface: "thread of #parent"

**Voice-channel text**
- Voice channels' text chats are watched, backfilled, and remembered like
  any text channel; context marks them "voice channel text chat"

**DM privacy**
- DMs are mechanically private: DM messages and attachments are visible
  only from inside their own DM - excluded from search, viewing, and
  attachment access everywhere else
- DMs answer on the urgent path (a DM is inherently addressed to the bot)
- The bot knows the room: DM context is marked "private, one-on-one", a
  standing prompt section explains the privacy walls in both directions,
  and DM conversations carry the partner's global profile

### Changed
- Proactive engagement skips threads (reactive-only there; internal
  constant, revisit on demand)
- The 0.7 per-server profile read shim is removed - memory context always
  lists the global profile path

## [0.7.0] - 2026-06-10

**Status:** Pre-release (beta), bundled in the v0.8.0 tag.

### Added

**Unified Identity**
- People are global: user profiles move to one per-human file
  (`memories/{bot_id}/global/users/{user_id}.md`, keyed by Discord user ID)
  with `Known from:` headers and per-claim origin tags; existing per-server
  profiles merge automatically on the consolidator's first run
- Vaults: a single `vaults: []` config key marks channels/servers whose
  content never leaves them - excluded from outside search and attachment
  access, memory files sealed, repository saves blocked from vaulted
  channels; inside a vault the bot is fully itself
- Tool scopes: message search defaults to the current server and widens to
  `global` on request; cross-server results carry `[from {server}]` origin
  labels; attachment and repository listings scope the same way
- Discretion norms: a mandatory prompt section turns provenance into
  decorum - cross-server familiarity is acknowledged, other people's
  business stays where it was learned

**Memory Reconsolidation**
- Weekly per-server background pass (Batches API, 50% token cost, separate
  rate limits): episode compaction into era digests, profile rewrites
  re-derived from evidence, provenance repair, monthly channel/culture
  refresh
- Every rewritten file's prior version is archived to a `.history/` sibling
  (last 3 kept) - one bad pass is always recoverable
- `api.consolidation_model` config (Sonnet default)
- `python bot_manager.py consolidate <bot_id> --server <id> [--force]` for
  manual/debug runs

### Changed
- `data_isolation` config removed (deprecation warning; key ignored) -
  vaults replace the scope modes
- `!timezone` now writes to the global profile (keyed by user ID) and works
  in DMs

### Removed
- `core/data_isolation.py` and the scope-mode machinery

## [0.6.1] - 2026-06-10

**Status:** Pre-release (beta).

### Added

**Bot File Repository**
- Persistent per-server local drive at `repository/{bot_id}/{server_id}/` —
  drop arbitrary files in by hand and the bot sees them; everything survives
  restarts
- New `repository` tool: `save_file` (author text files), `save_attachment`
  (preserve a Discord upload), `save_output` (pull a code-execution artifact
  in by file_id), `delete`, `rename`, `list` — every transfer is an explicit
  bot action, nothing is harvested automatically
- Repository contents ride a `<repository>` section in the volatile context
  tail (cache-safe), with in-context/not-in-context markers; reads reuse the
  existing `get_attachment` retrieval path (documents re-attach, images
  inline, spreadsheets mount into code execution)
- Disk is the source of truth: a per-request scan picks up user adds, edits,
  deletes, and renames; bot-side renames and content edits keep a stable
  attachment identity
- Config: `attachments.repository.enabled` (default on when attachments are
  enabled)

## [0.6.0] - 2026-06-10

**Status:** Pre-release (beta). The 0.5.0 feature set plus a memory-architecture
redesign, validated end-to-end through live scenario testing on a real Discord
server (six narrative scenarios, a full code audit, and a release stress test —
54 bugs and findings fixed along the way).

### Added

**Episodic Session Memory** (replaces client-side token budgeting)
- Conversation sessions are distilled into per-channel episode files (titled,
  timestamped, message-ranged) when a session crosses its token threshold or
  goes idle — then the live context reseeds fresh
- Episode archives are readable by the bot through its memory tool; distilled
  channel state (standing facts, settled questions, running jokes) feeds
  proactive decisions
- Startup catch-up distills anything missed while offline; failed distillations
  retry with a cooldown instead of re-billing every message

**Retrieval**
- `get_attachment` tool: the bot can pull any indexed attachment back into
  context on demand (documents re-attach, images inline, spreadsheets mount
  into code execution)
- Attachment index in the system prompt: recent channel files with
  in-context/not-in-context markers, so the bot knows what it can retrieve
- Full-text message search now handles multi-keyword queries (per-token AND
  with literal-phrase fallback)

**Prompt-Cache Architecture**
- Full-conversation caching: message-level cache breakpoints plus a
  volatile-context tail keep the entire prompt prefix byte-stable across turns
  — measured live at ~330 uncached tokens per turn versus ~3,000 before
- Persisted images stored by reference in a content-addressed blob store
  (states no longer re-serialize megabytes of base64 on every save)
- Cache-anatomy logging: input tokens split into uncached / cache-read /
  cache-write

**Autonomy**
- Engagement settlement: proactive messages are judged after a delay (replies
  count as success), so channel success rates actually learn — previously
  every channel death-spiraled after two unanswered sends
- Proactive evaluations return structured decisions and may explicitly decline
  to send ("nothing worth saying")

### Changed

- **Configuration simplified from ~150 options to ~30.** Rate limiting and
  proactive intensity are now presets (`strict/moderate/permissive/unlimited`,
  `gentle/moderate/active`); implementation details moved to internal
  constants. Old keys log deprecation warnings and map where possible.
- Both response paths (@mention and periodic check) now share one
  request/tool-loop/delivery pipeline
- Web search cost control is per-request (`max_uses`) — the daily quota
  system is gone
- API layer modernized: adaptive thinking, `effort` parameter (validated
  against model capability at startup), beta Messages endpoint throughout
- Message deletions are handled via raw gateway events, so storage and
  attachment purges fire even for messages older than the current process
- Backfill always runs in the background; `backfill_days: 0` now means
  unlimited

### Fixed

Fifty-four bugs and audit findings across three hardening passes, including:

- Server tool result blocks persisted as repr strings poisoned conversation
  replay (400 on every subsequent request) — server blocks are no longer
  persisted, and existing databases heal themselves on load
- Conversation states could start with a non-user turn after cap enforcement
  or DB seeding (channel-bricking 400) — invariants enforced at every
  mutation point, poisoned states self-heal
- Expired Files API references in persisted state bricked their channel; they
  now recover in place (re-upload from local storage, id swapped, request
  retried)
- The agentic engine: infinite proactive loop on `max_tokens` stops, naive
  datetime crash, hours-vs-minutes idle confusion, engagement success never
  recorded, effort sent to models that reject it
- MCP tools from underscore-named servers could never route back
- Skills upload blocked the Discord gateway heartbeat at startup (sync client
  in async path); skills cache now reconciles against the server
- Windows cp1252 encoding crashes in memory files, deployment tool, and logs
- Deleted Discord messages now purge their attachments (local copy, Files API
  upload, database rows)

### Upgrading from 0.4.x

No manual data migration: databases and persisted conversation states migrate
themselves on first load (new tables auto-create; legacy rows heal).

Config changes to make in your bot YAML:

- **Removed (now ignored, with deprecation warnings):** `reactive.cooldown`,
  `images:` section, `api.context_management`, `api.context_editing`,
  `api.throttling`, `rate_limiting:` section, `multimedia:` section, per-rate
  personality knobs (`mention_response_rate` etc. — express style in
  `personality.base_prompt` instead)
- **Renamed:** `discord.default_timezone` → `discord.timezone`;
  `api.context_management.max_conversation_messages` → `api.context_messages`;
  `api.context_management.max_total_tokens` → `api.context_tokens`
- **New:** `api.effort` (low/medium/high/max — startup validation rejects it
  on models without effort support), `reactive.check_interval_seconds`,
  `skills.default_skills`
- **Semantics:** `agentic.proactive.quiet_hours` is a list of LOCAL host-clock
  hours (default `[0..6]`); `discord.backfill_days: 0` means unlimited
- `web_search` no longer takes quota settings — enable/disable only

---

## [0.5.0] - 2026-06-10

**Status:** Never released independently — developed after 0.4.1, validated and
shipped as part of 0.6.0. Listed separately so the feature history stays honest.

### Added

- **MCP integration** - Remote MCP servers over HTTP (`mcp_servers.json`),
  tools auto-discovered at startup and namespaced by server; server failures
  degrade gracefully
- **Skills system** - `.zip` skills auto-discovered from `/skills/`, uploaded
  with SHA256 deduplication; Anthropic built-ins (xlsx, pptx, docx, pdf);
  progressive disclosure — the bot requests skills via a `request_skill` tool;
  code execution enabled automatically with skills
- **Unified attachments** - Images, documents, spreadsheets, and code files
  processed through one pipeline (Files API + local storage), with retroactive
  processing of historical attachments and configurable backfill
- **Container file delivery** - Files the bot creates in code execution
  (decks, charts, exports) attach to its Discord reply
- **Persistent conversation state** - Per-channel context survives restarts
  (SQLite-backed)
- **Data isolation modes** - `off` / `server` / `channel` scoping for memory,
  search, and Discord tools in multi-tenant deployments

---

## [0.4.1] - 2025-10-24

**Status:** Pre-release (beta) - polish and refinements

### Added

**Date & Time Awareness**
- **Current Date Display** - System prompt shows current date/time in configured server timezone
- **Knowledge Cutoff** - Optional `knowledge_cutoff_date` configuration displays model's training cutoff date
- **Timezone Tracking System** - `!timezone` / `!tz` commands let users set personal timezones stored in memory profiles
- **Server Timezone** - Configurable default timezone for the server (defaults to UTC, supports IANA format)

**Lifecycle Event Tracking**
- **Online/Offline Events** - System messages logged when bot starts or stops gracefully
- **Crash Detection** - Bot detects ungraceful shutdowns and logs crash events
- **Per-Channel Events** - Lifecycle messages appear in each channel's context for awareness

**Bot Customization**
- **Configurable Status** - Discord activity status now configurable via `discord.status` field
- **Crash Test Mode** - `--crash-test` flag for testing crash detection during development

### Changed
- **Database Schema** - Added `is_system` column for system messages with automatic migration
- **Timezone Handling** - All timestamps normalized to naive UTC for consistency
- **User Memory Files** - Timezone command uses usernames (not user IDs) for readable filenames

### Technical
- **Dependencies** - Added `pytz>=2024.1` for timezone support
- **Backward Compatibility** - All changes backward compatible via auto-migration and sensible defaults

---

## [0.4.0-beta] - 2025-10-20

**Status:** Beta release - framework feature-complete

### Added

**Tools & Integrations**
- **Discord Message Search** - Full-text search across conversation history with multiple viewing modes (recent, around message, first messages, date range)
- **Automatic Message Reindexing** - Daily reindex at 3 AM UTC with manual `@bot reindex` command support
- **Image Processing** - Automatic compression for up to 5 images per message with smart fallback to stay within API token limits
- **Web Search Integration** - Anthropic server tools with automatic citation extraction and daily quota management (300 searches default)

**Configuration & Deployment**
- **Config Validation** - Startup checks for missing environment variables and invalid configuration values
- **Template Files** - Pre-configured examples (`.env.example`, `alpha.yaml.example`) for straightforward setup
- **Export/Import Tool** - Zip-based backups for syncing bot data across machines

### Fixed
- **Context Builder Race Condition** - Eliminated multiple combined responses when processing rapid messages
- **Edited Message Search** - Edited messages now indexed correctly in message history
- **Forwarded Messages** - Bot handles forwarded messages without exceptions
- **Discord Tools Errors** - Silent failures in Discord search/view tools now log warnings
- **Web Search Quota Tracking** - Corrected server tool usage tracking for quota management

### Changed
- **Documentation Structure** - Consolidated into ARCHITECTURE.md (technical reference) and CHANGELOG.md (version history)
- **Simplified Configuration** - Streamlined config loading removes deployment submodule complexity
- **Version Numbering** - Switched to semantic versioning (0.x.0) instead of phase-based naming
- **Error Handling** - Config validation returns detailed error list instead of raising exceptions

### Security
- **API Key Isolation** - All sensitive credentials managed via environment variables
- **Rate Limiting** - Per-channel limits (20 messages per 5 min, 100 per hour) prevent spam and quota exhaustion
- **Quota Management** - Web search daily limits with tracking to control API costs
- **Memory Isolation** - Per-server and per-channel memory prevents data leakage between communities
- **Startup Validation** - Environment variable validation catches missing credentials before bot starts

---

## [0.3.0] - 2025-10-04

**Focus:** Autonomous agentic behaviors

### Added
- **Autonomous Background Loop** - Hourly checks for follow-ups and proactive engagement opportunities with configurable quiet hours
- **Follow-Up System** - Bot remembers to check in on users after events (e.g., "how did that presentation go?")
- **Proactive Engagement** - Bot initiates conversations in idle channels based on context analysis
- **Engagement Analytics** - Tracks proactive message performance per channel with adaptive learning to reduce attempts in low-engagement areas

### Changed
- **Engagement Detection** - Any user activity now counts as engagement
- **Memory Maintenance** - Integrated into autonomous loop for automatic cleanup
- **Background Processing** - All autonomous tasks managed via asyncio for non-blocking execution

---

## [0.2.0] - 2025-10-04

**Focus:** Intelligent context and memory

### Added
- **Smart Context Building** - Bot follows reply chains up to 5 levels deep and includes recent messages for natural conversation flow
- **Persistent Memory** - Anthropic's official memory tool with full CRUD operations stored per-server in Markdown files
- **Prompt Caching** - Automatic activation at 8k tokens reduces API costs while preserving memory tool results
- **Temporal Awareness** - Bot understands time context and can reference "earlier today", "yesterday", etc.
- **@Mention Resolution** - Converts Discord user IDs to readable names
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
- **Rate Limiting** - Per-channel limits (20 per 5 min, 200 per hour) with engagement-aware backoff
- **Message Storage** - SQLite-based persistent message history with async operations
- **Multi-Bot Support** - YAML configuration system for running multiple isolated bots with separate databases and memory
- **Bot Manager CLI** - Simple `spawn <bot_id>` command with graceful shutdown and environment variable loading
- **Extended Thinking** - Claude's step-by-step reasoning for complex questions (configurable 10k token budget)

### Requirements
- Python 3.10+
- discord.py 2.3+
- anthropic SDK
- aiosqlite
- PyYAML
