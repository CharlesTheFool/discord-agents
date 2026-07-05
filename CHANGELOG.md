# Changelog

All notable changes to Discord Agents will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.12.4] - 2026-07-05

**Status:** Pre-release (beta). Default model bump only - no behavioral changes.

### Changed
- **Default chat/consolidation model is now Claude Sonnet 5.** Config
  defaults, the supervisor dashboard's new-bot template, and docs all point
  at `claude-sonnet-5` instead of `claude-sonnet-4-6`. Sonnet 4.6 remains a
  supported, effort-capable model - its registry entries were kept, not
  removed - for anyone still pinned to it. Required adding `sonnet-5` to the
  effort-capable model marker list (`internal_constants.py` and its
  `bot.js` mirror); without it, a bot config with `api.effort` set and the
  model bumped to Sonnet 5 would fail startup validation.

---

## [0.12.3] - 2026-06-12

**Status:** Pre-release (beta). Field-test fixes round two: dashboard
staleness root-caused, skills discovery hardened, tool-loop spiraling
guarded.

### Fixed
- **Dashboard updates actually reach you now.** Root cause of "the dashboard
  still doesn't refresh": the supervisor served UI files with no cache
  headers, so Chromium's heuristic caching could pin a stale `bot.html` -
  and with it the previous version's JavaScript - long after a framework
  update. All non-API responses are now `no-store`. Additionally, the
  Memories and Repository tabs re-fetch every time they're opened (the bot
  writes to both behind the dashboard's back); Monitor keeps its 5s
  auto-refresh, verified live.
- **Skills survive sloppy unzipping.** A skill extracted as
  `skills/foo/foo/SKILL.md` (the classic zip wrapper folder) is now
  resolved one level down with a warning instead of the whole skills
  directory silently scanning as empty. Folders with no SKILL.md anywhere
  are named in the log.
- **The tool loop notices it's going in circles.** Three identical
  consecutive tool calls (same tool, same input) inject a note telling the
  model the call is working, repetition won't help, and the problem is
  elsewhere - instead of letting it burn its whole iteration budget
  re-fetching one file, as observed in the music-video field test.

### Changed
- **The bot knows its sandbox.** Code-execution guidance now states the
  environment is air-gapped (no pip, no curl, no ffmpeg - verified
  empirically) and lists what's actually preinstalled, so the bot stops
  discovering this the hard way mid-task. It's also taught to use the
  repository as its workbench for multi-turn projects: drafts and
  intermediates persist there, container files die with the turn.

---

## [0.12.2] - 2026-06-12

**Status:** Pre-release (beta). Dashboard fix only - no bot runtime changes.

### Fixed
- **The dashboard Monitor tab refreshes itself.** Live status, stats, and
  activity now auto-refresh every 5 seconds while the tab is active, with
  scroll position preserved; refreshing pauses when the tab is hidden or
  another tab is selected (no idle timers or SSE connections held open
  off-tab).

---

## [0.12.1] - 2026-06-12

**Status:** Pre-release (beta). Phantom-attachment hardening, from a second
field occurrence on 0.11.3 (the guard fired and the model doubled down past
it).

### Fixed
- **The bot can now see its own delivery record.** `<recent_messages>`
  includes the bot's own recent messages with Discord's ground truth -
  `[attached a file]` or `[no file attached]` - so a prior message claiming
  a file that never attached is contradicted at perception level, every
  request, instead of being trusted as memory.
- **The phantom-attachment guard escalates instead of giving up.** Two
  bounces with increasingly blunt system notes (the second states plainly
  that zero files exist and points at the delivery record); on a third
  strike the periodic path suppresses the response entirely - silence beats
  a lie - while the must-reply path still answers.
- **`send_message` description states the delivery rule**: writing
  "attached" never attaches anything; files only ride a message through
  `attach_outputs`.

---

## [0.12.0] - 2026-06-12

**Status:** Pre-release (beta). Native messaging, phase 1 (compat mode).

### Added
- **`send_message` tool - the bot's native voice in Discord.** Instead of
  only having its final text captured and posted, the model can now send
  messages as deliberate tool calls, mid-turn. Each call is one message; the
  tool result reports ground truth (sent message id, exactly which files
  attached), which mechanically closes the phantom-attachment failure class -
  the model can no longer sustain a false belief about its own output.
- **Reply anchoring.** `reply_to_message_id` anchors a send as a real Discord
  reply - when a batch of messages needs one specific answer, it lands
  attached to the right message. Targets come from a new `<recent_messages>`
  id map riding the volatile context tail (never cached, never persisted).
  Other bots' messages are valid targets (twins/Prime servers); a deleted
  target degrades to a standalone send and the tool result says so.
- **Multi-message turns.** The tool can be called several times in one turn
  with guaranteed ordering - e.g. an anchored reply to one person chained
  with a separate general message for the rest of the conversation.
- **Attachments by name.** `attach_outputs` attaches files created via code
  execution this turn, resolved by filename against the turn's container
  outputs; misses are reported, never silently invented.

### Changed
- **Compat mode:** plain final text still posts exactly as before - turns
  that don't use the tool behave identically to 0.11.x. Tool-sent turns
  satisfy the must-reply guarantee, count toward rate limits and engagement
  tracking, and are never discarded as stale (they're already out). Strict
  mode (final text stops auto-sending) is deferred to a later phase.

---

## [0.11.3] - 2026-06-12

**Status:** Pre-release (beta). Social-cadence and attachment fixes from the
first live 0.11.x field test.

### Fixed
- **The silence guardrail no longer benches an engaged bot.** Three changes
  to the ignored-streak silencer that sidelined SLH mid-conversation:
  engagement checks now run at escalating intervals (30s/2min/5min) so
  long-form posts get human reading time before being judged ignored; any
  human follow-up message counts as engagement, not just the triggering
  user's; and silence auto-expires after 30 minutes into a single trial
  message instead of muting the channel until the next @mention.
- **Replies to the bot are real-time engagement.** A formal reply to a bot
  message immediately decrements the ignored streak (reactions already did
  this), so explicit engagement can actually un-silence a channel.
- **Phantom attachments bounce back.** When a reply claims an attachment but
  no files are queued for Discord, the turn is returned to the model once
  with a system note - produce the file or reword - instead of shipping a
  false claim it will later trust as truth.
- **Duplicate container outputs deduplicate by content.** The same generated
  file surfacing under two Files API ids (e.g. via save_output plus the
  container sweep) no longer attaches twice to one Discord message.
- **No more empty lines inside messages.** Responses fragment on blank
  lines into separate texting-style Discord messages, mechanically (code
  blocks stay intact; single newlines and lists stay together). Applies to
  reactive replies and proactive/agentic sends.
- **@mention turns retry once on API failure.** A transient API error on the
  must-reply path (like the streaming-timeout error that ate a mention on
  June 11) gets one retry after 3s before apologizing.

### Changed
- **Replies are soft mentions now.** Replying to a bot message no longer
  forces an immediate, rate-limit-bypassing response like a true @mention.
  Instead the reply gets prompt consideration: a scan fires ~10 seconds
  later that may answer or stay silent. Explicit `@bot` text in the reply
  still forces a response.

## [0.11.2] - 2026-06-12

**Status:** Pre-release (beta).

### Fixed
- **Cost today counts cache writes.** Turn events now record
  `cache_creation_input_tokens`, and the estimator bills them at 1.25x input
  (the 5-minute-cache write rate). Previously only uncached input, cache
  reads, and output were counted, silently undercounting every turn.
- **Boot backfill is incremental.** On startup each channel resumes from its
  newest stored message instead of re-fetching the entire configured window
  from Discord. The daily 3 AM re-backfill and the manual reindex command
  still sweep the full window - their job is catching edits.

## [0.11.1] - 2026-06-12

**Status:** Pre-release (beta).

### Fixed
- **"Cost today" works for Fable bots.** The price table had no entry for
  `claude-fable-5`, so the estimator honestly returned "unknown" and the
  dashboard pinned at "$0.00 (partial)". Fable is now priced ($10/$50 per
  MTok live, half that on Batches), and the gauge reflects real spend.

## [0.11.0] - 2026-06-12

**Status:** Pre-release (beta). Streamed chat requests.

### Changed
- **Chat requests now stream from the API.** The reactive tool loop — every
  mention, scan, DM, and `/memory` turn — uses `messages.stream()` and
  collects the final message, instead of a blocking `create()`. Discord
  delivery is unchanged (full messages, posted once); what changes is the
  wire: the SDK no longer rejects large `max_tokens` as a potential >10-min
  operation, so the v0.10.6 `NONSTREAMING_MAX_OUTPUT` clamp (16,000) is gone
  and Fable/Opus/Sonnet get their full 32,000-token output ceiling back.
  Background calls (agentic one-liners, episode distillation, the Prime)
  request ≤4,000 tokens and stay non-streaming.

## [0.10.7] - 2026-06-11

**Status:** Pre-release (beta). Live logs.

### Added
- **The dashboard's log view is live.** The Monitor's log tab (renamed
  **Channels / Logs**) now streams the bot's log in real time over the
  backend's SSE endpoint — new lines appear the moment they're written,
  including a bot restart. It auto-scrolls (unless you scroll up), has a
  pulsing live indicator and a **Clear** button, and the **auto-follow**
  toggle (which existed but did nothing) now actually pauses the stream for
  reading. Previously the view fetched 50 lines once and never refreshed.

## [0.10.6] - 2026-06-11

**Status:** Pre-release (beta). Three live-found fixes.

### Fixed
- **Bots on Fable/Opus/Sonnet no longer crash on every message.** The reactive
  request derived `max_tokens` as `min(context_tokens, model_output_ceiling)`,
  which lands at 32,000 for those models — and the non-streaming SDK refuses a
  request that large (it estimates >10 min and demands streaming). The cap is
  now also clamped to a non-streaming-safe ceiling (`NONSTREAMING_MAX_OUTPUT`,
  16,000), ample for any reply. (Only surfaced once a Fable-configured bot went
  live.)
- **Start / Stop / Restart on a bot's own dashboard now work.** Those nameplate
  buttons were rendered but never wired — only the fleet board's worked.
- **Adding a skill now actually uploads the file.** The add-skill dialog was
  registering the skill's name in `default_skills` without ever sending the
  `.zip` to disk, so the bot was told to load a skill that wasn't there
  ("not found in catalog"). It now uploads before enabling.

## [0.10.5] - 2026-06-11

**Status:** Pre-release (beta). A real rethink of how the bot remembers.

### Changed
- **The bot writes its own memory in the first person.** Consolidation and
  induction used to frame the distilling model as a detached, third-person
  archivist. It's now framed as the bot itself — fed the bot's personality —
  revisiting its own memory top-down and rewriting it in the first person and
  its own voice. Era digests, channel notes, server culture, and user
  profiles all read like the bot's own notes-to-self instead of a report
  about it.
- **Memory is patterns and facts, not a log of every exchange.** The
  distillation prompts now pull recurring dynamics, learned facts, inside
  jokes, the drama and conversations that *shaped* a group, relationships,
  and — for working servers — deliverables, roles, and workflows. Cataloguing
  every interaction flattened people into caricatures (and the bot with
  them); it no longer does that.
- **Consolidation edits surgically.** Memory is treated as precious: the
  passes preserve stable long-term facts and change as little as the evidence
  forces, revising recent/top-of-mind material first and overturning an old
  established fact only on clear contradiction. (The bot can already edit its
  own memory live, so the background pass is for consolidation, not churn.)
- **Old facts are dated.** Time-sensitive claims (a job, a city, a status)
  are written as *true-as-of-then* when they come from old messages, so a
  year-old "works at X" reads as possibly stale instead of current.
- **Induction stays honest about provenance** — first person, but explicitly
  *gathered-from-reading, not lived*.
- Origin tags, vault boundaries, and the cross-server discretion norms are
  unchanged.

### Added
- **Reconsolidation is opt-out and tunable.** New `agentic.consolidation`
  config (`enabled`, `interval_days`) — turn the background memory pass off to
  freeze memory at what the bot writes live, or stretch the interval to spend
  fewer tokens. Surfaced in the app's Configure → Brain tab.

## [0.10.4] - 2026-06-11

**Status:** Pre-release (beta). Skill management and a stray fixture.

### Added
- **Remove a skill from the Integrations tab.** Custom skills now carry a
  **Remove** button that deletes the skill from disk (a new
  `DELETE /skills/{name}` endpoint). Skills live in one shared folder, so the
  UI is explicit that removal affects every bot and takes effect on each
  bot's next start. (MCP servers already had a per-card Remove.)

### Removed
- **The bundled `test-skill` fixture.** A leftover skill used to verify the
  discovery system was tracked in the repo and shipped to every install,
  showing up in the skills list. It's gone.

## [0.10.3] - 2026-06-11

**Status:** Pre-release (beta). Dashboard polish — the app reads more clearly
and the repository tab behaves like a real file manager.

### Added
- **Repository file manager** — the Repository tab is now a small Finder:
  one-click **Upload file** (straight to a native picker, into the selected
  folder), **New folder** (created and dropped into inline rename),
  double-click-to-rename for files and folders, and **drag-and-drop** to move
  things between folders or out to the root. Backed by new
  `repository/dir` (mkdir) and `repository/move` (rename/move) endpoints;
  folder deletion is recursive.
- **Config presets read as labelled cards.** Reaction usage, rate limit,
  proactive intensity, and log level now render each option as a selectable
  card with a Capitalized label and a one-line description, instead of a bare
  dropdown — you can tell what each preset means without guessing.

### Changed
- **The dashboard stays current without a restart.** Saving config now
  refreshes the bot's nameplate and re-pulls the monitor and memories tabs,
  and the save banner reflects whether a restart is actually needed (a
  stopped bot just applies it on next start) — matching the
  configure-before-you-start workflow.
- **File-tree chevrons track their state.** Folder arrows in the memory and
  repository browsers rotate to show expanded vs. collapsed.
- **Bot cards telegraph their clickability** with a hover state (the whole
  card already opened the bot's dashboard).

### Fixed
- **Memory browser no longer shows a phantom second tree.** `.history`
  archival snapshots (the id-named recovery copies kept beside each rewritten
  file) are now hidden from the memory and repository trees.

## [0.10.2] - 2026-06-11

**Status:** Pre-release (beta). Configuration clarity, memory readability, and a
handful of behavior fixes that came out of reviewing what 0.10.x actually does.

### Added

- **Memory browser resolves IDs to names.** The Memories tab now shows
  "Group No.5", "#general", and people's names with the raw Discord ID as a muted
  subtitle, instead of a wall of numbers — resolved from the channel-name cache
  and message authors. Files stay ID-keyed on disk (stable identity); only the
  display changes.
- **Always quotes the message it's replying to.** When someone replies to an
  earlier message, the bot now sees a short quote of that original — however old
  it is — pulled durably from message memory, so it always knows what it's
  answering.
- **Memory model thinks harder.** Induction and consolidation now run at medium
  thinking effort on capable models.

### Changed

- **Configuration declutter.** MCP and skill settings now live only in the
  Integrations tab (removed from Configure); the Advanced sub-tab is grouped
  under section headers instead of one flat scroll; `rate_limit` moved to
  Engagement (it's a response-frequency throttle, not a personality dial); the
  memory-model picker drops Haiku (it's still on the older thinking API) and adds
  Fable 5 and Opus 4.8.
- **`max_tokens` is gone from the UI and now derived.** Users only set
  `context_tokens`; output is `min(context_tokens, the model's output ceiling)`,
  clamped per-model so it can never exceed what the API allows. Generous enough
  that extended thinking never truncates, with no knob to misconfigure.
- **Backfill covers attachments by default.** The backfill slider now drives both
  message and attachment backfill, with matching depth.
- **No skill is preloaded by default.** The PDF skill (a testing residue) is no
  longer forced into every conversation; the bot requests any skill on demand
  when it's actually needed.
- Distillation prompts now write people and channels by name, not raw IDs.

### Fixed

- Long config labels/paths no longer overflow their column and overlap the
  controls (e.g. `agentic.proactive.allowed_channels`).

---

## [0.10.1] - 2026-06-11

**Status:** Pre-release (beta). A polish pass over 0.10.0: memory that adapts to
what a server actually *is*, an induction pipeline that no longer drops dense
channels, and a configuration UI reorganized around how people actually read it.

### Added

- **Server-character awareness in memory.** Induction and the weekly
  consolidator now read what kind of space a server is — a workplace runs on
  decisions and ownership; a friend group runs on its relationships, humor, and
  register — and let that steer what's kept, instead of flattening every server
  into the same standing-facts-and-decisions minutes. Two layers: the
  distillation prompts self-infer the register, and an optional operator-authored
  `memories/<bot>/servers/<id>/character.md` (never auto-written, unlike
  `culture.md`) anchors that judgment when present.
- **Backfill slider** in the config UI — `Off · 30 · 90 · 180 days · Unlimited` —
  replacing the raw day count and the separate enable toggle.
- **Timezone dropdown** — a curated list, no more typing IANA strings.

### Changed

- **Configuration is now horizontal sub-tabs** (Identity · Connection · Brain ·
  Engagement · Advanced) instead of one long scroll with a collapsed Advanced
  section. Status moved into Identity; the unused Description field and the
  Configure/Integrations intro blurbs are gone; the two skills toggles are
  relabeled so "available on demand" vs "preloaded at start" reads clearly.
- **Fleet board**: the whole bot card is clickable (not just the name); the name
  is larger and sits above a smaller status pill; checkboxes are bigger.

### Fixed

- **Induction dropped the largest channels.** A 100k-token chunk asked to
  summarize into an 8k output cap truncated into invalid JSON and silently
  dropped the channel. Chunks are now bounded to 12k and the output cap raised to
  12k, so even dense, high-traffic channels distill cleanly.

---

## [0.10.0] - 2026-06-11

**Status:** Pre-release (beta). The app becomes the product: a self-contained
installer a non-technical person can run — no Python, no git, no `.env`
editing, no terminal — plus a ground-up overhaul of the configuration UX.
The git-checkout developer model keeps working unchanged.

### Added

**Self-contained distributable**
- The installer ships an embedded CPython runtime and the framework source as
  app resources; bot data (configs, memories, databases, logs, secrets) lives
  in `%APPDATA%\Discord Agents` and survives every update.
- Framework split: `supervisor.py --code-root` + `SupervisorRoot(root,
  code_root)` separate read-only code from writable data. In a git checkout
  both are the same directory and nothing changes.
- First-run seeding: the daemon scaffolds the data root (directory tree,
  `.env`, `mcp_servers.json`) on boot, idempotently.
- `app/build-bundle.js` stages the embeddable CPython + vendored
  dependencies + framework for electron-builder's `extraResources`.

**Secrets in the app**
- Claude API key and per-bot Discord tokens are entered in the UI, validated
  against the live APIs before being written, stored in the install's `.env`,
  and never echoed back. First-run onboarding asks for the key.
- New endpoints: `GET /api/setup` (booleans only), `PUT /api/setup/anthropic`,
  `PUT /api/bots/{id}/token`.

**Configuration UX, rebuilt**
- Essentials first (identity, connection, brain, engagement); everything else
  under a collapsed Advanced section. Defaults merge under the loaded YAML so
  every setting renders and works even in a minimal config.
- Servers and proactive channels are picked from Discord by name (fetched
  live with the bot's token) — pasted ids are gone. Which servers a bot is
  *in* is decided by inviting it on Discord; the config only selects among
  them.
- Quiet hours: a clickable 24-hour grid (was a dead, read-only chip row).
- Editable chips for hand-edited lists; checkboxes mean checked = on.
- Model picker includes Fable 5; the effort dial renders only on
  effort-capable models (elsewhere it would 400 every call).

**Memory pre-population + induction from the app**
- Memories tab: create memory files directly; induction card runs a
  dry-run cost preview or a full induction with live output streaming.
- Repository tab: upload files into a bot's repository and delete them.

**Cost monitoring**
- Dollar estimates beside token counts (per-bot gauge and fleet bar), priced
  per model; unknown models show no number rather than a guess.

### Fixed

- **Closing the app no longer orphans bots.** The window now asks the daemon
  to shut down cleanly: bots stop, their desired state is kept, and they come
  back automatically when the app reopens. A daemon the operator runs from a
  terminal is left alone.
- The daemon restores desired bots on boot (shutdown's docstring promise,
  previously unimplemented) and reclaims orphaned bot processes before
  spawning — ending the double-login-on-same-token failure mode.
- App icon: the stock Electron logo is replaced by the project icon
  (`slh_icon.svg` → ico/png at build time; embedded with rcedit in an
  afterPack hook, sidestepping the winCodeSign cache's Developer Mode
  requirement).
- Hardcoded `v0.9.2` strings in the dashboard chrome now read the version
  from the API.
- `supervisor.py` inserts its own directory on `sys.path` (the embeddable
  interpreter has no implicit script-dir entry).

### Notes

- `api.context_messages` and `api.max_tokens` are still load-bearing (reseed
  tail window and per-reply cap) — they moved to Advanced, not out.
- `app-config.json` is now also read from the app's userData directory,
  which survives installer upgrades; keep the git-install pointer there.

---

## [0.9.2] - 2026-06-11

**Status:** Pre-release (beta). Dashboard polish — the first release delivered
to existing installs automatically by the v0.9.1 auto-update.

### Fixed

- Fleet card: a bot-detail rule (`.activity`) was unscoped and bled onto the
  fleet row, drawing a stray divider and shoving "N msgs today" down against
  it. Scoped to `.tabpanel .activity`.
- Fleet card height bumped (56px → 64px) so the row no longer reads as
  cramped — the stray margin from the bug above had been masking how tight it
  was.
- Bot detail: the stat figures (messages stored / episodes / …) are now
  vertically centered against the 7-day sparkline instead of bottom-pinned
  below it.

---

## [0.9.1] - 2026-06-11

**Status:** Pre-release (beta). Point release: automatic updates for the
desktop application.

### Added

**Auto-update (two layers)**
- The Electron launcher updates itself from GitHub Releases via
  `electron-updater`: it checks on launch and every 6 hours, downloads a new
  installer in the background, and offers a one-click restart to apply it.
  Bots, config, and memories are never touched. Pre-releases are tracked
  (releases ship marked as pre-release).
- The framework catches up on its own. Because `main` only advances at
  releases, a production install fast-forwards its git checkout to
  `origin/main` *before* the supervisor daemon starts, so the daemon always
  boots on the latest release. `pip install` runs only when
  `requirements.txt` actually changed in the incoming range.

### Notes

- Forward-only: a v0.9.0 install has no updater and must be reinstalled once
  to reach v0.9.1; every release after that is automatic.
- The framework updater is deliberately conservative - it only acts on a
  clean git checkout sitting on `main`. Dev workspaces (any other branch) and
  installs with local edits are left untouched. Disable per-install with
  `"autoUpdateFramework": false` in `app-config.json`.

---

## [0.9.0] - 2026-06-11

**Status:** Pre-release (beta). Validated by a live end-to-end campaign:
DM round-trip and /memory cycle driven through a real Discord session,
ask_prime mechanical gates, a standing watch resolved and relayed, the
supervisor managing real bot processes (crash auto-restart included), the
dashboard rendering the campaign as it happened, and the packaged Windows
app attaching to a live daemon.

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

### Fixed (found live, during the release campaign)
- Contentless Discord messages (sticker-only, pin/system notices) persisted
  as empty user turns and 400'd every later request on that channel
  ("text content blocks must be non-empty") - now skipped at ingestion and
  seeding, and healed at the wire for states already carrying the residue
- Outbound DM replies through the slash-command path could register the
  bot as its own DM partner, poisoning `dm_partner()` resolution for that
  channel - outbound registration now refuses the bot's own id
- The dashboard's channel rail showed raw ids for channels without recent
  traffic: the bot now seeds the channel-name cache for every visible
  channel at startup; the monitor feed also rendered the prototype's
  hardcoded bot name instead of the real bot id

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
