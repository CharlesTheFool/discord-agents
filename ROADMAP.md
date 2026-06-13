# Roadmap

Where Discord Agents is headed. Versions before 1.0 are beta; themes are
commitments, details are not. History lives in [CHANGELOG.md](CHANGELOG.md).

## 0.6.2 — Companion feature *(reserved)*

Slot held for a small follow-on to the 0.6.1 bot file repository.

## 0.7.0 — Unified identity & memory reconsolidation

One bot, one continuous identity across every server it inhabits — with the
judgment to handle that gracefully.

**Identity model**
- **People are global, places are local.** User profiles move to a single
  per-human file (`global/users/`); channels, episodes, culture, and
  follow-ups stay server-scoped. Existing per-server profiles merge
  automatically on first run.
- **Provenance everywhere.** Unified data keeps its origin: profile claims
  carry source tags, cross-server search/attachment results are labeled with
  the server they came from, repository files stay organized by server even
  though all drives are reachable.
- **Vaults** replace the `data_isolation` modes: one config key
  (`vaults: []`) marking channels/servers whose content never leaves them —
  a one-way valve (the bot is fully itself inside; nothing escapes:
  excluded from outside search, memory writes contained, repository saves
  blocked). Everything coarser is Discord's job: role permissions, and
  separate tokens for separate minds.
- **Tool scopes.** Search and repository listing default to the current
  server and widen to `global` on request — local and quiet by default,
  cross-server reach explicit and labeled.
- **Discretion norms.** A mandatory prompt section turns provenance into
  decorum: knowing someone from another server is fair to acknowledge;
  their business stays where it was learned. Unified, not a gossip pipe.

**Memory reconsolidation**
- Memory currently only accretes; nothing merges, decays, or self-corrects.
  A weekly background pass (Batches API, configurable
  `api.consolidation_model`, Sonnet default) fixes that:
  1. **Episode compaction** — old episodes merge into era digests
  2. **Profile rewrites** — re-derived from evidence, contradictions resolve
     toward recency, stale claims demoted, lean register enforced
  3. **Provenance repair** — origin tags normalized
  4. **Channel/culture refresh** — slower-cadence evidence-anchored rewrites
- Every rewritten file's prior version is archived (`.history/`, pruned) —
  one bad pass is always recoverable. Vault boundaries are absolute.

**Substrate**
- Message Batches API client: latency-tolerant distillation at 50% cost.

## 0.8.0 — Induction & new surfaces

Make a bot joining a populated server instantly tuned-in instead of amnesiac,
and present on every conversation surface.

- **Server induction** — a deliberate operator action (`induct`, with
  dry-run cost preview) that distills existing history into channel
  digests, lean third-person user profiles, and server culture — explicitly
  marked as *observations from reading the backlog, not lived memory*, so
  the bot does its homework without faking familiarity. Built on the
  reconsolidation machinery (induction is reconsolidation at t = 0).
- **Thread support** — threads become first-class conversation surfaces
  (backfill, replies, memory).
- **Voice-channel text presence** — the bot lives in voice channels' text
  chat like any other channel: meeting notes relayed there become memory,
  documents, reminders.

## 0.9.0 — The Prime, DMs, and the application

The bot gains a self above its servers, a private surface, and the framework
becomes a product.

- **The Prime** — a bot is already one mind across all its servers; 0.9
  gives that mind a surface of its own. DM the bot and you're talking to
  the Prime: it knows everything global — people, places it inhabits, its
  own state — but stands in no particular server. The Prime also moderates
  **cross-server coordination**: the bot's presence in one server can ask
  the Prime to have its presence in another server pose a question or make
  an announcement, optionally with a **standing watch** that monitors for
  the response and feeds it back into the requesting conversation. The
  Prime applies judgment, hard rate caps, and vault boundaries — it can
  refuse. All in-process: no second token, no extra infrastructure.
  Knowledge that crosses servers this way is labeled ("relayed via Prime").
  Bots never talk to *other* bots — separate tokens exist precisely to keep
  minds apart.
- **DM support** — each DM is a private room (an implicit per-user vault):
  the bot knows its DMs exist and says so, but nothing said there leaks
  out. Slash commands (`/memory show | remember | forget | feedback`) let a
  user deliberately cross that boundary — teach the bot something
  privately, correct what it remembers about you, tell it what you didn't
  like — integrated into how it treats you everywhere, cited nowhere.
- **Supervisor daemon** — process management for bot instances (start,
  stop, restart-on-crash) plus a local API for status, configuration,
  logs, and memory/repository browsing. Built once, consumed by the
  dashboard and the desktop app. Bots never communicate through it.
- **Desktop application** — Electron app on the supervisor API: create,
  configure, and monitor bots (memories, repositories, stats, activity,
  thinking traces) without touching a terminal; download-site distribution
  for non-developer operators.

## 0.12.0 — Native messaging

Sending stops being "whatever text the model ends its turn with" and becomes
a deliberate act: the model talks to Discord through a `send_message` tool.

- **Tool-based send** — each tool call is one Discord message, with explicit
  parameters for reply-targeting (answer a specific message mid-batch),
  attachments (a file is either in the call or it isn't — the
  phantom-attachment failure mode becomes unrepresentable), and natural
  multi-message pacing. Staying silent is simply not calling the tool, not
  emitting empty output. Replaces the capture-final-text pipeline tail
  (send, persist, engagement tracking, must-reply semantics all re-seam).

## 1.0

Declared by the operator once every feature above has been tested in real
use. Stability, not features: extended prod soak, a full audit pass like the
v0.6.0 pre-release sweep, config freeze.

## Later / unscheduled

- **Voice listening and speech** — researched and deliberately shelved.
  Discord's mandatory voice E2EE (March 2026) broke every community
  voice-receive library, and the platform has never officially supported
  bots listening; building on an unmaintained, patched dependency is
  incompatible with a stability-first 1.0. Revisit only if Discord or a
  maintained library makes voice receive a supported surface. (The design
  is archived and ready: local faster-whisper + VAD, per-utterance
  transcription over Discord's per-user streams.)
- **Richer presence** — events, polls, scheduled activities
- **Daily web-search quota** — dormant; revisit only if per-request caps
  prove insufficient
- **The workshop: a real development sandbox** — the Anthropic code-execution
  container is air-gapped (probed 2026-06-12: pip install times out, curl
  fails, no ffmpeg binary; only the preinstalled scientific stack exists).
  That caps artifact work at what ships in the box. Two candidate paths for
  giving bots a real workspace, to be designed properly before building:
  1. **Local sandbox** — a Docker/WSL container on the host with network,
     pip, ffmpeg, and the per-server repository mounted as its working
     directory. Maximum capability; serious security surface (model-written
     code + network + operator's machine) — needs egress policy, resource
     caps, and a kill switch designed first.
  2. **Managed Agents session** (Anthropic-hosted, beta) — the bot spawns a
     cloud session whose environment allows package managers
     (`networking.limited` + `allow_package_managers`), mounts repo files,
     and returns outputs via the Files API. No local attack surface, real
     pip; adds per-session cost, latency, and a beta dependency.
  Pairs naturally with long-work concurrency below — workshop jobs are
  exactly the long work the chat path must survive. Design both together.
- **Long-work concurrency** — when a turn enters long tool work (skill
  containers, deck generation), a parallel chat path keeps handling incoming
  messages with an injected "you're already mid-response to X" notice, so
  fast conversations don't fly past a busy bot. Scoped to long-work turns
  only — ordinary chat turns stay serial per channel (ordering and
  state-write hazards aren't worth it there). Not yet properly discussed;
  design with care after 0.12.0's tool-based send (discrete, observable
  sends make the concurrency story much cleaner).
- **Cheap scan pre-gate** — a small classifier model deciding respond/stay
  silent before the big model writes. Only worth designing if dashboard cost
  data shows silent scans are a real expense (the prompt-cache layout already
  makes them ~330 uncached tokens), and only if the gate provably doesn't
  degrade the social judgment that makes the bot fun.
- **Repository file viewer** — the dashboard's repository view should
  preview the file types the bots actually produce: PDFs, images (incl.
  gifs), audio, video — not just text. And the file browser itself needs a
  redesign toward the clean macOS-Finder-style UI originally requested
  (columns/preview pane, drag-drop, breadcrumbs) instead of the current
  clunky list. Feature work, not a bugfix; schedule deliberately.
