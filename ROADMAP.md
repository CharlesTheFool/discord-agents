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

## The active plan

Each release below changes one thing, so a regression can be attributed.
0.13.0 shipped alone for that reason: had concurrency changes ridden along,
a cache regression would have had two suspects. The workshop came first,
then what runs in it, then where else it can run.

### 0.15.0: projects and workers

**Projects.** A project is a memory object: a note at `projects/<slug>.md`
in the scope's own tree, with `status`, `next_check` and `home_channel`
frontmatter the hourly loop reads without a model call, plus a folder under
`/workspace/projects/`. Discovery is scope-bound and `home_channel` must
resolve to the same scope as the note, so a model-written note cannot aim a
check-in at another server. No command starts a project; the bot opens one
at its discretion. Work resumes three ways: a job or worker finishes, a
`next_check` date passes, or someone mentions it.

**Workers.** A worker is a job whose executor is a model loop instead of a
shell command, started with `delegate`. Workers are hands, never voice: they
read and search memory, message history and attachments under the same
vault rules as the resident, and never write memory, send messages, or ask
the operator for anything. They run on `api.worker_model` (default: the
main model), draw from their own pool of two concurrent model calls, and
stop at 40 tool-loop iterations or 60 minutes with a partial report.

**Supervisor and app.** A per-bot workshop panel: scopes, sandbox status,
disk use, jobs, allowlists, pending approvals, reset and wipe.

### 0.15.1: the second provider

A native OpenAI Responses adapter, deliberately the wire shape most unlike
Anthropic Messages, as the proof that the model layer's seams hold.
Stateless requests, reasoning items replayed with their encrypted content,
prompt caching mapped to OpenAI's own, effort mapped to `reasoning.effort`,
function calls and outputs mapped to Responses items. It ships on the
background roles first (episode distillation, watch checks), then workers.
Claude remains the model the framework is built and tested on.
Chat-completions-shaped providers and Google are later.

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
