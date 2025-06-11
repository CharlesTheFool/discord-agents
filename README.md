# SLH-01 Discord Bot

A sharp-tongued Discord bot powered by Claude 4 Sonnet with vision capabilities, contextual awareness, and natural conversation flow.

## Features

- **Vision Support**: Analyzes images with witty commentary
- **Contextual Awareness**: Remembers server history and user personalities
- **Prompt Caching**: Efficient API usage with Anthropic's caching
- **Natural Conversation**: Smart decision-making about when to respond
- **Rate Limiting**: Adaptive behavior based on channel activity

## Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment:**
   Create a `.env` file with:
   ```
   DISCORD_BOT_TOKEN=your_discord_bot_token
   ANTHROPIC_API_KEY=your_anthropic_api_key
   ```

3. **Enable Web Search:**
   - Go to [Anthropic Console](https://console.anthropic.com) → Settings → Beta tools
   - **Enable "Web search (beta)"** for your API key
   - Without this toggle, Claude will silently ignore web search requests

4. **Run the bot:**
   ```bash
   python slh.py
   ```

## Cost Estimate

~$0.035 per message with comprehensive context and thinking enabled.

## Bot Personality

SLH-01 is a witty, critical robot from the game Station No.5. Responds selectively with sharp observations, technical help, and occasional roasts. Uses lowercase, avoids emoji spam, and varies response patterns.

## File Structure

- `slh.py` - Main bot implementation
- `context_manager.py` - Contextual awareness system
- `bot/memory.py` - Message storage and retrieval
- `server_summaries/` - Pre-analyzed server context data