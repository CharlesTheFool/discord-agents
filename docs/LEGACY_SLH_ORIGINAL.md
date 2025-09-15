Legacy Bot: slh_original.py Overview

This document explains how the single‑file bot works, where to find key logic, and what to change to add features safely.

Note: Some core classes have been extracted to the modular workspace under `SLH/core/` and `SLH/bot/`.

Code Map (jump points)

- Config/env: `SLH/core/config.py`
- State persistence: `SLH/core/state.py`
- System prompt template: `legacy/slh_original.py:105`
- API throttler: `SLH/core/throttle.py`
- Rate limiter: `SLH/core/rate_limiter.py`
- Bot class: `legacy/slh_original.py:242`
- System prompt builder: `legacy/slh_original.py:320`
- Platform limits (image): `legacy/slh_original.py:360`
- Image processing: `legacy/slh_original.py:735`
- Text file processing: `legacy/slh_original.py:780`
- on_ready lifecycle: `legacy/slh_original.py:867`
- Startup history load: `legacy/slh_original.py:893`
- Periodic analyzer (30s): `legacy/slh_original.py:1066`
- on_message ingestion: `legacy/slh_original.py:1236`
- Typing detection: `legacy/slh_original.py:1275`
- Conversation analysis: `legacy/slh_original.py:1323`
- Response execution: `legacy/slh_original.py:1621`
- Urgent mentions: `legacy/slh_original.py:1858`
- Provocation generation: `legacy/slh_original.py:1986`
- Scheduled provocations (1.5h): `legacy/slh_original.py:2064`
- Web search tool call: `legacy/slh_original.py:2150`
- Entrypoint: `legacy/slh_original.py:2334`

High‑Level Flow

- Startup: `on_ready` loads recent history into memory, prints guild/channel stats, loads optional context summaries, analyzes warm conversations, and starts two tasks: periodic checks (30s) and provocations (1.5h).
- Ingestion: `on_message` stores messages in a per‑channel memory buffer and flags channels for analysis. Direct mentions are handled immediately (`handle_urgent_mention`).
- Periodic analysis: `check_conversations` gathers new messages since last check, applies cooldowns/typing‑status, and calls `analyze_conversation_chunk` to decide if/what to say.
- Decision: `analyze_conversation_chunk` filters users on cooldown, formats new messages chronologically, processes all attachments (images and code/text files), and produces a response plan (including potential web search).
- Execution: `execute_response_plan` simulates typing delays, sends text and attachments, applies per‑user/channel cooldowns, and spawns engagement tracking.
- Proactive: `scheduled_provocation` periodically posts one contextual “provocation” per eligible server/channel, capped by a daily budget.
- Web search: `analyze_with_web_search` lets Claude auto‑invoke `WEB_TOOL` and merges all returned text blocks into a reply with citations.

Key Concepts

- Memory: Recent per‑channel message cache for context. Populated at startup and during runtime. Used for both reactive replies and provocations.
- Cooldowns: 
  - Per‑channel cooldowns after bot responds, tiered by number of messages sent.
  - Per‑user cooldowns to prevent over‑engagement with a single user.
- Typing‑aware: Defers responses briefly when humans are typing to feel natural.
- Engagement tracking: Reactions/replies reduce ignore counts via the rate limiter.
- Budgets: Daily limits for web search calls and provocations.
- State: Persisted via `StateStore` (`SLH/.state.json` by default), with defensive load/save and tmp‑replace writes.

External Integrations

- Discord: `discord.py` events (`on_ready`, `on_message`, `on_reaction_add`, `on_typing`) and tasks (`@tasks.loop`).
- Anthropic: `AsyncAnthropic` messages API for conversation, provocation, and web search tool use.
- Images: PIL used to compress/resize aggressively to respect provider limits (with base64 overhead considered).
- Context Manager: Optional deep context via `ContextManager` used when available (`SLH/context_manager.py`).

Where To Modify Common Behaviors

- Tweak personality/prompt: `legacy/slh_original.py:105` and `legacy/slh_original.py:320`.
- Adjust web‑search behavior: `legacy/slh_original.py:2150` (budgeting, logging, concatenation of text blocks).
- Change response frequency (channel cooldowns): `legacy/slh_original.py:242` for config wiring; defaults in env (`CHANNEL_COOLDOWN_*`).
- Per‑user cooldown: `legacy/slh_original.py:1323` logic and `PER_USER_COOLDOWN_SECONDS` in env.
- Attachment handling:
  - Images: `legacy/slh_original.py:735` and supporting compression strategies.
  - Text/code files: `legacy/slh_original.py:780` (size caps, encoding fallbacks, truncation).
- Provocations (when/where/how):
  - Selection: `_find_engagement_target` near `legacy/slh_original.py:1911`.
  - Content: `_generate_provocation` at `legacy/slh_original.py:1986`.
  - Scheduler cadence/daily caps: `legacy/slh_original.py:2064` and env.

Operational Notes

- Required env: see `.env.example` at repo root.
- Permissions: Ensure the bot can read history, send messages, and attach files.
- Limits: Discord 2000‑char messages are handled; long responses are truncated with ellipsis.
- Safety: All Anthropic calls pass through an `APIThrottler` and a semaphore for concurrency control.

Gotchas

- Timezones: Code normalizes timestamps when needed. Keep comparisons timezone‑aware.
- Base64 overhead: Image compression calculates target raw size using a safety factor to avoid post‑encode surprises.
- Duplicate analysis: `processed_messages` prevents re‑processing during periodic checks and after startup history load.

Quick Start

- Create a `.env` from `.env.example`.
- Run: `python legacy/slh_original.py`.
