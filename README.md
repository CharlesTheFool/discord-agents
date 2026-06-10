# Discord Agents

**Version:** 0.6.1 (Pre-release Beta)

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
- **Long-term memory** — per-server Markdown files (user profiles, channel
  notes, server culture) the bot reads and writes itself.
- **Message history** — SQLite with FTS5 full-text search; the bot can search
  and quote its own channel history on demand.
- **Attachments** — images, documents, spreadsheets, and code files are
  indexed and retrievable: the bot can pull any past attachment back into
  context with its `get_attachment` tool.

### Capabilities

- **Skills + code execution** — drop `.zip` skill packages into `/skills/`;
  Anthropic's built-in document skills (xlsx, pptx, docx, pdf) are included.
  Files the bot creates in its sandbox (decks, charts, exports) attach
  directly to its Discord reply.
- **Web search** with automatic citations.
- **MCP integration** — connect remote MCP servers; their tools are
  auto-discovered and available to the bot.
- **Vision** — image attachments are processed and understood in context.

### Production posture

- Per-channel rate-limit presets with engagement-aware backoff
- Prompt-cache-aware request layout (measured ~330 uncached tokens per turn in
  steady state — the conversation prefix stays cached)
- Persistent conversation state across restarts; crash detection and catch-up
- Optional data isolation (`server` or `channel` scope) for multi-tenant use
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
- **Data isolation** (optional) scopes memory, search, and Discord tools to
  the current server or channel — validated in both directions by live
  testing.
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

- ~25 core modules, 216 unit tests
- Validated by live scenario campaigns on a real Discord server: six narrative
  scenarios, a full pre-release code audit, and a release stress test — 54
  bugs and findings fixed across three hardening passes (see CHANGELOG)

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

See [ROADMAP.md](ROADMAP.md) — next up: **0.7.0** (one continuous identity
across servers, with vaults and discretion; memory reconsolidation), then
induction, new surfaces, fleet coordination, voice, and a desktop app on the
road to 0.9.

See [CHANGELOG.md](CHANGELOG.md) for detailed version history.
