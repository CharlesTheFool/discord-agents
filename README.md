# Discord Agents

**Version:** 0.13.1 (Pre-release Beta)

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.3+-blue.svg)](https://discordpy.readthedocs.io/)
[![Security](https://img.shields.io/badge/security-policy-blue.svg)](SECURITY.md)
[![Status](https://img.shields.io/badge/status-beta-yellow.svg)](CHANGELOG.md)

A self-hosted framework for Discord bots that behave like resident members
of a community rather than a chat window onto a model. A bot built on it
decides when to speak and when to stay quiet, remembers people across every
server it shares with them, distills conversations into episodic memory,
follows up on things people mentioned, and shows its operator what it read,
what it thought, and why it stayed silent. Everything runs on your machine:
one Python process per bot, SQLite and Markdown on disk, your own Anthropic
key.

This is a beta. The runtime is validated by live scenario campaigns on a
real server as well as by the unit suite, but configuration and APIs may
still change between beta versions. Report bugs through
[GitHub Issues](../../issues); report vulnerabilities through GitHub
Security Advisories, as described in [SECURITY.md](SECURITY.md).

---

## What it does

### Two engines

**Reactive.** Answers @mentions, and on a configurable interval (60 seconds
by default) scans the conversation it can see for a moment worth joining.
On the scan path the bot may stay silent; the pipeline treats silence as a
normal outcome, not a failure.

**Agentic.** An hourly background loop. It follows up on things people said
("how did the presentation go?"), starts conversation in idle channels when
configured to, learns per-channel engagement rates and backs off where it is
not wanted, and runs the standing watches described below.

### Memory

Memory is episodic. A channel's live conversation state is distilled into a
titled, timestamped episode file when the session crosses a token threshold
or goes idle, and the live context then reseeds. Episodes stay searchable
through the bot's memory tool, so "what did we decide last Tuesday" works
weeks later.

- **People are global, places are local.** One profile per human, keyed by
  Discord user ID, with per-claim origin tags recording where each claim was
  learned. Channel notes, server culture and follow-ups stay server-scoped.
- **Reconsolidation.** A weekly pass over the Batches API compacts old
  episodes into era digests and rewrites profiles from evidence. Every file
  it rewrites is copied to a `.history/` sibling first.
- **Induction.** `python bot_manager.py induct` distills a server's stored
  backlog into starting memory, labelled as observations rather than lived
  experience. `--dry-run` prints a cost table before anything is spent.
- **History and attachments.** Message history in SQLite with FTS5 full-text
  search; attachments indexed and retrievable by the bot after Discord's CDN
  links expire; a per-server file repository the bot manages with a tool and
  you can edit by hand.
- **DMs.** A DM is a private room with its own memory tree. `/memory show`
  returns your profile verbatim; `/memory remember`, `forget` and `feedback`
  let you teach or correct the bot through a one-shot grant that opens
  exactly your own profile file for that turn.

### One mind across servers

A bot in several servers is one mind with separate rooms. The `ask_prime`
tool lets its presence in one server ask "the Prime" (the bot above all its
servers) to pose a question or make an announcement in another. Mechanical
gates run before any model judgment: vault boundaries in both directions,
daily caps, refusal from DMs. A standing watch can ride along: the hourly
loop watches the target channel for an answer and relays it back, labelled
with its origin. A DM is a conversation with the Prime itself.

### Surfaces and capabilities

Text channels, threads (active and archived, with memory nested under the
parent channel), voice-channel text chats, and DMs. Image attachments are
understood in context. Web search runs as a server tool with citations,
capped per request. Remote MCP servers can be connected; their tools are
discovered at startup. Skills (`.zip` packages in `skills/`, plus
Anthropic's document skills for xlsx, pptx, docx and pdf) run with code
execution in Anthropic's server-side container, not on your host, and files
produced there attach to the bot's reply. That container is air-gapped: no
pip, no network. Version 0.14 replaces it with a local sandbox, described in
[ROADMAP.md](ROADMAP.md).

### Operator surface

- **Supervisor daemon** (`python supervisor.py`). A separate process on
  `127.0.0.1:8642` that starts, stops and restarts bots (crash recovery with
  exponential backoff, capped at five retries an hour) and exposes a read
  surface over each bot's artifacts: status, memory, repository, logs,
  events. It reads files and never imports bot internals. Every file route
  is path-jailed, and every `/api/` route except a read-only health check
  requires a per-install token.
- **Dashboard**, served by the daemon. A fleet board and per-bot tabs.
  Monitor shows engine gauges, live-context fill, and a channel monitor of
  the bot's turn stream: what it read, what it thought, which tools it
  called, what it said or why it stayed quiet. Configure renders the YAML as
  a form validated by the same code the bot boots with. Integrations,
  Memories and Repository browse and edit the rest.
- **Desktop app.** A Windows installer with an embedded Python runtime and
  the framework inside. Paste a Claude key and a Discord token, pick
  servers, press Start. Bot data lives in `%APPDATA%\Discord Agents` and
  survives updates. The launcher updates itself from GitHub Releases; a
  git-checkout install pointed at by `app-config.json` fast-forwards to the
  latest release on launch instead.
- **Events.** Every turn writes one structured row (mention, DM, scan,
  silent, proactive, follow-up, memory write, relay, watch, skill, reseed)
  with its tool calls. The dashboard and the conversations log both read
  from it, and `scripts/cost_by_turn_kind.py` reports API spend by turn
  kind.

---

## How it works

### One pipeline for every reply

Both reply paths, the @mention that must be answered and the periodic scan
that may stay silent, drive one shared pipeline: build the request, run the
tool loop, send, persist. A single parameter (`fallback_text`) is the only
semantic difference between them. The `/memory` command runs the same
pipeline through a synthetic carrier rather than writing files itself. A
guard added once (the repeated-call detector, the phantom-attachment bounce,
the empty-after-tools reprompt) therefore covers every path.

### Prompt-cache-aware request layout

Anthropic's prompt cache is a prefix cache, so anything volatile upstream
invalidates everything downstream. The request is laid out around that. One
cached system block holds personality, memory and the skills catalog.
Messages are rebuilt from conversation state with a cache breakpoint on the
last stable block. Everything per-request (timestamp, channel and user line,
attachment index, repository manifest, recent-message id map) rides a
transient `<context_update>` user message appended after both breakpoints
and never persisted. Steady state is a few hundred uncached tokens per turn,
and the token log prints uncached, cache-read and cache-write counts so a
regression shows up on the first turn.

### Mechanical safety

The design rule is that no safety property depends on the model declining
something. Vaults mark a channel or server whose content never leaves it;
the `VaultEnforcer` gates memory paths, the search SQL, attachment access
and repository saves, and every tool seam threads the current server and
channel through it. DM privacy is enforced in the same layer, before any
vault rule runs, so it holds with zero vaults configured. Cross-server sends
pass hard gates before their one bounded judgment call and fail closed on
API errors. The supervisor resolves every user-influenced path through one
jailing builder. Finer distinctions, such as whether to mention that you
know someone from another server, are a prompt-level norm, and the docs
call them that rather than guarantees. [SECURITY.md](SECURITY.md) lists
what is enforced, what is not yet, and what is scheduled.

### A model layer with a byte-identity proof

Since 0.13.0, every model call (chat turns, scans, follow-ups, watch checks,
episode distillation, the consolidation and induction batches, `ask_prime`)
builds one request type and goes through one driver in `core/model/`. A
catalog holds one entry per model family (effort support, output ceiling,
prices, cache semantics), so adding a model is one line. A registry
assembles tools, filtered first by policy (vaults, DM context, config) and
then by what the model supports, so a filtered tool's schema never reaches
the model. The refactor was proven by golden tests: the exact SDK request
for twelve call-site scenarios was snapshotted before any code moved, and
the Anthropic adapter has to reproduce each one byte for byte. Assistant
messages are stamped with the model that wrote them, so a future adapter
can drop thinking blocks that a different model produced.

### Native sending

The bot posts through a `send_message` tool rather than only through its
captured final text. Each call is one Discord message, and the tool result
reports ground truth (message id, which files attached), so the model
cannot sustain a false belief about its own output. Reply anchoring,
multi-message turns and attachments by filename ride the same call. Plain
final text still posts too, except right after a `send_message` call: that
is the send-then-restate double post, so it is held back, never persisted,
and counted on the turn's event.

### Concurrency

Turns in one channel run one at a time; turns in different channels run
side by side, with at most three model calls in flight bot-wide. A turn
holds its channel from loading the conversation state through saving its
reply, and everything else that writes a channel's state (scan ingestion,
relay notes, episode reseeds, proactive and follow-up messages) waits for
the turn to finish, so nothing lands between a turn's tool calls and its
answer.

---

## Quick start

### The short way (Windows, no Python, no git)

Download **Discord Agents Setup** from the latest release and run it. On
first launch the app asks for a Claude API key. Create a bot, paste its
Discord token on the bot's Configure tab, pick its servers from a list,
press Start. Everything below is the developer path.

### Prerequisites

- Python 3.10 or newer.
- A Discord application with a bot user and **Message Content Intent**
  enabled ([developer portal](https://discord.com/developers/applications)).
- An Anthropic API key ([console](https://console.anthropic.com/)). The
  default model is `claude-sonnet-5`. The model catalog in
  `core/model/catalog.py` records what each model supports, and startup
  validation refuses combinations the API would reject, such as `api.effort`
  on a model without effort support or adaptive thinking on Haiku 4.5.

### 1. Clone and install

```bash
git clone https://github.com/CharlesTheFool/discord-agents.git
cd discord-agents
pip install -r requirements.txt
```

### 2. Secrets

```bash
cp .env.example .env
```

```bash
ALPHA_BOT_TOKEN=your_discord_bot_token_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

The bot config names its token variable (`discord.token_env_var`), so
several bots can share one `.env`.

### 3. Bot config

```bash
cp bots/alpha.yaml.example bots/alpha.yaml
```

```yaml
discord:
  servers:
    - "YOUR_SERVER_ID_HERE"   # Right-click server > Copy ID (Developer Mode)
  timezone: "UTC"              # IANA name: America/New_York, Europe/London, ...

personality:
  base_prompt: |
    Who the bot is and how it behaves: tone, when to engage, formatting.
    This prompt is the main behavioral control.
```

### 4. Run

```bash
python bot_manager.py spawn alpha
```

The bot connects, backfills message history in the background, starts its
hourly loop, and answers @mentions.

### 5. Dashboard (optional)

```bash
python supervisor.py            # http://127.0.0.1:8642
```

The desktop app from the release assets opens the same dashboard in its own
window.

---

## Configuration

Configuration is deliberately small: about thirty user-facing settings.
Behavioral style lives in the personality prompt; operational tuning uses
presets that expand from internal constants.

```yaml
reactive:
  rate_limit: "moderate"        # strict | moderate | permissive | unlimited

agentic:
  proactive:
    enabled: false
    intensity: "moderate"       # gentle | moderate | active
    quiet_hours: [0, 1, 2, 3, 4, 5, 6]   # local host-clock hours

api:
  model: "claude-sonnet-5"
  background_model: "claude-haiku-4-5"   # episode distillation, watch checks
  context_messages: 30          # rolling window of Discord messages
  context_tokens: 80000         # session threshold: episodize and reseed past this
  effort: "medium"              # optional cost/depth dial: low | medium | high | max

vaults: []                      # channel or server IDs whose content never leaves them
```

`validate()` runs at startup and rejects bad presets, `context_messages`
outside 5 to 100, and model settings the API would refuse. Deprecated keys
warn with their replacement. `bots/alpha.yaml.example` is the complete
annotated reference.

---

## Deployment

A bot needs no inbound ports. Any process supervisor works; the examples
assume a bot named `alpha`. [DEPLOYMENT.md](DEPLOYMENT.md) covers running a
development checkout and a production install side by side, the release
workflow, the desktop app's automatic updates, and rolling back.

**systemd**

```ini
# /etc/systemd/system/discord-agent.service
[Unit]
Description=Discord Agent
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/discord-agents
ExecStart=/usr/bin/python3 bot_manager.py spawn alpha
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**PM2**

```bash
pm2 start bot_manager.py --name discord-agent --interpreter python3 -- spawn alpha
pm2 save && pm2 startup
```

**Docker**

```dockerfile
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python3", "bot_manager.py", "spawn", "alpha"]
```

```bash
docker build -t discord-agent .
docker run -d --name bot --env-file .env discord-agent
```

Mount `persistence/`, `memories/`, `bots/` and `repository/` as volumes if
the container is meant to keep its memory across rebuilds.

**Backup and restore**

```bash
python deployment_tool.py export                 # config + data to a portable zip
python deployment_tool.py export --exclude logs
python deployment_tool.py import --input backup.zip --dry-run
python deployment_tool.py import --input backup.zip
```

---

## Security

The full policy, including how to report a vulnerability, is in
[SECURITY.md](SECURITY.md). In brief:

- Secrets live in environment variables; `.env` is gitignored, and the
  dashboard writes keys without ever reading them back.
- Vaults and DM privacy are enforced in storage and tool code, not in the
  prompt.
- Rate limits are per channel with preset tiers. After five consecutive
  ignored messages the bot goes quiet in that channel for half an hour;
  @mentions still get answered.
- Web search is capped per request. Code execution runs in Anthropic's
  server-side container, not on your host.
- Deleting a Discord message purges it from the bot's storage and
  attachment pipeline, including messages older than the running process.
- The dashboard API requires a per-install token and refuses foreign `Host`
  and `Origin` headers.

---

## Documentation

- [README.md](README.md): this file.
- [ROADMAP.md](ROADMAP.md): what shipped, what is being built, what comes
  after.
- [CHANGELOG.md](CHANGELOG.md): version history with upgrade and rollback
  notes.
- [DEPLOYMENT.md](DEPLOYMENT.md): development and production installs, the
  release workflow, automatic updates, rolling back.
- [SECURITY.md](SECURITY.md): what is enforced mechanically, what is not,
  and how to report a vulnerability.
- [REDESIGN.md](REDESIGN.md): the v0.6.0 memory-architecture design
  (episodic sessions, context layout). Historical background.
- [NATIVE_MESSAGING.md](NATIVE_MESSAGING.md): the v0.12.0 design for the
  `send_message` tool.
- `docs/archive/`: design documents from earlier versions.

---

## Status

0.13.0 and 0.13.1 are the first two releases of the plan in
[ROADMAP.md](ROADMAP.md): the model layer landed alone so that the request
bytes could be compared with nothing else changing, then per-channel
concurrency and three fixes. Next is 0.14, the workshop: a sandboxed,
per-scope development environment with background jobs and workers.

The codebase is about thirty core modules plus the supervisor package, with
about 800 unit tests. Every release cycle also runs live scenario campaigns
on a real Discord server, because the wire-level bugs that matter most do
not reproduce under mocks; the [CHANGELOG](CHANGELOG.md) records what each
campaign found.

---

## License

No license file yet — all rights reserved until one is added.

---

## Acknowledgments

Built with [Anthropic Claude](https://www.anthropic.com/),
[discord.py](https://discordpy.readthedocs.io/),
[aiohttp](https://docs.aiohttp.org/) and
[aiosqlite](https://github.com/omnilib/aiosqlite).
