# Roadmap

Where Discord Agents is headed. Versions before 1.0 are beta; themes are
commitments, details are not. Full history with upgrade notes lives in
[CHANGELOG.md](CHANGELOG.md).

## Shipped so far

- **0.1 to 0.4 (2025):** the core framework. @mention handling, per-channel
  rate limits with engagement-aware backoff, SQLite message history with
  full-text search, multi-bot YAML configuration, the memory tool, prompt
  caching, the hourly agentic loop (follow-ups, proactive engagement,
  per-channel engagement learning), web search, image handling.
- **0.5 and 0.6:** MCP servers, skills with code execution, and the memory
  redesign: episodic sessions replace client-side token budgeting, validated
  by a live scenario campaign. 0.6.1 adds the per-server file repository.
- **0.7 and 0.8:** one global profile per human with origin tags, vaults,
  weekly reconsolidation over the Batches API; server induction, threads,
  and voice-channel text as first-class surfaces.
- **0.9:** DMs as the Prime's private surface, `/memory` commands,
  cross-server coordination through `ask_prime` with standing watches, the
  supervisor daemon, the dashboard, the Electron desktop app, and the events
  table every turn writes to.
- **0.10 to 0.12:** the self-contained Windows installer and configuration
  UX, streamed chat requests, the `send_message` tool (compat mode),
  phantom-attachment hardening, dashboard staleness fixes, Sonnet 5 as the
  default model, and in 0.12.5 token authentication for the dashboard API.
- **0.13.0:** one model layer. Every model call goes through one driver and
  one request type; a model catalog and a tool registry replace hand-kept
  tables; golden tests prove the requests are byte-identical to 0.12.5.
- **0.13.1:** per-channel concurrency (turns in different channels run side
  by side; one channel's turn holds it from state load through the saved
  reply), a send guard against the send-then-restate double post, turn
  events that count every model call, consolidation that never overwrites a
  file the bot edited meanwhile, and vault write containment.
- **0.14.0:** the workshop. A microVM per scope (server, vaulted channel or
  thread, DM) with a shell, a file editor, a persistent `/workspace` and
  local skills; background jobs that report back on their own; egress
  through the bot's own proxy to an exact-host allowlist, with network
  approvals DMed to the operator; a start gate that test-boots a VM before
  login. Server-side code execution and the Skills API are gone, and
  attachments persist by the framework's own id.
- **0.14.1:** polish. Automated turns stay quiet when nothing needs saying;
  text-only skills and documents up to 4 MB where the workshop is off;
  scope proxy credentials rotate on every start; an MIT license.
- **0.14.2:** the message cap stops breaking the prompt cache. A full
  channel used to drop one message per turn, shifting everything after the
  system prompt; it now trims to a quarter below the cap, so the cache
  holds for several turns.
- **0.14.3:** the workshop is the only sandbox again. The web tools'
  newest versions brought Anthropic's code execution along; they're pinned
  to the versions without it.
- **0.14.4:** web search no longer cuts sessions short (a server-tool
  call's summed usage no longer counts as the context size).
- **0.15.0:** projects and helpers. A project is a memory note whose
  `next_check` brings the bot back to it on its own, bound to the scope
  that holds it; `delegate` hands a task to a helper that works in the
  workshop and reports back, with hands and no voice (it reads, never
  sends or writes memory). The dashboard gains a Workshop tab, the app
  keeps the computer awake on request, and web fetch stops taking URLs
  from the bot's own tool output.
- **0.15.1:** the second provider. Background work and helpers can run on
  OpenAI's Responses API through a native adapter, routed by each model's
  catalog entry; an all-Claude bot's requests stay byte-identical. Prompt
  caching was measured before it was built: OpenAI matches only at
  breakpoints still present in the request, so the adapter keeps the last
  two.
- **0.15.2:** two bots that answer each other stop on their own: past six
  bot messages in a row, unaddressed bot chatter isn't scanned until a
  person speaks.
- **0.15.3:** a scan that stays out says `<silent/>` instead of posting
  "*nothing to add*"; a quiet channel's first messages no longer land twice
  in its conversation.

## The active plan

Each release below changes one thing, so a regression can be attributed.
0.13.0 shipped alone for that reason: had concurrency changes ridden along,
a cache regression would have had two suspects. The workshop came first,
then what runs in it (0.15.0) and a second provider (0.15.1), both
shipped, then where else it can run.

### 0.16.0: Host mode

A third scope setting beside Container and Off: commands run on the
operator's own PC, in the operator's DM and in servers or channels the
operator marks trusted, each scope behind its own restricted Windows
account. The first probe on Windows 11 Home showed file permissions and a
per-account firewall rule hold, but that an account launched onto the
operator's desktop can read the clipboard, see windows and send
keystrokes. So the design moves the runner off the desktop entirely: one
registered at Host setup (the one admin prompt) and running in the
background session, where the operator's desktop and clipboard don't
exist. A second probe proves that before anything is built.

### 1.0

Declared once the releases above have run in real use. Stability, not
features: an extended production soak, a full audit pass like the v0.6.0
pre-release sweep, a configuration freeze. The macOS build and signing of
the desktop app belong to the final application release.

## Later

- **Authenticated GitHub and npm access.** A bot-owned identity (a GitHub
  App or a fine-grained token limited to chosen repositories), stored on the
  host and injected by the proxy only for the scopes the operator granted,
  so the credential never enters a sandbox. Also deferred: restricting HTTP
  methods at the proxy, which needs TLS interception.
- **Resumable workers, a remote sandbox backend, operator folder grants,
  GPU in sandboxes.**
- **Voice listening and speech.** Researched and shelved. Discord's
  mandatory voice E2EE (March 2026) broke every community voice-receive
  library, and the platform has never supported bots listening; building on
  a patched, unmaintained dependency is incompatible with a stability-first
  1.0. The design is archived (local faster-whisper with VAD, per-utterance
  transcription over Discord's per-user streams) and waits for a supported
  surface.
- **Richer presence:** events, polls, scheduled activities, deleting its
  own messages.
- **Daily web-search quota.** Dormant; revisit only if the per-request cap
  proves insufficient.
- **Cheap scan pre-gate.** A small classifier deciding respond-or-silent
  before the main model writes. Worth designing only if cost data shows
  silent scans are a real expense (the cache layout already makes them a
  few hundred uncached tokens) and only if the gate provably keeps the
  social judgment that makes the bot worth having.
- **Repository file viewer.** The dashboard's repository view should preview
  what bots actually produce (PDFs, images, audio, video), and the browser
  itself needs a redesign toward a Finder-style layout with columns, a
  preview pane and drag-and-drop.
