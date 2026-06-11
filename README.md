# Discord Agents

**Version:** 0.9.0 (Pre-release Beta)

Build intelligent Discord bots that think, remember, and act autonomously. Powered by Claude.

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.3+-blue.svg)](https://discordpy.readthedocs.io/)
[![Security](https://img.shields.io/badge/security-policy-blue.svg)](SECURITY.md)
[![Status](https://img.shields.io/badge/status-beta-yellow.svg)](CHANGELOG.md)

---

## ⚠️ Beta Notice

This is a beta release. The framework is feature-complete and validated through
live scenario testing, but APIs and configuration may still evolve between beta
versions. Report issues via [GitHub Issues](../../issues); report security
vulnerabilities via [GitHub Security Advisories](SECURITY.md).

---

## What It Does

### Two engines, one bot

A **reactive engine** answers @mentions immediately and scans ongoing
conversation for moments worth joining — with the judgment to stay quiet when
it has nothing to add. An **agentic engine** runs in the background: it
initiates conversation in idle channels, remembers to follow up on things
people mentioned ("how did the presentation go?"), and learns per-channel
engagement rates so it backs off where it isn't wanted.

### Memory that survives

- **Episodic sessions** — when a conversation session grows past its token
  threshold or goes idle, the bot distills it into a titled, timestamped
  episode file and reseeds its live context. The archive stays searchable
  through the bot's memory tool, so "what did we decide last Tuesday?" works
  weeks later.
- **One person, one profile** — user profiles are global (one file per
  human, keyed by Discord ID) with per-claim origin tags, so the bot knows
  the same person across every server while keeping what it learned where
  it learned it.
- **Long-term memory** — Markdown files (profiles, channel notes, server
  culture) the bot reads and writes itself; a weekly reconsolidation pass
  (Batches API, half-price tokens) compacts old episodes into era digests
  and rewrites profiles from evidence.
- **Server induction** — point the bot at a server with history and
  `python bot_manager.py induct` distills the stored backlog into starting
  memory, explicitly framed as observations rather than lived experience
  (`--dry-run` prints a cost table first).
- **Message history** — SQLite with FTS5 full-text search; the bot can search
  and quote its own channel history on demand.
- **Attachments** — images, documents, spreadsheets, and code files are
  indexed and retrievable: the bot can pull any past attachment back into
  context with its `get_attachment` tool.
- **File repository** — a per-server local drive you can edit by hand and
  the bot manages with its `repository` tool; files survive Discord's CDN
  expiry.
- **DM memory** — DMs are a first-class memory surface: notes and episodes
  live in a global per-person tree (`global/dms/`) that belongs to the
  person, not to any server, and is mechanically invisible from everywhere
  else.
- **/memory commands** — in a DM, `/memory show` returns your profile
  verbatim; `/memory remember`, `forget`, and `feedback` let you teach or
  correct the bot directly, through a one-shot grant that opens exactly
  your own profile file for that turn.

### One mind across servers

A bot in several servers is one mind with separate rooms — and v0.9 gives
the rooms a hallway. The `ask_prime` tool lets the bot in one server ask
"the Prime" (itself, above all its servers) to pose a question or announce
something in another server. Mechanical gates run before any model
judgment: vault boundaries in both directions, daily budgets, DM refusal.
Approved messages are delivered by the target server's bot in its own
voice. A **standing watch** can ride along: the hourly loop watches the
target channel for an answer (one cheap model call per check) and relays
it back — "relayed via Prime, from {server}" — durably, into the asking
channel. DMs sit above this entirely: a DM is a conversation with the
Prime itself.

### Capabilities

- **Skills + code execution** — drop `.zip` skill packages into `/skills/`;
  Anthropic's built-in document skills (xlsx, pptx, docx, pdf) are included.
  Files the bot creates in its sandbox (decks, charts, exports) attach
  directly to its Discord reply.
- **Web search** with automatic citations.
- **MCP integration** — connect remote MCP servers; their tools are
  auto-discovered and available to the bot.
- **Vision** — image attachments are processed and understood in context.
- **Every surface** — text channels, threads (active and archived, with
  memory nested under the parent channel), voice-channel text chats, and
  private DMs; the bot always knows what kind of room it's in.

### Operator surface (v0.9)

- **Supervisor daemon** — `python supervisor.py` runs a separate
  process-manager + localhost API (127.0.0.1:8642): start/stop/restart
  bots, crash recovery with exponential backoff, and a full read surface
  over every bot's artifacts (status, memory, logs, events). It reads
  files, never imports bot internals; every file route is path-jailed.
- **Dashboard** — served by the daemon at `/`: a fleet board and per-bot
  pages — Monitor (engine gauges, live-context fill, and a channel
  monitor that shows the bot's *x-ray stream*: what it read, what it
  thought, which tools it called, what it said or why it stayed quiet),
  Configure (the YAML as a form, validated by the same code the bot boots
  with), Integrations (skill toggles, MCP health), Memories (browse and
  edit), Repository.
- **Desktop app** — an Electron shell packaged as a Windows installer:
  attaches to a running daemon or spawns one, opens the dashboard in a
  window.
- **Events substrate** — every bot turn (mention, DM, scan, *stayed
  silent*, proactive, follow-up, memory write, relay, watch, reseed)
  writes one structured row; the dashboard and the conversations log both
  read from it.

### Production posture

- Per-channel rate-limit presets with engagement-aware backoff
- Prompt-cache-aware request layout (a few hundred uncached tokens per turn
  in steady state — the conversation prefix stays cached)
- Persistent conversation state across restarts; crash detection and catch-up
- **Vaults** — mark any channel or server and its content never leaves it:
  excluded from outside search and attachments, memory files sealed
- **DM privacy is mechanical** — what's said in a DM is visible only inside
  that DM, enforced at the search, viewing, and attachment layers
- Multiple isolated bots from one codebase (separate configs, databases,
  memory trees, and logs)

---

## Prerequisites

- **Python 3.10+** — [Download](https://www.python.org/downloads/)
- **Discord Bot** — [Create application](https://discord.com/developers/applications)
  - Enable "Message Content Intent" in Bot settings
- **Anthropic API Key** — [Get key](https://console.anthropic.com/)
  - Defaults to `claude-sonnet-4-6`. Any Claude 4-family model works; the
    optional `api.effort` setting requires an effort-capable model (validated
    at startup).

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/CharlesTheFool/discord-agents.git
cd discord-agents
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```bash
ALPHA_BOT_TOKEN=your_discord_bot_token_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

### 3. Configure your bot

```bash
cp bots/alpha.yaml.example bots/alpha.yaml
```

Edit `bots/alpha.yaml`:

```yaml
discord:
  servers:
    - "YOUR_SERVER_ID_HERE"   # Right-click server → Copy ID (Developer Mode)
  timezone: "UTC"              # IANA format: America/New_York, Europe/London, ...

personality:
  base_prompt: |
    Your bot's character AND behavior style — tone, when to engage,
    formatting preferences. This prompt is the main behavioral control.
```

### 4. Run

```bash
python bot_manager.py spawn alpha
```

The bot connects, backfills message history in the background, starts its
autonomous loop, and responds to @mentions.

### 5. (Optional) Run the dashboard

```bash
python supervisor.py            # http://127.0.0.1:8642
```

Manage bots, watch the channel monitor, edit memories and config from the
browser — or install the desktop app from the release assets, which opens
the same dashboard in its own window.

---

## Configuration

v0.6.0 deliberately keeps configuration small (~30 user-facing settings).
Behavioral style lives in the personality prompt; operational tuning uses
presets:

```yaml
reactive:
  rate_limit: "moderate"        # strict | moderate | permissive | unlimited

agentic:
  proactive:
    enabled: true
    intensity: "moderate"       # gentle | moderate | active
    quiet_hours: [0, 1, 2, 3, 4, 5, 6]   # LOCAL host-clock hours

api:
  model: "claude-sonnet-4-6"
  context_messages: 30          # Rolling window of Discord messages
  context_tokens: 80000         # Session threshold: episodize + reseed past this
  effort: "medium"              # Optional cost/depth dial (low|medium|high|max)
```

See `bots/alpha.yaml.example` for the complete annotated reference, and
`CHANGELOG.md` → *Upgrading from 0.4.x* if you're migrating an existing
config.

---

## Deployment (Self-Hosted)

### Systemd (Linux VPS)

```ini
# /etc/systemd/system/discord-bot.service
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

```bash
sudo systemctl enable --now discord-bot
```

### PM2

```bash
pm2 start bot_manager.py --name discord-bot --interpreter python3 -- spawn alpha
pm2 save && pm2 startup
```

### Docker

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

### Screen/Tmux (simple)

```bash
screen -S discord-bot
python bot_manager.py spawn alpha    # Ctrl+A, D to detach
```

---

## Backup & Restore

```bash
# Export configuration + data to a portable zip
python deployment_tool.py export
python deployment_tool.py export --exclude logs

# Import on another machine (preview first)
python deployment_tool.py import --input backup.zip --dry-run
python deployment_tool.py import --input backup.zip
```

---

## Security & Safety

**Full policy:** [SECURITY.md](SECURITY.md) — including vulnerability
reporting via [GitHub Security Advisories](../../security/advisories/new)
(please don't open public issues for security problems).

- **API keys** live in environment variables only; `.env` is git-ignored.
  Rotate immediately if exposed.
- **Rate limiting** is per-channel with preset tiers; the bot also silences
  itself after consecutive ignored messages.
- **Web search** is capped per request; code execution runs in Anthropic's
  sandbox, not on your host.
- **Vaults** mechanically seal a channel's or server's content (search,
  memory, attachments, repository) — validated in both directions by live
  testing; a mandatory discretion prompt handles everything finer.
- **DM privacy** is enforced in the storage layer, not just the prompt: DM
  messages and files never surface outside their own DM.
- **Deletion handling** — deleting a Discord message purges it from the bot's
  storage and attachment pipeline, including messages older than the current
  process.
- **Graceful shutdown** on SIGTERM/SIGINT; database connections close cleanly.

---

## Documentation

- **[README.md](README.md)** — this file: quick start and overview
- **[ROADMAP.md](ROADMAP.md)** — where the project is headed
- **[CHANGELOG.md](CHANGELOG.md)** — version history and upgrade notes
- **[DEPLOYMENT.md](DEPLOYMENT.md)** — running dev and production installs
  side by side, and the release/update workflow
- **[REDESIGN.md](REDESIGN.md)** — the v0.6.0 architecture design document
  (episodic sessions, context layout)
- **[SECURITY.md](SECURITY.md)** — security policy
- `docs/archive/` — historical design documents from earlier versions

---

## Project Stats

- ~30 core modules + the supervisor package, 595 unit tests
- Validated by live scenario campaigns on a real Discord server: narrative
  scenarios, pre-release code audits, and release stress tests every cycle —
  including wire-level bugs that mocked tests structurally can't catch (see
  CHANGELOG)

---

## License

No license file yet — all rights reserved until one is added.

---

## Acknowledgments

Built with:

- [Anthropic Claude](https://www.anthropic.com/) — AI foundation
- [discord.py](https://discordpy.readthedocs.io/) — Discord integration
- [aiosqlite](https://github.com/omnilib/aiosqlite) — async SQLite

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) — the 0.x roadmap is complete; next up:
**1.0** (polish).

See [CHANGELOG.md](CHANGELOG.md) for detailed version history.
