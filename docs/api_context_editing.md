# Context Editing - API Reference

**Source:** https://docs.claude.com/en/docs/build-with-claude/context-editing

**Beta Feature:** Requires `context-management-2025-06-27` beta header

---

## Overview

Context editing automatically manages conversation length by clearing old content when context grows beyond a threshold. This prevents token bloat in long conversations while preserving recent, relevant information.

### Key Benefits

- **Automatic Token Management:** No manual conversation pruning
- **Cost Control:** Prevent runaway token costs in long conversations
- **Preserve Recent Context:** Keep most relevant information
- **Tool-Aware:** Selectively clear tool results vs tool calls

---

## How It Works

The `clear_tool_uses_20250919` strategy clears tool results chronologically when conversation context exceeds your configured threshold:

1. **Trigger:** When token count exceeds threshold (default: 100,000)
2. **Clear:** Oldest tool results removed first
3. **Placeholder:** Removed content replaced with placeholder text
4. **Preserve:** Recent tool results kept (default: last 3)

### What Gets Cleared

**By default:**
- Tool results (the output from tool executions)
- Oldest results cleared first (chronological order)

**Optional:**
- Tool calls (the input parameters Claude used)
- Set `clear_tool_inputs: true` to clear both

### Placeholder Text

When content is cleared, Claude sees:
```
[Tool result was cleared to manage context length]
```

This lets Claude know information was removed without disrupting conversation flow.

---

## Supported Models

- Claude Opus 4.1 (`claude-opus-4-1-20250805`)
- Claude Opus 4 (`claude-opus-4-20250514`)
- Claude Sonnet 4.5 (`claude-sonnet-4-5-20250929`)
- Claude Sonnet 4 (`claude-sonnet-4-20250514`)

---

## Basic Usage

### Simplest Configuration

Enable with defaults (trigger at 100k tokens, keep last 3 tool uses):

```python
from anthropic import Anthropic

client = Anthropic(api_key="your-api-key")

response = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    betas=["context-management-2025-06-27"],  # Required beta header
    context_management={
        "clear_tool_uses_20250919": {}  # Use all defaults
    },
    messages=[...],
    max_tokens=4096
)
```

### With Custom Threshold

```python
response = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    betas=["context-management-2025-06-27"],
    context_management={
        "clear_tool_uses_20250919": {
            "trigger": {
                "input_tokens": 150000  # Clear when exceeding 150k tokens
            }
        }
    },
    messages=[...],
    max_tokens=4096
)
```

---

## Advanced Configuration

### Full Configuration Example

```python
response = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    betas=["context-management-2025-06-27"],
    context_management={
        "clear_tool_uses_20250919": {
            # When to start clearing
            "trigger": {
                "input_tokens": 100000  # or use "tool_uses": 50
            },
            
            # How many recent tool uses to preserve
            "keep": {
                "tool_uses": 3  # Keep last 3 tool use/result pairs
            },
            
            # Minimum tokens to clear (cache efficiency)
            "clear_at_least": {
                "input_tokens": 10000  # Clear at least 10k tokens
            },
            
            # Tools whose results should never be cleared
            "exclude_tools": ["memory", "important_data"],
            
            # Whether to clear tool inputs (calls) as well
            "clear_tool_inputs": False  # Default: only clear results
        }
    },
    messages=[...],
    max_tokens=4096
)
```

---

## Configuration Options

### `trigger` - When to Start Clearing

Defines the threshold for activating context editing.

**By Input Tokens (Recommended):**
```python
"trigger": {
    "input_tokens": 100000  # Default
}
```

**By Tool Use Count:**
```python
"trigger": {
    "tool_uses": 50  # Start clearing after 50 tool uses
}
```

**Note:** Choose based on your use case:
- Token-based: Better for controlling API costs
- Tool-use-based: Better for limiting conversation length

---

### `keep` - Preserve Recent Context

Controls how many recent tool use/result pairs to preserve after clearing.

```python
"keep": {
    "tool_uses": 3  # Default: Keep last 3 tool interactions
}
```

**Why it matters:**
- Too low (1-2): May lose important recent context
- Too high (10+): Less aggressive clearing, context grows faster
- Recommended: 3-5 for most use cases

---

### `clear_at_least` - Cache Efficiency

Ensures minimum tokens cleared to make cache invalidation worthwhile.

```python
"clear_at_least": {
    "input_tokens": 10000  # Clear at least 10k tokens
}
```

**Why it matters:**
Context editing invalidates cached prompt prefixes. If you're using [prompt caching](https://docs.claude.com/en/docs/build-with-claude/prompt-caching), you'll incur cache write costs each time content is cleared.

**Recommendation:**
- Set to ~10-20% of trigger threshold
- Higher values = less frequent cache invalidation
- Lower values = more aggressive clearing

**Example:**
```python
# Conservative: Less frequent cache invalidation
"trigger": {"input_tokens": 100000},
"clear_at_least": {"input_tokens": 20000}  # Clear 20k+ each time

# Aggressive: More frequent clearing
"trigger": {"input_tokens": 100000},
"clear_at_least": {"input_tokens": 5000}   # Clear 5k+ each time
```

---

### `exclude_tools` - Preserve Important Tools

List of tool names whose results should never be cleared.

```python
"exclude_tools": ["memory", "database_query", "user_auth"]
```

**Use cases:**
- **Memory tool:** Preserve memory operations for continuity
- **Authentication:** Keep user session data
- **Critical data:** Database queries with important results

**Example:**
```python
context_management={
    "clear_tool_uses_20250919": {
        "trigger": {"input_tokens": 100000},
        "keep": {"tool_uses": 3},
        "exclude_tools": ["memory", "search_documents"]
    }
}
```

**Note:** Excluded tools still count toward the trigger threshold, but their results won't be cleared.

---

### `clear_tool_inputs` - Clear Tool Calls

Controls whether tool call parameters are cleared along with results.

```python
"clear_tool_inputs": True  # Default: False
```

**False (default):** Only clear tool results
```
User: "Search for Python tutorials"
Assistant: [uses search tool with query "Python tutorials"]
Tool Result: [cleared]
```
Claude still sees it made a search, just not the results.

**True:** Clear both tool calls and results
```
User: "Search for Python tutorials"
Assistant: [tool use cleared]
Tool Result: [cleared]
```
Claude sees placeholder for entire interaction.

**Recommendation:**
- Use `False` for better context continuity
- Use `True` for maximum token reduction

---

## Response Format

### Context Management Field

The API response includes a `context_management` field with clearing statistics:

```python
response = client.messages.create(...)

# Check what was cleared
if hasattr(response, 'context_management'):
    stats = response.context_management
    print(f"Tool uses cleared: {stats.tool_uses_cleared}")
    print(f"Input tokens cleared: {stats.input_tokens_cleared}")
    print(f"Original input tokens: {stats.original_input_tokens}")
```

**Example response:**
```json
{
  "id": "msg_...",
  "type": "message",
  "content": [...],
  "context_management": {
    "tool_uses_cleared": 12,
    "input_tokens_cleared": 15234,
    "original_input_tokens": 125000
  },
  "usage": {
    "input_tokens": 109766,  // After clearing
    "output_tokens": 523
  }
}
```

### Streaming Response

Context management info appears in `message_delta` event:

```python
with client.messages.stream(
    model="claude-sonnet-4-5-20250929",
    betas=["context-management-2025-06-27"],
    context_management={
        "clear_tool_uses_20250919": {}
    },
    messages=[...],
    max_tokens=4096
) as stream:
    for event in stream:
        if event.type == "message_delta":
            if hasattr(event, 'context_management'):
                print(f"Cleared {event.context_management.tool_uses_cleared} tool uses")
```

---

## Token Counting

Preview how many tokens will be used after context editing:

```python
# Count tokens with context editing applied
token_count = client.messages.count_tokens(
    model="claude-sonnet-4-5-20250929",
    betas=["context-management-2025-06-27"],
    context_management={
        "clear_tool_uses_20250919": {
            "trigger": {"input_tokens": 100000},
            "keep": {"tool_uses": 3}
        }
    },
    messages=[...]
)

print(f"Input tokens (after clearing): {token_count.input_tokens}")
print(f"Original input tokens: {token_count.original_input_tokens}")
```

**Response:**
```json
{
  "input_tokens": 89432,        // After context editing
  "original_input_tokens": 125000  // Before context editing
}
```

---

## Interaction with Prompt Caching

### Cache Invalidation

Context editing modifies the prompt structure, which **breaks cache hits** because the content no longer matches cached prefixes.

**What happens:**
1. Context editing triggers
2. Tool results cleared from prompt
3. Prompt structure changes
4. Cache invalidated
5. New cache written (incurs write costs)
6. Subsequent requests can use new cache

### Cost Implications

```python
# Without context editing
Request 1: 100k tokens → Cache WRITE (expensive)
Request 2: 110k tokens → Cache HIT (cheap)
Request 3: 120k tokens → Cache HIT (cheap)

# With context editing (trigger at 100k)
Request 1: 90k tokens  → Cache WRITE
Request 2: 110k tokens → Context edit → 95k tokens → Cache WRITE
Request 3: 105k tokens → Context edit → 90k tokens → Cache WRITE
```

### Optimization Strategy

Use `clear_at_least` to make cache invalidation worthwhile:

```python
context_management={
    "clear_tool_uses_20250919": {
        "trigger": {"input_tokens": 100000},
        "clear_at_least": {"input_tokens": 15000},  # Clear significant amount
        "keep": {"tool_uses": 3}
    }
}
```

**Formula:**
```
clear_at_least ≈ cache_write_cost / cache_read_savings

Example:
- Cache write: 25% more expensive than regular tokens
- Cache read: 90% cheaper than regular tokens
- Sweet spot: Clear 15-20% of trigger threshold
```

---

## Best Practices

### 1. Choose Appropriate Trigger

```python
# Short conversations (customer support)
"trigger": {"input_tokens": 50000}

# Medium conversations (coding assistant)
"trigger": {"input_tokens": 100000}  # Default, recommended

# Long conversations (research, analysis)
"trigger": {"input_tokens": 150000}
```

### 2. Preserve Critical Tools

```python
# Always preserve memory and auth tools
"exclude_tools": ["memory", "user_auth", "session_data"]
```

### 3. Balance Keep vs Clear

```python
# Aggressive clearing (cheaper, less context)
"keep": {"tool_uses": 2},
"clear_at_least": {"input_tokens": 5000}

# Conservative (more context, higher cost)
"keep": {"tool_uses": 5},
"clear_at_least": {"input_tokens": 20000}
```

### 4. Monitor Context Management Stats

```python
def log_context_stats(response):
    """Track clearing statistics for optimization"""
    if hasattr(response, 'context_management'):
        stats = response.context_management
        
        # Log metrics
        logger.info(f"Context cleared: {stats.tool_uses_cleared} tool uses")
        logger.info(f"Tokens cleared: {stats.input_tokens_cleared}")
        logger.info(f"Original tokens: {stats.original_input_tokens}")
        
        # Alert if clearing too frequently
        clearing_rate = stats.tool_uses_cleared / (stats.tool_uses_cleared + keep_count)
        if clearing_rate > 0.7:
            logger.warning("Clearing >70% of tool uses - consider higher trigger")
```

### 5. Combine with Prompt Caching

```python
# Optimal configuration for cached conversations
response = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    betas=["context-management-2025-06-27", "prompt-caching-2024-07-31"],
    
    # Context editing
    context_management={
        "clear_tool_uses_20250919": {
            "trigger": {"input_tokens": 100000},
            "clear_at_least": {"input_tokens": 15000},  # Make cache break worthwhile
            "keep": {"tool_uses": 3},
            "exclude_tools": ["memory"]
        }
    },
    
    # Prompt caching
    system=[
        {
            "type": "text",
            "text": "System prompt here...",
            "cache_control": {"type": "ephemeral"}
        }
    ],
    
    messages=[...]
)
```

---

## Use Cases

### Long-Running Conversations

**Scenario:** Customer support bot with multi-hour conversations.

```python
# Configuration
context_management={
    "clear_tool_uses_20250919": {
        "trigger": {"input_tokens": 80000},    # Lower trigger
        "keep": {"tool_uses": 5},              # Keep more recent context
        "clear_at_least": {"input_tokens": 10000},
        "exclude_tools": ["user_profile", "order_lookup"]  # Preserve user data
    }
}
```

### High Tool Usage

**Scenario:** Coding assistant making many tool calls.

```python
# Configuration
context_management={
    "clear_tool_uses_20250919": {
        "trigger": {"tool_uses": 30},          # Trigger by tool count
        "keep": {"tool_uses": 3},              # Keep only recent results
        "clear_at_least": {"input_tokens": 5000},
        "clear_tool_inputs": True              # Clear both calls and results
    }
}
```

### Memory-Intensive Agent

**Scenario:** Agent with memory tool, needs to preserve memories.

```python
# Configuration
context_management={
    "clear_tool_uses_20250919": {
        "trigger": {"input_tokens": 120000},   # Higher trigger
        "keep": {"tool_uses": 5},
        "exclude_tools": ["memory"],           # NEVER clear memory operations
        "clear_tool_inputs": False             # Keep tool call history
    }
}
```

---

## Troubleshooting

### Context Not Being Cleared

**Problem:** Tokens keep growing despite context editing enabled.

**Possible causes:**
1. **Threshold not reached:** Check `original_input_tokens` in response
2. **All tools excluded:** `exclude_tools` preventing clearing
3. **Beta header missing:** Requires `context-management-2025-06-27`

**Solution:**
```python
# Log to verify clearing
if hasattr(response, 'context_management'):
    print(f"Cleared: {response.context_management.tool_uses_cleared}")
else:
    print("Context management not active - check beta header")
```

### Excessive Cache Writes

**Problem:** High costs from frequent cache invalidation.

**Solution:** Increase `clear_at_least` threshold:
```python
# Before (frequent small clears)
"clear_at_least": {"input_tokens": 3000}

# After (less frequent, larger clears)
"clear_at_least": {"input_tokens": 20000}
```

### Lost Critical Context

**Problem:** Important information being cleared too early.

**Solution:**
1. Add tools to `exclude_tools`
2. Increase `keep` value
3. Raise `trigger` threshold

```python
context_management={
    "clear_tool_uses_20250919": {
        "trigger": {"input_tokens": 150000},  # Higher trigger
        "keep": {"tool_uses": 5},             # Keep more
        "exclude_tools": ["critical_tool"]    # Protect important tools
    }
}
```

### Unclear What's Being Cleared

**Problem:** Not sure which tool results are being removed.

**Solution:** Monitor `context_management` field:
```python
def analyze_clearing(response):
    """Analyze what was cleared"""
    if not hasattr(response, 'context_management'):
        return
    
    stats = response.context_management
    cleared_pct = (stats.input_tokens_cleared / stats.original_input_tokens) * 100
    
    print(f"Cleared {cleared_pct:.1f}% of context")
    print(f"Removed {stats.tool_uses_cleared} tool use/result pairs")
    print(f"Kept most recent {keep_count} tool uses")
```

---

## Migration Guide

### From Manual Context Management

**Before (manual pruning):**
```python
# Manually track and prune messages
def prune_old_messages(messages, max_tokens=100000):
    total_tokens = count_tokens(messages)
    while total_tokens > max_tokens:
        messages.pop(0)  # Remove oldest
        total_tokens = count_tokens(messages)
    return messages

messages = prune_old_messages(conversation_history)
response = client.messages.create(messages=messages, ...)
```

**After (automatic context editing):**
```python
# Let context editing handle it automatically
response = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    betas=["context-management-2025-06-27"],
    context_management={
        "clear_tool_uses_20250919": {
            "trigger": {"input_tokens": 100000}
        }
    },
    messages=conversation_history,  # No manual pruning needed
    max_tokens=4096
)
```

---

## Additional Resources

- **Prompt Caching:** https://docs.claude.com/en/docs/build-with-claude/prompt-caching
- **Token Counting:** https://docs.claude.com/en/docs/build-with-claude/token-counting
- **Feedback Form:** https://forms.gle/YXC2EKGMhjN1c4L88
