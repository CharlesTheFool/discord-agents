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

## The active plan

Each release below changes one thing, so a regression can be attributed.
0.13.0 shipped alone for that reason: had concurrency changes ridden along,
a cache regression would have had two suspects.

### 0.14.0: the workshop

Each bot gets a development environment of its own, so it can run code,
install packages, keep project files, work for hours in the background and
come back with results, without becoming a terminal reachable from Discord.
Capabilities stay ambient and discretionary; nothing requires a "start
session" command.

**Sandboxes.** One microVM per scope, run by microsandbox (libkrun on
Windows Hypervisor Platform, Hypervisor.framework or KVM). A scope is a
server, a vaulted channel, or a DM, and the innermost vault wins. Two scopes
never share a sandbox, a volume, a shell state, or a proxy credential.
Nothing from the host is mounted into a sandbox; files cross the boundary as
bytes through framework code. The image is `python:3.12-slim-bookworm`
provisioned with Node, git, ffmpeg, ripgrep, build tools and the document
libraries; `/workspace` is a per-scope volume with a hard size cap, and a
free-space floor on the host drive refuses new work below it. Spikes on
Windows 11 Home and macOS passed: sub-second creates, roughly 45 MiB of host
memory per idle sandbox, persistence across stop and start, and network
lockdown as designed.

**Tools the model sees.** `bash` (`command`, `background`, `timeout_s`;
every command is a job, and a foreground command still running at the
three-minute ceiling becomes a background one instead of being killed), a
file editor, `request_network_access`, and `delegate`. The server-side code
execution tool, the skills container and `container_upload` blocks are
removed; the Files API remains only as an adapter-internal cache for
document inputs. The skills catalog stays, served from local folders.

**Egress.** The sandbox's network policy is default deny with DNS denied;
its one route is the host-side proxy. The proxy accepts CONNECT only, to
port 443 only, with a per-scope credential and a per-scope host allowlist;
it resolves names itself, refuses non-global addresses (including
IPv4-mapped IPv6 and NAT64), and connects to the exact address it checked.
The default allowlist is download-only: PyPI, GitHub archive and object
hosts, Debian package mirrors. `github.com` and `registry.npmjs.org` accept
uploads, so they are approval items. The workshop carries no credentials in
this release and never borrows the operator's own logins.

**Approvals.** `request_network_access` is the only runtime approval. It
DMs the operator with Allow and Deny buttons; an allowed host persists in
that scope's allowlist, requests expire after 24 hours, and the decision
reaches the requesting channel as a persisted context note so the bot can
resume on its own. The operator is the Discord application's owner or, for
a team-owned application, its admins; `workshop.operators` overrides.

**Scope settings.** Container is the default. Off removes the workshop from
a scope and is always an explicit operator choice. Host runs commands on the
operator's own PC, allowed in the operator's DM and in servers or channels
the operator marks trusted, each behind its own restricted Windows account:
file permissions keep it out of the install, the data root and the
operator's profile; an outbound firewall rule forces its traffic through the
same proxy; its environment carries no secrets; the dashboard shows a banner
listing exactly what the account can reach. A verification gate on Windows
11 Home comes first; if per-account egress blocking cannot be enforced
there, Host falls back to the operator's DM only.

**Start gate.** If any scope resolves to Container, the bot starts only
after the sandbox runtime passes a test boot. On a host without
virtualization the bot does not start and the dashboard or CLI walks the
operator through the prerequisite for their platform. `workshop.enabled:
false` is the documented last resort: the bot starts with no execution
capability at all, and the dashboard says so.

**Projects.** A project is a memory object: a note at `projects/<slug>.md`
in the scope's own tree, with `status`, `next_check` and `home_channel`
frontmatter the hourly loop reads without a model call, plus a folder under
`/workspace/projects/`. Discovery is scope-bound and `home_channel` must
resolve to the same scope as the note, so a model-written note cannot aim a
check-in at another server. No command starts a project; the bot opens one
at its discretion. Work resumes three ways: a job or worker finishes, a
`next_check` date passes, or someone mentions it.

**Jobs and workers.** Every command is a row in a jobs table (scope, home
channel, kind, state, log location). A worker is a job whose executor is a
model loop instead of a shell command, started with `delegate`. Workers are
hands, never voice: they read and search memory, message history and
attachments under the same vault rules as the resident, and never write
memory, send messages, or ask the operator for anything. They run on
`api.worker_model` (default: the main model), draw from their own pool of
two concurrent model calls, and stop at 40 tool-loop iterations or 60
minutes with a partial report. The resident's volatile tail gains one line
listing the scope's running jobs and workers.

**Supervisor and app.** A per-bot workshop panel (scopes, sandbox status,
disk use, jobs, allowlists, pending approvals, reset and wipe); the
supervisor stops a bot's sandboxes by label on an operator Stop; a first-run
test boot that, on Windows, offers to enable the Windows Hypervisor Platform
behind one admin prompt.

### 0.14.x: the second provider

A native OpenAI Responses adapter, deliberately the wire shape most unlike
Anthropic Messages, as the proof that the model layer's seams hold.
Stateless requests, reasoning items replayed with their encrypted content,
`prompt_cache_key` set to the channel id, effort mapped to
`reasoning.effort`, function calls and outputs mapped to Responses items.
It ships on a background role first; it is done when a worker and an
episode distillation run end to end on OpenAI. Claude remains the model the
framework is built and tested on. Chat-completions-shaped providers and
Google are later.

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
