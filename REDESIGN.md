# SLH v0.6.0 Redesign — Handoff Document

> Written 2026-06-09 (revised same day after design discussion with Charles).
> Purpose: let a fresh Claude Code instance pick up the redesign with full context.
> CLAUDE.md describes the v0.5.0 *as-built* system; where they conflict, THIS file wins.

## 1. Where the project stands

**Code: ~complete. Validation: ~zero.** All v0.5.0 features (MCP, skills, unified attachments,
data isolation, conversation state) are implemented and wired into ReactiveEngine — no stubs.
Almost nothing was executed end-to-end. The stall was the context-management subsystem becoming
undebuggable, not missing code.

Facts a new session must know:

- Branch `Beta`. Baseline + cleanup commits made 2026-06-09 (all v0.5.0 core modules now
  tracked). Before that, 10 core modules existed only on disk.
- **"Bug #6" is a ghost**: TESTING_PLAN (now in docs/archive/v0.5.0-dev/) blocks Phase 1 tests
  on a bug documented nowhere. Don't chase it — the blocked tests target code being deleted.
- `ANTHRODOCS/` (vendored SDK docs, git submodules) was removed 2026-06-09 — superseded by
  Claude Code's built-in claude-api skill. CLAUDE.md references to it are stale.
- Historical dev docs (bug tracker, test plans, v0.5.0 status, old architecture doc) live in
  `docs/archive/v0.5.0-dev/`. They describe machinery this redesign deletes; read for history
  only.
- Local test instrumentation: stdio discord-mcp configured in `.mcp.json` (gitignored; the bot
  token that sat in it should be ROTATED). The official Claude Code Discord plugin does NOT
  replace it (it's an inbound chat channel, not an automation surface).

## 2. Context architecture: EPISODIC SESSIONS (the core redesign)

### The constraint that shapes everything (Charles, 2026-06-09)

1M context does NOT solve the Discord problem. With the old bot, large rolling contexts caused
**context rot**: re-answering long-settled questions, repetition loops (same jokes, same
references), general confusion — users called it "annoying and confused." That's why the cap
was 100k even when 200k existed. Two structural reasons:

- Multi-user, never-ending, interleaved conversations are NOT Claude's native shape. Claude is
  trained on session-like interactions (Claude.ai, Claude Code) that start small, grow
  linearly, and end.
- A rolling window is *chronically full* — there is no natural moment for compaction, and the
  window being "already curated to recent relevance" makes compaction-on-top incoherent.

### Resolution: stop rolling. Model each channel as a sequence of bounded episodes.

This is the Claude Code session pattern transplanted to Discord: sessions grow linearly,
end at natural boundaries, distill knowledge to durable memory, and the next session
*agentically recontextualizes* from a small seed.

**Per-channel lifecycle — distill and reseed:**

1. **Active session**: linear append (user messages, assistant turns, tool activity). Grows
   like a normal Claude session. Stays small most of the time.
2. **Boundary triggers** (any of):
   - channel idle > T (e.g. 4–6h — Discord has natural lulls; tune empirically),
   - session input tokens > threshold (~60–80k) — read from `response.usage.input_tokens`
     (+ cache fields) on every reply; **no count_tokens API calls anywhere**,
   - optional scheduled reset during quiet hours.
3. **Distillation at boundary**: one LLM call (Haiku-class; Batches-eligible) produces TWO
   outputs, both inside the existing memory tree (so the memory tool can read them with zero
   new access plumbing):
   - **An episode file** — episodes are first-class, model-visible artifacts, NOT code
     abstractions. `memories/{bot}/servers/{server}/channels/{channel}/episodes/
     2026-06-09_1430_<model-generated-slug>.md`, with header metadata: start/end timestamps,
     Discord **message-ID range** (snowflakes — stable, sortable, jump-linkable, and joinable
     against the FTS5 DB; do NOT use array indices, which were the v0.5.0 bug factory),
     participants. Body: what happened, what was settled, what stayed open, artifacts
     (attachment IDs).
   - **The channel state file** (`channels/{channel}.md`) — rolling CURRENT state: standing
     facts, **settled-questions ledger** (anti-re-answering), **used-jokes ledger**
     (anti-repetition), open threads, plus a **chronological episode index** (one line per
     episode: date, title, message-ID range, one-sentence hook).
4. **New session seed**: system prompt + channel state file (including the tail of the episode
   index, last ~10 entries) + last ~10–20 messages + an explicit "you are rejoining an ongoing
   channel; if a reference is unclear, open the relevant episode file, search history, or
   load attachments instead of guessing" instruction. The model pulls more context on demand
   (memory `view` on episode files → FTS5 search by message-ID range → get_attachment) —
   recontextualization is the agent's job, not the harness's.
5. **Episode hygiene**: the index grows forever; only its tail enters the seed. Periodically
   (quiet hours / monthly) roll old episodes up into a digest file. Cheap, deferrable.

**Boundaries are timeline-derived, not runtime-derived (offline/crash resistance):**

An episode boundary is a property of the channel's *message timeline* — an idle gap > T
between consecutive messages, or accumulated span mass — computable from the message store
(FTS5/SQLite, populated by the existing startup backfill) at ANY time. It is never a property
of the bot process being up. Consequences:

- **Per-channel watermark**: persist `last_episodized_message_id` (snowflake) in SQLite.
  The "open span" = watermark → now. Episodization = "find boundaries in the open span,
  distill each closed segment in order, advance the watermark" — one function, three modes:
  1. **Live**: boundary observed while online → distill, advance watermark.
  2. **Catch-up on startup**: backfill fills the message store (already exists); run the same
     segmentation over the open span; distill each missed episode (possibly several); then
     seed the current session. A crash or kill mid-conversation just leaves the in-flight
     segment in the open span — it becomes an episode at next startup. Nothing is lost except
     un-persisted in-context working state, which the trust-the-agent decision already
     writes off.
  3. **Retroactive on server join**: same machinery with the watermark initialized deep in
     history. Policy knobs: depth (e.g. 30 days) and granularity — pre-join history should get
     COARSE era digests (weekly/monthly), not fine episodes; run via Batches API (50% cost,
     latency-irrelevant). Reasonable default: skip fine pre-join episodization entirely —
     FTS5 search + culture.md cover "before my time"; the ledgers (settled questions, used
     jokes) only matter for the bot's OWN participation anyway.
- **Idempotency**: episode filename is keyed by the range-start message ID; distilling a fixed
  ID range is safe to re-run (overwrite). Advance the watermark in the same transaction as
  recording the episode; a partial failure re-runs and overwrites.
- **Segmentation token estimates**: the live threshold trigger reads `response.usage`, but
  catch-up segmentation can't — estimate span mass from stored message lengths (chars/4).
  Rough is fine: it only picks boundaries; nothing API-facing depends on it. Idle-gap
  boundaries dominate in practice anyway.

**Asymmetric context (chat small, work big) — turn-scoped working memory:**
Within a single turn's tool loop (code execution, big tool results, multi-step agentic work),
context may balloon freely. At **turn end**, persist the final response and stub out heavy
tool_result blocks older than the last K turns (keep one-line stubs: "ran X, concluded Y").
This is deterministic, client-owned, happens only at turn boundaries — the old system's fatal
flaw was *mid-flight reconciliation between two owners* (client mirror vs server clearing).
One owner, one boundary, no sync.

**Why no server-side compaction for the chat transcript:** distill-and-reseed IS the
compaction, but the summary lands in *memory files* — durable across sessions, cross-channel
queryable, and **human-readable** (you can open the file and see exactly what the bot carried
over; compaction blocks are opaque). Debuggability was the whole failure mode; inspectability
is the cure. Server compaction (`compact-2026-01-12`) remains an option for a marathon single
session, but don't build on it initially.

**Tool-result persistence: dropped as a goal (decided 2026-06-09).** Trust the agent to
re-call tools when context was stripped. This is safe because artifacts are durable: an xlsx
someone posted lives in attachment storage — if the analysis got stubbed out and someone asks
a follow-up, the bot re-reads the file. The only lossy case is non-reproducible reads
(volatile external state at time T); mitigation is the distillation ledger + prompting the bot
to write important findings to memory. The `exclude_tools: ["memory"]` hack is obsolete.

### What gets deleted (unchanged from first draft)

| Component | Action |
|---|---|
| `conversation_state.py` token counting, 60s cache, `_convert_messages_for_token_counting` | Delete (usage comes from `response.usage`) |
| `document_references` + `context_items` dual tracking | Delete both |
| 3× token-overflow loops in `reactive_engine.py` (~835, ~1407, ~2054) | Delete |
| `strip_documents_from_oldest/current`, `remove_oldest_message` + reindexing | Delete |
| `sync_with_server_clearing()` + `clear_tool_uses` server-edit config + `context-management-2025-06-27` header | Delete |
| `enforce_message_cap` multi-threshold logic | Replaced by session boundaries + seed size |
| ConversationState | Shrinks to: message list, attachment-id annotations, active_skills, session metadata (started_at, usage watermark), persistence |
| UnifiedAttachmentManager | Keep storage/classification/Files API; fix duplicate-upload race (~lines 219–242); simplify expiration handling |

Keep as-is: dual-engine split, rate limiter, data isolation, memory tool executor, FTS5
message memory, attachment DB/local storage/Files API client.

## 3. API modernization checklist

| Current | Change to |
|---|---|
| `claude-sonnet-4-5-20250929` (alpha/beta yaml, config default) | `claude-sonnet-4-6` (alias, no date suffix) |
| `claude-sonnet-4-20250514` (slh-01.yaml) | DONE 2026-06-09 → `claude-sonnet-4-6` (was retiring 2026-06-15) |
| `thinking: {enabled, budget_tokens}` | `thinking: {type: "adaptive"}`; expose `output_config: {effort}` (Sonnet 4.6 defaults `high`; chat wants `low`/`medium` — this is the main cost dial) |
| `prompt-caching-2024-07-31` beta header | Remove (long GA) |
| `code-execution-2025-08-25` | GA type `code_execution_20260120`, no beta header |
| `web-fetch-2025-09-10` | `web_search_20260209` / `web_fetch_20260209` (built-in dynamic filtering) |
| `files-api-2025-04-14`, `skills-2025-10-02`, `memory_20250818` | Unchanged, still current |
| Timestamp baked into system prompt (ContextBuilder) | Move to mid-conversation system message (beta `mid-conversation-system-2026-04-07`) or end of messages — currently kills the prompt cache every request |

Adopt opportunistically: structured outputs (`output_config.format`) for AgenticEngine
decisions; Batches API for hourly agentic evaluations and session distillation (50% cost,
latency-tolerant); tool search tool if MCP tool count grows; programmatic tool calling for
bulky MCP outputs.

Note on memory: `memory_20250818` is an API-*defined* but client-*executed* tool — there is no
server-side memory in the Messages API, so MemoryToolExecutor stays. The SDK's
`BetaAbstractMemoryTool` is only a scaffold for the same commands and mainly pays off with the
SDK tool runner; with our manual loop it's optional/low value. Skip.

## 4. Housekeeping log

Done 2026-06-09:
- `.gitignore`: added `.mcp.json`, `mcp_servers.json`, `*.stackdump`, `.skills_cache.json`,
  `Test-MCP-Servers/`, `test_files/`. Deleted `bash.exe.stackdump`.
- slh-01.yaml model swap (above).
- Committed v0.5.0 baseline on `Beta` (all core modules now tracked), then cleanup commit:
  removed ANTHRODOCS submodules + `.gitmodules`, deleted `core/multimedia_processor.py` (dead)
  and `generate_test_files.py`, archived stale dev docs to `docs/archive/v0.5.0-dev/`.

Remaining (cheap):
- [ ] Rotate the Discord bot token that sat in `.mcp.json` pre-gitignore.
- [ ] Decide fate of `skills/canvas-design/` (4.2MB fonts; deliberately left untracked).
- [ ] README.md + CHANGELOG.md still describe v0.4.x — update as part of v0.6.0 release, not
      before (don't document architecture that's about to change).
- [ ] Rewrite CLAUDE.md after the refactor (it documents the deleted budget system and
      ANTHRODOCS; it is local-only/gitignored).

## 5. Implementation plan — phased, each phase one commit

**Phase 1 — API modernization on the EXISTING architecture.**
Small diff to validate the environment before surgery: model strings → `claude-sonnet-4-6`,
adaptive thinking, drop stale beta headers, new web tool versions, expose `effort` in config.
Smoke-test: spawn alpha on the test server, @mention it, confirm reply + logs clean.

**Phase 2 — Demolition.**
Delete everything in the §2 table. Keep a temporary hard message cap as the only guard so the
bot stays runnable. Delete the unit tests that test deleted behavior (do NOT "fix" them).
Smoke-test again: bot must still converse (context just grows for now).

**Phase 3 — Episodic sessions.**
- Session metadata on ConversationState (started_at, last_activity, usage watermark from
  `response.usage`).
- Per-channel `last_episodized_message_id` watermark (SQLite) + timeline segmentation
  function (idle gaps + estimated span mass) — the single episodization code path.
- Boundary detection: idle timer (check in the existing hourly/periodic loops) + usage
  threshold + optional quiet-hours reset → all funnel into the same segmentation function.
- Startup catch-up pass: after backfill, episodize the open span before seeding the session.
- (Deferrable) retroactive-on-join knob: depth + granularity, Batches-backed era digests.
- Distillation: Haiku-class call → episode file (timestamped, slug-titled, message-ID range)
  + channel state file update (ledgers + episode index). Both under memories/ (§2.3).
- Seed builder: channel state + episode-index tail + last N messages + recontextualization
  instructions in system prompt.
- Turn-end tool-result stubbing (keep last K turns full).

**Phase 4 — Retrieval path, end to end (the never-tested multimedia).**
Verify with the local discord-mcp: post image/pdf/xlsx → bot processes; later session
references it → bot retrieves via get_attachment/code-exec and answers. Fix the
duplicate-upload race. Slim the uploaded-files manifest to an index (IDs + one-liners), not
pinned content.

**Phase 5 — Tune & extend.**
Effort/cost tuning per engine; structured outputs in AgenticEngine; Batches for hourly loop
and distillation; repetition/settled-question behavior checks against the anti-rot ledgers
(this is the acceptance test for the whole redesign: does the bot stop re-answering and
re-joking?).

Testing discipline (from CLAUDE.md, still applies): kill bot processes after tests, delete
logs between runs, use discord-mcp + log cross-referencing, keep a living test plan.
