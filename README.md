# Discord Agents

**Version:** 0.4.0-beta (Closed Beta)

Build intelligent Discord bots that think, remember, and act autonomously. Powered by Anthropic's Claude Sonnet 4.5.

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.3+-blue.svg)](https://discordpy.readthedocs.io/)
[![Security](https://img.shields.io/badge/security-policy-blue.svg)](SECURITY.md)

---

## ⚠️ Beta Notice

This is a beta release. The framework is feature-complete but undergoing community testing.

- **Expect changes** - API and configuration may evolve between beta versions
- **Report issues** - Use [GitHub Issues](../../issues) with provided templates
- **Beta testing** - See [BETA_TESTING.md](BETA_TESTING.md) for guidelines
- **Security** - Report vulnerabilities via [GitHub Security Advisories](SECURITY.md)

---

## What It Does

### Intelligence

Your bot reasons through complex questions with extended thinking, maintains persistent memory per server (Markdown-based), and searches message history using FTS5 full-text indexing. Web search comes with automatic citation extraction. Handles up to 5 images per message through a compression pipeline. It understands time, threads reply chains up to 5 levels deep, and knows when to shut up.

### Autonomy

The bot initiates conversations in idle channels when appropriate, tracks events and follows up naturally, and learns channel success rates to adapt its behavior. Configure quiet hours to avoid late night enthusiasm. Messages are delivered standalone, woven into context, or deferred based on delivery intelligence.

### Production Features

Rate limiting prevents embarrassment (20/min, 100/hour per channel). Web search quota management keeps costs predictable (300/day default). Daily message reindexing runs at 3 AM UTC. API keys stay isolated, environment variables get validated, and comprehensive logging tracks everything including conversation flows. Run multiple bots with isolated configurations because sometimes one personality isn't enough.

**Technical Details:**
- Extended thinking for step-by-step reasoning
- Persistent per-server knowledge storage (Markdown)
- FTS5 full-text message search with agentic retrieval
- Web search via Anthropic server tools with citations
- Multi-image processing (up to 5 per message, 6 compression strategies)
- Temporal awareness with message timestamps
- Reply chain threading (5-level depth)
- Adaptive learning from channel engagement rates
- Configurable quiet hours
- Smart message delivery (standalone/woven/deferred)

---

## Prerequisites

- **Python 3.10+** - [Download](https://www.python.org/downloads/)
- **Discord Bot** - [Create application](https://discord.com/developers/applications)
  - Enable "Message Content Intent" in Bot settings
- **Anthropic API Key** - [Get key](https://console.anthropic.com/)

---

## Quick Start

### 1. Clone Repository

```bash
git clone <repository-url>
cd discord-agents
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

Copy the template and add your API keys:

```bash
cp .env.example .env
```

Edit `.env`:
```bash
ALPHA_BOT_TOKEN=your_discord_bot_token_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

### 4. Configure Your Bot

Copy the template and customize:

```bash
cp bots/alpha.yaml.example bots/alpha.yaml
```

Edit `bots/alpha.yaml`:
```yaml
discord:
  servers:
    - "YOUR_SERVER_ID_HERE"  # Right-click server → Copy ID

personality:
  base_prompt: |
    Customize your bot's personality here.
    Define tone, expertise, behavior preferences.
```

### 5. Run Your Bot

```bash
python bot_manager.py spawn alpha
```

The bot will connect to Discord, backfill message history, start its autonomous background loop, and respond to @mentions.

---

## Deployment (Self-Hosted)

### Option 1: Systemd Service (Linux VPS)

Create `/etc/systemd/system/discord-bot.service`:

```ini
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

Enable and start:
```bash
sudo systemctl enable discord-bot
sudo systemctl start discord-bot
sudo systemctl status discord-bot
```

### Option 2: PM2 (Process Manager)

```bash
pm2 start bot_manager.py --name discord-bot --interpreter python3 -- spawn alpha
pm2 save
pm2 startup  # Follow instructions for auto-start
```

### Option 3: Docker

Create `Dockerfile`:
```dockerfile
FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python3", "bot_manager.py", "spawn", "alpha"]
```

Build and run:
```bash
docker build -t discord-agent .
docker run -d --name bot --env-file .env discord-agent
```

### Option 4: Screen/Tmux (Simple)

```bash
screen -S discord-bot
python bot_manager.py spawn alpha
# Ctrl+A, D to detach
```

---

## Security & Safety

**Full Security Policy:** See [SECURITY.md](SECURITY.md) for complete guidelines and best practices.

### Reporting Security Vulnerabilities

⚠️ **Found a security issue?** Report it responsibly:

- **GitHub Security Advisories** (preferred): [Report vulnerability](../../security/advisories/new)
- **Do NOT** create public GitHub issues for security vulnerabilities
- See [SECURITY.md](SECURITY.md) for detailed reporting instructions

### API Key Management
Never commit `.env` files (already git-ignored). Use environment variables only—no hardcoded keys. Rotate keys immediately if exposed by regenerating Discord token and Anthropic key. For multi-bot setups, consider one key per bot for isolation.

### Rate Limiting
Per-channel limits prevent spam: 20 messages per 5 minutes, 200 per 60 minutes. The bot learns when users are less responsive and backs off accordingly.

### Quota Management
Web search defaults to 300 per day (configurable in bot YAML). Image processing maxes at 5 per message. Tracking data lives in `persistence/{bot}_web_search_stats.json`.

### Resource Cleanup
Graceful shutdown on SIGTERM/SIGINT. Database connections close properly. Background tasks cancel cleanly.

### Memory Isolation
Per-server separation prevents data leakage between communities. Per-channel isolation maintains channel-specific memory contexts. Each server has isolated memory files—no cross-contamination.

**For comprehensive security guidelines, hardening recommendations, and deployment best practices, see [SECURITY.md](SECURITY.md).**

---

## Documentation

- **[README.md](README.md)** - This file: Quick start and overview
- **[BETA_TESTING.md](BETA_TESTING.md)** - Beta testing guide and feedback channels
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - Technical reference and system design
- **[CHANGELOG.md](CHANGELOG.md)** - Version history and planned features
- **[SECURITY.md](SECURITY.md)** - Security policy and vulnerability reporting

---

## Configuration Guide

### Minimal Setup

Just get started:
```yaml
bot_id: mybot
name: "My Bot"

discord:
  token_env_var: "DISCORD_BOT_TOKEN"
  servers: ["YOUR_SERVER_ID"]

personality:
  base_prompt: "Your bot's personality"
```

### Full Configuration

See `bots/alpha.yaml.example` for all options including personality and engagement rates, reactive engine settings, agentic behaviors (proactive, follow-ups), API configuration (thinking, context editing, tools), rate limiting, and logging.

---

## Backup & Restore

### Export Your Bot Data

Create a portable backup of your bot configuration and data:

```bash
# Export everything
python deployment_tool.py export

# Export without logs (smaller backup)
python deployment_tool.py export --exclude logs

# Export to specific location
python deployment_tool.py export --output ~/backups/my-bot.zip
```

This creates a timestamped zip file containing bot configurations (`bots/*.yaml`), environment variables (`.env`), and optionally logs, memories, and persistence data.

### Import on Another Machine

Restore your bot data from a backup:

```bash
# Preview what will be imported (safe, no changes)
python deployment_tool.py import --input backup.zip --dry-run

# Import backup (creates safety backup of existing files first)
python deployment_tool.py import --input backup.zip
```

Portable (USB drive, Dropbox, cloud storage), safe (auto-backup before import), selective (choose what to include), and simple (no git complexity).

---

## Known Issues

See [CHANGELOG.md](CHANGELOG.md#known-issues) for current issues.

**Report bugs:** [GitHub Issues](repository-url/issues)

---

## Project Stats

**Current Version:** 0.4.0-beta (Closed Beta)

**Framework:**
- 12 core modules
- 3 tool integrations (discord, web, image)
- 6 test suites
- 3,000+ lines of code

**Capabilities:**
- Message handling (reactive)
- Autonomous behaviors (proactive)
- Full-text search (FTS5)
- Web search with citations
- Image processing (6 strategies)
- Memory management (Markdown)

---

## License

[Your License Here - e.g., MIT]

---

## Acknowledgments

Built with:
- [Anthropic Claude](https://www.anthropic.com/) - AI foundation
- [discord.py](https://discordpy.readthedocs.io/) - Discord integration
- [aiosqlite](https://github.com/omnilib/aiosqlite) - Async SQLite

---

## Roadmap

### Planned for v0.5.0
- Enhanced analytics dashboard
- Thread and voice channel support
- Performance optimizations
- Community feedback integration

See [CHANGELOG.md](CHANGELOG.md) for detailed version history and planned features.
