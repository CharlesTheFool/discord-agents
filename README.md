# Discord-Claude Bot Framework

**Version:** 0.4.0-beta (Closed Beta)

Agentic Discord bot framework powered by Anthropic's Claude Sonnet 4.5. Build intelligent, autonomous bots with memory, proactive engagement, web search, and multi-modal capabilities.

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.3+-blue.svg)](https://discordpy.readthedocs.io/)

---

## 🚀 Features

### ✅ Complete Feature Set (v0.4.0-beta)

**Core Intelligence**
- 🧠 **Extended Thinking** - Step-by-step reasoning for complex questions
- 💾 **Memory Tool** - Persistent per-server knowledge storage (Markdown-based)
- 🔍 **Discord Message Search** - FTS5 full-text indexing with agentic search & view
- 🌐 **Web Search** - Anthropic server tools with automatic citation extraction
- 🖼️ **Image Processing** - 6-strategy compression pipeline, up to 5 images per message
- ⏰ **Temporal Awareness** - Time-sensitive responses with message timestamps
- 🔗 **Smart Context** - Reply chain threading (up to 5 levels deep)

**Autonomous Behaviors**
- 🤖 **Proactive Engagement** - Initiates conversations in idle channels
- 📅 **Follow-Up System** - Auto-track events and check in naturally
- 📊 **Adaptive Learning** - Learns channel success rates and adapts behavior
- 🌙 **Quiet Hours** - Configurable time windows for reduced activity
- 🎯 **Delivery Intelligence** - Standalone, woven, or deferred message delivery

**Production Ready**
- ⚡ **Rate Limiting** - Per-channel limits (20/min, 100/hour)
- 💰 **Quota Management** - Web search daily limits (300/day default)
- 🔄 **Daily Reindexing** - Automatic message reindex at 3 AM UTC
- 🛡️ **Security** - API key isolation, environment variable validation
- 📝 **Comprehensive Logging** - Structured logs with conversation tracking
- 🔧 **Multi-Bot Support** - Run multiple bots with isolated configs

---

## 📋 Prerequisites

- **Python 3.10+** - [Download](https://www.python.org/downloads/)
- **Discord Bot** - [Create application](https://discord.com/developers/applications)
  - Enable "Message Content Intent" in Bot settings
- **Anthropic API Key** - [Get key](https://console.anthropic.com/)
- **Git** (optional, for deployment submodule)

---

## 🎯 Quick Start

### 1. Clone Repository

```bash
git clone <repository-url>
cd discord-claude-framework
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

**The bot will:**
- ✅ Connect to Discord
- ✅ Backfill message history
- ✅ Start autonomous background loop
- ✅ Respond to @mentions

---

## 🚀 Deployment (Self-Hosted)

### Option 1: Systemd Service (Linux VPS)

Create `/etc/systemd/system/discord-bot.service`:

```ini
[Unit]
Description=Discord-Claude Bot
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/discord-claude-framework
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
docker build -t discord-claude-bot .
docker run -d --name bot --env-file .env discord-claude-bot
```

### Option 4: Screen/Tmux (Simple)

```bash
screen -S discord-bot
python bot_manager.py spawn alpha
# Ctrl+A, D to detach
```

---

## 🔒 Security & Safety

### API Key Management
✅ **Never commit `.env` files**
✅ **Use environment variables only**
✅ **Rotate keys immediately if exposed**
✅ **One key per bot (optional isolation)**

### Rate Limiting
✅ **Per-channel limits:** 20 messages/5 min, 200 messages/60 min
✅ **Prevents spam and quota exhaustion**
✅ **Engagement-aware backoff**

### Quota Management
✅ **Web search:** 300/day default (configurable)
✅ **Image processing:** 5 per message max
✅ **Tracked in:** `persistence/{bot}_web_search_stats.json`

### Resource Cleanup
✅ **Graceful shutdown** on SIGTERM/SIGINT
✅ **Database connections** closed properly
✅ **Background tasks** cancelled cleanly

### Memory Isolation
✅ **Per-server separation**
✅ **Per-channel isolation**
✅ **No cross-contamination**

---

## 📚 Documentation

### Main Documentation
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** - Technical reference and system design
- **[CHANGELOG.md](CHANGELOG.md)** - Version history and roadmap
- **[TESTING.md](TESTING.md)** - Test suite documentation

### Reference Documentation
- **[docs/reference/](docs/reference/)** - API references
  - Memory Tool API
  - Context Editing API
  - Discord Patterns
  - Preserved Algorithms

### Historical Documentation
- **[docs/phases/](docs/phases/)** - Development phase documentation
  - Phase 2 Complete (v0.2.0 - Intelligence)
  - Phase 3 Complete (v0.3.0 - Autonomy)
  - Phase 4 Complete (v0.4.0-beta - Tools & Polish)

---

## 🔧 Configuration Guide

### Quick Configuration

**Minimal setup** (just get started):
```yaml
bot_id: mybot
name: "My Bot"

discord:
  token_env_var: "DISCORD_BOT_TOKEN"
  servers: ["YOUR_SERVER_ID"]

personality:
  base_prompt: "Your bot's personality"
```

**Full configuration** - See `bots/alpha.yaml.example` for all options:
- Personality and engagement rates
- Reactive engine settings
- Agentic behaviors (proactive, follow-ups)
- API configuration (thinking, context editing, tools)
- Rate limiting
- Logging

---

## 📦 Backup & Restore

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

This creates a timestamped zip file containing:
- Bot configurations (`bots/*.yaml`)
- Environment variables (`.env`)
- Logs (optional)
- Memories (optional)
- Persistence data (optional)

### Import on Another Machine

Restore your bot data from a backup:

```bash
# Preview what will be imported (safe, no changes)
python deployment_tool.py import --input backup.zip --dry-run

# Import backup (creates safety backup of existing files first)
python deployment_tool.py import --input backup.zip
```

**Benefits:**
- ✅ Portable (USB drive, Dropbox, cloud storage)
- ✅ Safe (auto-backup before import)
- ✅ Selective (choose what to include)
- ✅ Simple (no git complexity)

---

## 🐛 Known Issues

See [CHANGELOG.md](CHANGELOG.md#known-issues) for current issues.

**Report bugs:** [GitHub Issues](repository-url/issues)

---

## 📊 Project Stats

**Current Version:** 0.4.0-beta (Closed Beta)

**Framework Components:**
- 12 core modules
- 3 tool integrations (discord, web, image)
- 6 test suites
- 3,000+ lines of code

**Bot Capabilities:**
- Message handling (reactive)
- Autonomous behaviors (proactive)
- Full-text search (FTS5)
- Web search with citations
- Image processing (6 strategies)
- Memory management (Markdown)

---

## 📝 License

[Your License Here - e.g., MIT]

---

## 🙏 Acknowledgments

Built with:
- [Anthropic Claude](https://www.anthropic.com/) - AI foundation
- [discord.py](https://discordpy.readthedocs.io/) - Discord integration
- [aiosqlite](https://github.com/omnilib/aiosqlite) - Async SQLite

---

## ⚠️ Closed Beta Disclaimer

This is a **beta release**. Expect changes. API may evolve. Configuration format may change between versions. Use in production at your own risk.

For questions or feedback: [Your Contact]

---

## 🔮 Roadmap

### Planned for v0.5.0
- Enhanced analytics dashboard
- Thread and voice channel support
- Performance optimizations
- Community feedback integration

See [CHANGELOG.md](CHANGELOG.md) for detailed version history and planned features.
