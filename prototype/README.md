# SLH v1 Prototype

This is the original working implementation of the SLH-01 Discord bot.

## Running the Prototype

From the project root:

```bash
python prototype/slh.py
```

Make sure you have a `.env` file in the project root with:
- `DISCORD_BOT_TOKEN`
- `ANTHROPIC_API_KEY`

## Architecture

- **slh.py** - Main bot class (1,764 lines)
- **context_manager.py** - Loads user/channel summaries from `server_summaries/`
- **bot/memory.py** - In-memory message storage
- **server_summaries/** - Pre-generated context files (user profiles, channel summaries)

## Key Features

- Agentic conversation participation (decides when to respond)
- Periodic conversation checking (30s intervals)
- Scheduled provocations (1.5h intervals)
- Image processing with compression
- Web search integration
- Multi-layer rate limiting

## Documentation

See [PROTOTYPE_V1_TECHNICAL_SPEC.md](../docs/PROTOTYPE_V1_TECHNICAL_SPEC.md) for complete technical documentation.

## Status

**Working, stable, feature-complete**

This prototype serves as the reference implementation for the v2 framework being developed.
