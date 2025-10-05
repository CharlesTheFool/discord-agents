# Discord-Claude Bot Framework

Generalized framework for building agentic Discord bots powered by Claude AI.

## Project Structure

```
discord-claude-framework/
├── core/                      # Core framework components
│   ├── config.py             # Configuration system
│   ├── rate_limiter.py       # Rate limiting (preserved algorithm)
│   ├── message_memory.py     # SQLite message storage
│   ├── memory_manager.py     # Memory tool wrapper
│   ├── memory_tool_executor.py # Client-side memory tool operations (Phase 2)
│   ├── context_builder.py    # Smart context assembly (Phase 2)
│   ├── reactive_engine.py    # Message handling engine
│   ├── agentic_engine.py     # Autonomous behaviors (Phase 3)
│   ├── proactive_action.py   # Proactive action data class (Phase 3)
│   ├── conversation_logger.py # Enhanced logging
│   └── discord_client.py     # Discord.py integration
│
├── tools/                     # Tool implementations
│
├── bots/                      # Bot configurations
│   ├── alpha.yaml            # Example bot config
│   └── beta.yaml             # Second bot for testing
│
├── memories/                  # Memory tool storage (git committed)
│   └── {bot_id}/
│       └── servers/...
│
├── persistence/               # SQLite databases (git committed)
│   └── {bot_id}_messages.db
│
├── logs/                      # Bot logs (git committed)
│   ├── {bot_id}.log
│   └── {bot_id}_conversations.log
│
├── prototype/                 # v1 prototype (reference only)
│   └── slh.py
│
├── docs/                      # Documentation
│   ├── PROJECT_SPEC.md       # Complete framework specification
│   ├── PHASE_2_COMPLETE.md   # Phase 2 completion summary
│   ├── PHASE_3_COMPLETE.md   # Phase 3 completion summary
│   ├── api_memory_tool.md
│   ├── api_context_editing.md
│   ├── discord_patterns.md
│   └── preserved_algorithms.md
│
├── bot_manager.py             # CLI entry point
├── requirements.txt
├── .env.example
└── README.md
```

## Quick Start (Phase 1)

### 1. Setup Environment

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
# Edit .env and add your DISCORD_BOT_TOKEN and ANTHROPIC_API_KEY
```

Required keys:
- `DISCORD_BOT_TOKEN` - Get from [Discord Developer Portal](https://discord.com/developers/applications)
- `ANTHROPIC_API_KEY` - Get from [Anthropic Console](https://console.anthropic.com/)

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Your Bot

Edit `bots/alpha.yaml`:

```yaml
discord:
  servers:
    - "YOUR_SERVER_ID_HERE"  # Replace with your Discord server ID

personality:
  base_prompt: |
    # Customize your bot's personality here
```

### 4. Run Your Bot

```bash
python bot_manager.py spawn alpha
```

The bot will connect to Discord and respond to @mentions!

## Features

✅ **Phase 1: Foundation Complete**
- Responds to @mentions with Claude Sonnet 4.5
- Extended thinking integration (step-by-step reasoning)
- SQLite message storage
- Rate limiting with engagement tracking
- Multi-bot support via YAML configs
- Git-friendly state management

✅ **Phase 2: Intelligence Complete**
- Smart context building with reply chain threading (up to 5 levels)
- Memory tool integration (all 6 official commands)
- @Mention resolution to readable names
- Temporal awareness (current time + message timestamps)
- Bot identity awareness (knows its Discord name)
- Loose engagement detection (any user message counts)
- Context editing for token efficiency (full integration)

✅ **Phase 3: Autonomy Complete**
- Agentic engine with hourly background loop
- Follow-up system (auto-track user events, natural check-ins)
- Proactive engagement (initiates conversations in idle channels)
- Adaptive learning (learns channel success rates)
- Memory maintenance (cleanup old follow-ups)
- Configurable quiet hours and rate limits
- Delivery method intelligence (standalone/woven/deferred)

🚧 **Coming in Phase 4**
- Image processing
- Web search
- Discord tools (query server state)
- Production deployment

## Documentation

- **[Project Specification](docs/PROJECT_SPEC.md)** - Complete framework architecture
- **[Memory Tool API](docs/api_memory_tool.md)** - Anthropic memory tool reference
- **[Context Editing API](docs/api_context_editing.md)** - Token management reference
- **[Discord Patterns](docs/discord_patterns.md)** - Discord.py patterns and examples
- **[Preserved Algorithms](docs/preserved_algorithms.md)** - Battle-tested algorithms from v1
- **[Beta Features Tracking](docs/BETA_FEATURES_TRACKING.md)** - Beta API features in use and migration guide

## Current Status

**Phase 1: Foundation** - ✅ Complete (2025-09-30)
- Bot connects to Discord
- Responds to @mentions
- Stores messages in SQLite
- Extended thinking enabled
- Rate limiting works

**Phase 2: Intelligence** - ✅ Complete (2025-10-04)
- Smart context building with reply chains
- Memory tool (6 commands: view, create, str_replace, insert, delete, rename)
- Temporal awareness (timestamps + current time)
- Bot identity awareness
- Loose engagement detection
- Context editing fully integrated

**Phase 3: Autonomy** - ✅ Complete (2025-10-04)
- AgenticEngine with background loop
- Follow-up system (tracking + natural check-ins)
- Proactive engagement (idle channel detection)
- Adaptive learning (channel success rates)
- Memory maintenance tasks
- Complete configuration system

**Phase 4: Tools & Polish** - 🔮 Future
- Image processing
- Web search
- Production ready

## Development

This is a generalized framework converted from a working prototype. The `prototype/` folder contains the original v1 bot for reference. Phase 1 establishes the foundation - core infrastructure that future phases will build upon.
