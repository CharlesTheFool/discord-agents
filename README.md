# SLH Discord Bot

Agentic Discord bot powered by Claude AI.

## Project Structure

```
SLH/
├── prototype/              # v1 prototype implementation
│   ├── slh.py             # Main bot (run this)
│   ├── context_manager.py # Context loading module
│   ├── bot/               # Bot modules
│   │   └── memory.py      # Message memory system
│   └── server_summaries/  # User/channel context files
│
├── docs/                   # Documentation
│   └── PROTOTYPE_V1_TECHNICAL_SPEC.md
│
├── .env.example           # Configuration template
├── .gitignore
└── README.md
```

## Quick Start

### 1. Setup Environment

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

Required keys:
- `DISCORD_BOT_TOKEN` - Your Discord bot token
- `ANTHROPIC_API_KEY` - Your Anthropic API key

### 2. Install Dependencies

```bash
pip install discord.py anthropic python-dotenv aiohttp pillow
```

### 3. Run the Prototype Bot

```bash
python prototype/slh.py
```

## Documentation

- **[Prototype Technical Specification](docs/PROTOTYPE_V1_TECHNICAL_SPEC.md)** - Complete technical documentation for v1 implementation

## Current Status

**v1 Prototype:** Stable, feature-complete
- Agentic conversation participation
- Multi-modal support (images, web search)
- Context-aware responses
- Rate limiting and cost controls

**v2 Framework:** In development
- Modular architecture for multiple bot types
- Improved configuration system
- Enhanced state management

## Development

This project is transitioning from a prototype to a generalized framework for agentic Discord bots. The `prototype/` folder contains the working v1 implementation that serves as reference for the new architecture.
