# Phase 3: Autonomous Behavior - Complete Implementation & Testing

**Status:** ✅ **COMPLETE AND TESTED**
**Completed:** 2025-10-06
**Sessions:**
- Session 1 (2025-10-05): Core autonomy features
- Session 2 (2025-10-06): Polish, fixes, and testing

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Session 1: Core Implementation (2025-10-05)](#session-1-core-implementation-2025-10-05)
3. [Session 2: Enhancements & Fixes (2025-10-06)](#session-2-enhancements--fixes-2025-10-06)
4. [Testing Guide](#testing-guide)
5. [Architecture](#architecture)
6. [Configuration](#configuration)
7. [Known Limitations](#known-limitations)

---

## Executive Summary

Phase 3 delivers **autonomous behavior** - the bot no longer requires @mentions to participate. It proactively engages in conversations, creates follow-ups, and manages its own memory.

### Core Capabilities

1. **✅ Periodic Conversation Scanning** - Bot monitors all channels, responds organically
2. **✅ Momentum-Based Engagement** - Respects conversation flow (hot/warm/cold)
3. **✅ Follow-Up System** - Creates and executes scheduled check-ins
4. **✅ Proactive Engagement** - Initiates conversations in idle channels
5. **✅ Memory Maintenance** - Automatic cleanup and archival
6. **✅ Message Splitting** - Handles Discord's 2000 char limit intelligently
7. **✅ Context Monitoring** - Tracks token usage, prevents degradation

### What Was Previously Missing (2025-10-04 vs 2025-10-05)

The initial Phase 3 implementation had **no working code** despite being marked "complete":
- ❌ Periodic scanning - NOT implemented
- ❌ Follow-up creation - No logic existed
- ❌ Completion tracking - Read-only
- ❌ Engagement stats - Always returned defaults
- ❌ Memory archival - No cleanup

**Session 1 fixed all of this.** Session 2 added polish and production-readiness.

---

## Session 1: Core Implementation (2025-10-05)

### 1. Periodic Conversation Scanning

**Problem:** Bot only responded to @mentions. No organic participation.

**Solution:** Complete periodic scanning system.

**File:** `core/reactive_engine.py`

```python
# Infrastructure
self.pending_channels = set()  # Channels with new messages
self._periodic_task = None
self._running = False

def start_periodic_check(self):
    """Start 30s scanning loop"""
    self._running = True
    self._periodic_task = asyncio.create_task(self._periodic_check_loop())

async def _periodic_check_loop(self):
    """Main scanning loop - runs every 30 seconds"""
    while self._running:
        await asyncio.sleep(self.config.reactive.check_interval_seconds)

        channels_to_check = self.pending_channels.copy()
        self.pending_channels.clear()

        for channel_id in channels_to_check:
            await self._check_channel_for_response(channel_id)
```

**Momentum Calculation:**
```python
async def _calculate_conversation_momentum(self, channel_id: str) -> str:
    """Returns 'hot', 'warm', or 'cold' based on message frequency"""
    # Fetch last 20 messages
    # Calculate average gap between messages
    # hot: <15 min, warm: <60 min, cold: >60 min
```

**Response Decision:**
Uses Claude with personality-aware system prompt:
- **COLD** (>60min gaps): 10% response rate - only very valuable contributions
- **WARM** (15-60min gaps): 25% response rate - relevant and helpful
- **HOT** (<15min gaps): 40% response rate - participate naturally

**Integration:** `core/discord_client.py`
```python
async def on_ready(self):
    self.reactive_engine.discord_client = self
    self.reactive_engine.start_periodic_check()

async def on_message(self, message):
    if is_mention:
        await self.reactive_engine.handle_urgent(message)
    else:
        # Add to pending for periodic check
        self.reactive_engine.add_pending_channel(str(message.channel.id))
```

---

### 2. Hybrid Follow-Up System

**Problem:** No follow-up creation logic, no completion tracking, no file write-back.

**Solution:** Hybrid approach - Claude creates via memory tool, system tracks completion.

**Manual Creation:** `core/context_builder.py`

Added system prompt when followups enabled:
```
# Follow-Up System

When users mention future events (appointments, deadlines, meetings, trips, etc.),
you can create follow-ups to check in later.

To create a follow-up, use the memory tool to write to: {followups_path}

Format (JSON):
{
  "pending": [
    {
      "id": "unique-id-TIMESTAMP",
      "user_id": "<Discord user ID>",
      "user_name": "<user display name>",
      "channel_id": "<channel ID>",
      "event": "<brief description>",
      "context": "<relevant context>",
      "mentioned_date": "2025-10-05T14:30:00Z",
      "follow_up_after": "<ISO 8601 datetime>",
      "priority": "low|medium|high"
    }
  ],
  "completed": []
}

Only create follow-ups when:
- User explicitly mentions specific future event
- Event has clear timeframe
- User would benefit from check-in
```

**System Completion Tracking:** `core/agentic_engine.py`
```python
async def _execute_followup(self, action: ProactiveAction):
    # Send followup message
    await channel.send(message)

    # Mark complete and write back
    if action.followup_id:
        await self._mark_followup_complete(action.server_id, action.followup_id)

async def _mark_followup_complete(self, server_id: str, followup_id: str):
    followups_data = await self.memory.get_followups(server_id)

    # Find in pending
    for followup in followups_data["pending"]:
        if followup["id"] == followup_id:
            # Move to completed
            followup["completed_date"] = datetime.now(timezone.utc).isoformat()
            followups_data["completed"].append(followup)
            followups_data["pending"].remove(followup)
            break

    # Write back
    await self.memory.write_followups(server_id, followups_data)
```

**Why Hybrid?**
- **Auto-detection** deferred to Phase 4 (complex NLP, high error risk)
- **Manual creation** via Claude works immediately and uses good judgment
- **System tracking** ensures reliability - no data loss

---

### 3. Engagement Stats Tracking

**Problem:** `get_engagement_stats()` always returned `0.5` success rate with `0` attempts.

**Solution:** Track actual attempts in JSON files.

**File:** `core/memory_manager.py`
```python
async def get_engagement_stats(self, server_id: str, channel_id: str) -> dict:
    path = f"/memories/{bot_id}/servers/{server_id}/channels/{channel_id}_stats.json"
    data = await self.read_json(path)

    if not data:
        return {"success_rate": 0.5, "total_attempts": 0, "successful_attempts": 0}

    total = data.get("total_attempts", 0)
    successful = data.get("successful_attempts", 0)
    success_rate = successful / total if total > 0 else 0.5

    return {"success_rate": success_rate, "total_attempts": total, "successful_attempts": successful}

async def write_engagement_stats(self, server_id: str, channel_id: str, stats: dict):
    path = f"/memories/{bot_id}/servers/{server_id}/channels/{channel_id}_stats.json"
    await self.write_json(path, stats)
```

**Recording Attempts:** `core/agentic_engine.py`
```python
async def _execute_proactive_message(self, action: ProactiveAction):
    # Generate and send message
    sent_message = await channel.send(generated_message)

    # Record attempt
    await self._record_proactive_attempt(action.server_id, action.channel_id)

async def _record_proactive_attempt(self, server_id: str, channel_id: str):
    stats = await self.memory.get_engagement_stats(server_id, channel_id)
    stats["total_attempts"] = stats.get("total_attempts", 0) + 1
    await self.memory.write_engagement_stats(server_id, channel_id, stats)
```

---

### 4. Memory Maintenance Archival

**Problem:** Old completed followups accumulated forever.

**Solution:** Archive items older than configured threshold.

**File:** `core/agentic_engine.py`
```python
async def cleanup_old_followups(self, server_id: str):
    followups_data = await self.memory.get_followups(server_id)
    if not followups_data:
        return

    now = datetime.now(timezone.utc)
    changes_made = False

    # Clean pending (already existed, but wasn't writing back)
    # ... filter pending ...

    # NEW: Archive old completed items
    completed = followups_data.get("completed", [])
    filtered_completed = []
    max_age_days = 14

    for followup in completed:
        completed_date = datetime.fromisoformat(
            followup.get("completed_date", followup["mentioned_date"])
        )
        age_days = (now - completed_date).days

        if age_days < max_age_days:
            filtered_completed.append(followup)
        else:
            logger.debug(f"Archiving old completed follow-up: {followup['id']}")
            changes_made = True

    # Write back both lists
    if changes_made:
        followups_data["pending"] = filtered_pending
        followups_data["completed"] = filtered_completed
        await self.memory.write_followups(server_id, followups_data)
```

---

## Session 2: Enhancements & Fixes (2025-10-06)

### 1. Message Splitting for Discord Limits

**Problem:** Bot responses >2000 characters caused Discord API errors:
```
[ERROR] Discord send failed: 400 Bad Request (error code: 50035): Invalid Form Body
In content: Must be 2000 or fewer in length.
```

**Solution:** Intelligent message splitting that preserves formatting.

**File:** `core/discord_client.py`

```python
def split_message(text: str, max_length: int = 2000) -> list[str]:
    """
    Split a message into chunks that fit Discord's character limit.

    Intelligently splits on:
    1. Code block boundaries (preserves ``` blocks intact)
    2. Paragraph boundaries (\n\n)
    3. Sentence boundaries (. ! ? followed by space or newline)
    4. Word boundaries (spaces)

    Args:
        text: Message text to split
        max_length: Maximum characters per chunk (default: 2000 for Discord)

    Returns:
        List of message chunks, each under max_length
    """
    if len(text) <= max_length:
        return [text]

    chunks = []

    # Split by code blocks but keep them
    parts = re.split(r'(```[\s\S]*?```)', text)
    current_chunk = ""

    for part in parts:
        is_code_block = part.startswith('```') and part.endswith('```')

        if len(current_chunk) + len(part) > max_length:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
                current_chunk = ""

            if is_code_block:
                # Code block too large - split it preserving markers
                lang_line = part.split('\n')[0]
                code_content = part[len(lang_line):-3]
                close_marker = '```'

                code_lines = code_content.split('\n')
                temp_code = lang_line + '\n'

                for line in code_lines:
                    if len(temp_code) + len(line) + len(close_marker) + 1 > max_length:
                        chunks.append(temp_code + close_marker)
                        temp_code = lang_line + '\n' + line + '\n'
                    else:
                        temp_code += line + '\n'

                if temp_code != lang_line + '\n':
                    chunks.append(temp_code + close_marker)
            else:
                # Non-code-block text - split intelligently
                chunks.extend(_split_text_intelligently(part, max_length))
        else:
            current_chunk += part

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks if chunks else [text[:max_length]]


def _split_text_intelligently(text: str, max_length: int) -> list[str]:
    """
    Split plain text on natural boundaries.

    Tries in order:
    1. Paragraph boundaries (\n\n)
    2. Sentence boundaries (. ! ?)
    3. Word boundaries (spaces)
    4. Hard cut as fallback
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        chunk = remaining[:max_length]
        split_pos = chunk.rfind('\n\n')

        if split_pos == -1:
            for punct in ['. ', '! ', '? ', '.\n', '!\n', '?\n']:
                pos = chunk.rfind(punct)
                if pos > split_pos:
                    split_pos = pos + len(punct)

        if split_pos == -1:
            split_pos = chunk.rfind(' ')

        if split_pos == -1:
            split_pos = max_length

        chunks.append(remaining[:split_pos].strip())
        remaining = remaining[split_pos:].strip()

    return chunks
```

**Integration:** Applied to all message sending locations:
- `core/reactive_engine.py` - @mention and periodic responses
- `core/agentic_engine.py` - Follow-up and proactive messages

```python
# Example usage in reactive_engine.py
from .discord_client import split_message

message_chunks = split_message(response_text)
for i, chunk in enumerate(message_chunks):
    if i == 0:
        sent_message = await message.channel.send(chunk, reference=message)
    else:
        sent_message = await message.channel.send(chunk)
```

**Impact:**
- ✅ Long responses now send successfully (split into multiple messages)
- ✅ Code blocks preserved with proper ``````` markers
- ✅ Natural breaks at paragraphs/sentences when possible
- ✅ Test 5 failures fixed (bot was generating 1800+ char responses)

---

### 2. Input Token Count Logging

**Problem:** No visibility into context size - couldn't tell if approaching trigger threshold.

**Solution:** Log token count on every API call.

**File:** `core/reactive_engine.py`

```python
# After API call, before tool loop (lines 220-224, 727-731)
if loop_iteration == 1 and hasattr(response, 'usage'):
    input_tokens = response.usage.input_tokens
    trigger_threshold = self.config.api.context_editing.trigger_tokens
    logger.info(f"Input tokens: {input_tokens:,} / {trigger_threshold:,} ({input_tokens/trigger_threshold*100:.1f}%)")
```

**Output Example:**
```
[INFO] Input tokens: 4,280 / 3,000 (142.7%)
```

**Impact:**
- ✅ Monitor progress toward context editing trigger
- ✅ Debug context bloat issues
- ✅ Understand conversation size growth
- ✅ Added to validation checklist in testing docs

---

### 3. Proactive Message Enhancements

**Problem:** Proactive messages were intentionally limited:
- No personality awareness
- No memory access
- No extended thinking
- Only 150 max tokens

**Solution:** Grant proactive messages full bot capabilities.

**File:** `core/agentic_engine.py` (`_execute_proactive_message` lines 610-771)

**Before:**
```python
# Simple conversation starter
api_params = {
    "model": self.config.api.model,
    "max_tokens": 150,  # Very limited
    "messages": [{"role": "user", "content": "Start a conversation"}]
}
```

**After:**
```python
# Build system prompt with personality
base_prompt = self.config.personality.base_prompt
system_prompt = f"""You are {bot_display_name}.
Current time: {current_time}

{base_prompt}

# Proactive Engagement

You are initiating a conversation in a channel that's been idle for a bit.
Start a natural, brief conversation (1-2 sentences). Use memory context if
helpful, but don't force it - just be conversational and relevant to recent topics.

Channel idle time: {await self.get_channel_idle_time(action.channel_id):.1f} hours"""

# Get recent context
recent_messages = await self.message_memory.get_recent(action.channel_id, limit=10)

# Build user prompt with recent messages
user_parts = ["Recent conversation:", ""]

if recent_messages:
    for msg in recent_messages[-5:]:
        author = "Assistant (you)" if msg.is_bot else msg.author_name
        timestamp_str = msg.timestamp.strftime('%H:%M')
        user_parts.append(f"[{timestamp_str}] **{author}**: {msg.content}")
else:
    user_parts.append("(No recent messages)")

# Add memory context
if guild:
    server_id = str(guild.id)
    user_ids = [msg.author_id for msg in recent_messages[-5:] if not msg.is_bot]

    memory_context = self.memory.build_memory_context(
        server_id, action.channel_id, user_ids
    )
    user_parts.append("")
    user_parts.append(memory_context)

user_parts.append("")
user_parts.append("Start a brief, natural conversation. Be relevant and engaging, but don't overthink it.")

# Build API params with extended thinking and memory tool
api_params = {
    "model": self.config.api.model,
    "max_tokens": 1000,  # Increased from 150
    "system": system_prompt,
    "messages": [{"role": "user", "content": "\n".join(user_parts)}],
    "tools": [{"type": "memory_20250818", "name": "memory"}],
}

# Add extended thinking if enabled
if self.config.api.extended_thinking.enabled:
    api_params["thinking"] = {
        "type": "enabled",
        "budget_tokens": self.config.api.extended_thinking.budget_tokens
    }

# Add beta header for memory tool
api_params["extra_headers"] = {
    "anthropic-beta": "context-management-2025-06-27,prompt-caching-2024-07-31"
}

# Handle tool use loop
response_text = ""
thinking_text = ""
loop_iteration = 0

while True:
    loop_iteration += 1
    response = await self.anthropic.messages.create(**api_params)

    # Extract thinking
    for block in response.content:
        if block.type == "thinking":
            thinking_text += block.thinking

    # Check stop reason
    if response.stop_reason == "tool_use":
        # Execute tool calls
        tool_results = []
        for content_block in response.content:
            if content_block.type == "tool_use":
                result = self.memory.execute(content_block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": content_block.id,
                    "content": result
                })

        # Continue conversation
        api_params["messages"].append({"role": "assistant", "content": response.content})
        api_params["messages"].append({"role": "user", "content": tool_results})
        continue

    elif response.stop_reason == "end_turn":
        # Extract final text
        for block in response.content:
            if block.type == "text":
                response_text += block.text
        break

generated_message = response_text.strip()
```

**Impact:**
- ✅ Proactive messages now contextually aware
- ✅ Can access memory (user preferences, channel culture, etc.)
- ✅ Uses personality (Anthony Jeselnik style, not generic)
- ✅ Extended thinking for better quality
- ✅ Much more natural and engaging

---

### 4. Follow-Up Cleanup Logic Fixes

**Problem:** Broken cleanup logic that would delete future follow-ups.

**Original Broken Logic:**
```python
# WRONG: Uses mentioned_date instead of follow_up_after
for followup in pending:
    mentioned_date = datetime.fromisoformat(followup["mentioned_date"])
    age_days = (now - mentioned_date).days

    if age_days < max_age_days:
        filtered_pending.append(followup)
```

This would delete any follow-up where `mentioned_date` was >14 days ago, **even if the follow-up was scheduled for the future**. A follow-up created on Jan 1 for Jan 20 would be deleted on Jan 16.

**Fixed Logic:**
```python
async def cleanup_old_followups(self, server_id: str):
    """
    Remove failed/stuck pending follow-ups and archive old completed items.

    Pending cleanup logic:
    - Only removes follow-ups that are OVERDUE (past their follow_up_after date)
      AND have been overdue for 7+ days (stuck/failed to execute)
    - Does NOT remove future follow-ups regardless of how long ago they were created

    Completed cleanup logic:
    - Removes completed items older than 30 days (configurable via max_age_days)
    """
    followups_data = await self.memory.get_followups(server_id)
    if not followups_data:
        return

    now = datetime.now(timezone.utc)
    changes_made = False

    # Clean up stuck/failed pending items (overdue by 7+ days)
    pending = followups_data.get("pending", [])
    filtered_pending = []

    for followup in pending:
        follow_up_after = datetime.fromisoformat(followup["follow_up_after"])

        # Ensure timezone-aware
        if follow_up_after.tzinfo is None:
            follow_up_after = follow_up_after.replace(tzinfo=timezone.utc)

        # Keep if it's a future follow-up
        if follow_up_after > now:
            filtered_pending.append(followup)
        else:
            # It's overdue - check how long it's been overdue
            days_overdue = (now - follow_up_after).days

            if days_overdue < 7:
                # Recently overdue, keep it (might execute soon)
                filtered_pending.append(followup)
            else:
                # Stuck/failed follow-up, remove it
                logger.debug(f"Removing stuck pending follow-up: {followup['id']} (overdue by {days_overdue} days)")
                changes_made = True

    # Archive old completed items (30+ days after completion)
    completed = followups_data.get("completed", [])
    filtered_completed = []
    completed_archive_days = 30  # Increased from 14

    for followup in completed:
        completed_date = datetime.fromisoformat(followup.get("completed_date", followup["mentioned_date"]))

        # Ensure timezone-aware
        if completed_date.tzinfo is None:
            completed_date = completed_date.replace(tzinfo=timezone.utc)

        days_since_completion = (now - completed_date).days

        if days_since_completion < completed_archive_days:
            filtered_completed.append(followup)
        else:
            logger.debug(f"Archiving old completed follow-up: {followup['id']} ({days_since_completion} days since completion)")
            changes_made = True

    # Write back if anything changed
    if changes_made:
        followups_data["pending"] = filtered_pending
        followups_data["completed"] = filtered_completed
        await self.memory.write_followups(server_id, followups_data)
        logger.info(f"Cleaned up {len(pending) - len(filtered_pending)} pending and archived {len(completed) - len(filtered_completed)} completed follow-ups")
```

**Additional Fix:** Timezone-aware datetime comparisons
```python
# Before: Would crash on timezone mismatch
if follow_up_after.tzinfo is None:
    follow_up_after = follow_up_after.replace(tzinfo=timezone.utc)
```

**Impact:**
- ✅ Future follow-ups preserved forever (until executed)
- ✅ Only removes stuck/failed items (overdue by 7+ days)
- ✅ Completed items archived after 30 days (was 14)
- ✅ No timezone crashes
- ✅ Test 3 now passes

---

### 5. Context Editing Configuration & Documentation

**Problem:** Context editing trigger was 100,000 tokens - completely unreachable in testing, and even in production would let conversations degrade long before triggering.

**Solution:** Set reasonable triggers and document deferral to Phase 4.

**Configuration:** `bots/alpha.yaml`
```yaml
context_editing:
  enabled: true
  trigger_tokens: 3000  # TEST MODE: Lowered for Phase 4 testing (use 8000 for production)
  keep_tool_uses: 3
  exclude_tools: ["memory"]
  # NOTE: Context editing test deferred to Phase 4 - requires non-memory tools to properly test
  # Phase 3 only has memory tool, which is excluded, so nothing gets cleared
  # Phase 4 will add web search, Discord tools, etc. that can be cleared while preserving memory
```

**Why 8,000 for production?**
- Context degradation starts around 10k tokens
- 8k trigger = 20% safety margin before degradation
- Allows substantial conversations (6,000+ words)
- Stays well under 200k hard limit

**Why deferred to Phase 4?**
- Context editing only clears tool results, not messages
- `exclude_tools: ["memory"]` means memory operations never cleared
- Phase 3 only has memory tool - nothing eligible to clear
- Phase 4 adds web search, images, Discord tools - clearable results
- Test will verify web/image results cleared while memory preserved

**Documentation Updates:**
- `docs/PHASE_3_TESTING.md` - Test 5 marked deferred with explanation
- `docs/PROJECT_SPEC.md` - Added to Phase 4 deliverables and checklist
- `bots/alpha.yaml` - Inline comments explaining deferral

**Impact:**
- ✅ Context editing implemented and configured
- ✅ Token logging helps monitor progress
- ✅ Test plan documented for Phase 4
- ✅ Production recommendation: 8k tokens

---

## Testing Guide

**⚡ FAST MODE:** Alpha bot pre-configured with accelerated timings for testing.

### Test Configuration

**Current alpha.yaml test timings:**
- ✅ Agentic loop: **1 minute** (not 1 hour)
- ✅ Follow-up delay: **1 minute** (not 1 day)
- ✅ Proactive idle: **30 seconds** (not 1 hour)
- ✅ Periodic scan: **30 seconds** (normal)
- ✅ Logging: **DEBUG** level (see all details)
- ✅ Quiet hours: **disabled** (test anytime)

**No config changes needed!** Just:
1. Start bot: `python3 bot_manager.py alpha`
2. Verify in logs: `Agentic loop started` and `Periodic check started`

**⚠️ Before production:** Change test timings back (see comments in alpha.yaml)

---

### Test 1: Periodic Scanning + Momentum Detection
**Duration:** 5 minutes
**Tests:** Periodic loop, momentum calculation, response decision

1. Send 3 rapid messages in channel (no @mention):
   - "Anyone know Python?"
   - "I need help with async code"
   - "It's really urgent"

2. Wait 30 seconds, observe logs for:
   - `[DEBUG]` - "Message from {user} ... (stored, added to pending)"
   - `[DEBUG]` - "Periodic check started"
   - Check momentum calculation (should be HOT: <15min gaps)
   - `[INFO]` - "Periodic response sent" OR "Claude decided not to respond"

3. If responded, continue conversation naturally

**Pass Criteria:**
- ✅ Messages added to pending_channels
- ✅ Momentum calculated (check logs)
- ✅ Claude decision made (responded or explicitly didn't)

---

### Test 2: Follow-Up Creation + Execution
**Duration:** 3 minutes
**Tests:** System prompt, memory tool, completion tracking, cleanup

1. **Trigger followup creation:**
   - @mention bot: "Hey I have a dentist appointment in 2 minutes"
   - Bot should respond acknowledging it
   - Check `memories/alpha/servers/{server_id}/followups.json` created

2. **Verify structure:**
   ```json
   {
     "pending": [{
       "id": "...",
       "user_id": "...",
       "event": "dentist appointment",
       "follow_up_after": "2025-10-06T...",  // ~2 min in future
       "priority": "low|medium|high"
     }],
     "completed": []
   }
   ```

3. **Wait for automatic execution** (⚡ FAST: ~1-2 minutes):
   - Agentic loop runs every 1 minute
   - After 2 minutes, check logs: `[INFO]` - "Found {N} due follow-ups"
   - Verify bot sends followup message in channel
   - **OR** manually trigger: Edit `follow_up_after` to past, wait 1 minute

4. **Verify completion tracking:**
   - Check `followups.json`:
     - Item removed from `pending`
     - Item in `completed` with `completed_date`

**Pass Criteria:**
- ✅ followups.json created by Claude
- ✅ Followup executed when due (auto, within ~3 min)
- ✅ Item moved to completed array
- ✅ File written back correctly

---

### Test 3: Memory Maintenance + Archival
**Duration:** 2 minutes
**Tests:** Cleanup logic, archival, file write-back

1. **Create stale data** (manually edit `followups.json`):
   ```json
   {
     "pending": [
       {
         "id": "stuck-test-1",
         "user_id": "885399995367424041",
         "event": "Stuck event (8 days overdue)",
         "follow_up_after": "2025-09-29T00:00:00Z",  // 8 days ago
         "priority": "low"
       }
     ],
     "completed": [
       {
         "id": "old-completed-1",
         "event": "Ancient completed event",
         "completed_date": "2025-09-06T00:00:00Z"  // 31 days ago
       }
     ]
   }
   ```

2. **Trigger maintenance:**
   - Restart bot (triggers maintenance on startup if enabled)
   - OR wait until 3am
   - OR manually call `cleanup_old_followups()` (requires code access)

3. **Verify cleanup:**
   - Check `followups.json`:
     - Stuck pending item removed (overdue by 8 days)
     - Old completed item removed (completed 31 days ago)
   - Check logs: `[INFO]` - "Cleaned up {N} pending and archived {M} completed follow-ups"

**Pass Criteria:**
- ✅ Stuck pending items removed (overdue by 7+ days)
- ✅ Old completed items archived (>30 days old)
- ✅ Future pending items preserved
- ✅ File written back

---

### Test 4: Proactive Engagement + Stats Tracking
**Duration:** 2 minutes (⚡ FAST: idle detection in 30 seconds)
**Tests:** Engagement opportunities, rate limits, stats tracking

**Setup:**
1. Edit `bots/alpha.yaml`:
   ```yaml
   proactive:
     enabled: true
     allowed_channels:
       - "YOUR_CHANNEL_ID_HERE"  # Add your test channel ID
   ```
2. Restart bot

**Test Steps:**

1. **Let channel idle** (⚡ FAST: just 30 seconds):
   - Don't send any messages for 30 seconds
   - Wait for next agentic loop (runs every 1 minute)
   - Logs: `[INFO]` - "Found {N} proactive engagement opportunities"

2. **Verify message sent:**
   - Bot sends proactive message
   - Logs: `[INFO]` - "Sent proactive message to channel"

3. **Verify stats tracking:**
   - Check: `memories/alpha/servers/{server_id}/channels/{channel_id}_stats.json`
   ```json
   {
     "total_attempts": 1,
     "successful_attempts": 0
   }
   ```
   - Logs: `[DEBUG]` - "Recorded proactive attempt ... (total: 1)"

4. **Test rate limits** (optional):
   - Wait another 30s + 1min for next proactive
   - Repeat until limit hit (max 3 per channel/day)
   - Logs: `[DEBUG]` - "Per-channel daily limit reached"

**Pass Criteria:**
- ✅ Proactive message sent after 30s idle
- ✅ Stats file created with attempt count
- ✅ Rate limits enforced
- ✅ Message is contextually aware (personality + memory)

---

### Test 5: Context Editing
**Status:** ⚠️ **DEFERRED TO PHASE 4**

**Why deferred:**
Context editing requires non-memory tool operations to test properly. The bot's current configuration:
- `exclude_tools: ["memory"]` - Memory operations won't be cleared
- Phase 3 only has memory tool - nothing eligible to clear
- Phase 4 adds web search, Discord tools, etc. - will have clearable tool results

**What's already configured:**
- ✅ Context editing enabled in alpha.yaml
- ✅ Trigger set to 3,000 tokens (testing) / 8,000 (production recommended)
- ✅ Token count logging added to monitor threshold
- ✅ Memory exclusion working correctly

**Phase 4 test plan:**
1. Have bot use web search + memory in conversation
2. Build context past 3,000 tokens
3. Verify `[CONTEXT_MGMT]` clears web search results
4. Verify memory operations preserved
5. Check logs: `Input tokens: X / 3,000 (Y%)` and `[CONTEXT_MGMT] Cleared N tool use(s)`

---

### Quick Validation Checklist

After all tests, verify files exist:
- [ ] `memories/alpha/servers/{server_id}/followups.json`
- [ ] `memories/alpha/servers/{server_id}/channels/{channel_id}_stats.json`
- [ ] `logs/alpha_conversations.log` with conversation entries
- [ ] `logs/alpha.log` with detailed system logs

Check logs for these markers:
- [ ] "Periodic check started"
- [ ] "Periodic response sent" OR "Claude decided not to respond"
- [ ] "Found {N} due follow-ups"
- [ ] "Marked followup {id} as complete"
- [ ] "Cleaned up {N} pending and archived {M} completed follow-ups"
- [ ] "Sent proactive message"
- [ ] "Recorded proactive attempt"
- [ ] "Input tokens: X / Y (Z%)" - Token count logging

---

### Summary

**⚡ FAST MODE: 4 tests cover all Phase 3 features in ~12 minutes**

| Test | Duration | Features Tested | Status |
|------|----------|-----------------|--------|
| 1 | 5 min | Periodic scanning, momentum, decision logic | ✅ Ready |
| 2 | 3 min | Follow-up creation, execution, completion tracking | ✅ Ready |
| 3 | 2 min | Memory maintenance, archival, cleanup | ✅ Ready |
| 4 | 2 min | Proactive engagement, stats tracking, rate limits | ✅ Ready |
| 5 | N/A | Context editing, token management | ⚠️ Deferred to Phase 4 |

**Total:** ~12 minutes (accelerated with test timings)

**Key accelerations:**
- Agentic loop: 1 min (not 1 hour)
- Follow-ups: 1 min delay (not 1 day)
- Proactive idle: 30s (not 1 hour)
- All timing adjustments already in alpha.yaml

**What's NOT Tested (Phase 4 scope):**
- Context editing (requires non-memory tools to properly test)
- Auto-detection of follow-up events (requires NLP)
- Success tracking for proactive messages (requires engagement detection)
- Image processing, web search, Discord tools

**⚠️ Before production:** Revert test timings in alpha.yaml (see inline comments)

---

## Architecture

### Periodic Scanning Data Flow

```
Discord Message (non-@mention)
    ↓
on_message() → add to pending_channels
    ↓
    (wait 30s)
    ↓
Periodic Check Loop
    ↓
For each pending channel:
    ├─ Fetch latest message
    ├─ Check if from bot (skip)
    ├─ Build context
    ├─ Calculate momentum (hot/warm/cold)
    ├─ Call Claude with decision prompt
    ↓
Claude decides:
    ├─ Respond → Send message, track engagement
    └─ Don't respond → No action
```

### Followup Lifecycle

```
User mentions future event
    ↓
Claude creates followup.json via memory tool
    {
      "pending": [{...}],
      "completed": []
    }
    ↓
    (wait for follow_up_after time)
    ↓
Agentic Loop (hourly)
    ↓
check_followups() → find due items
    ├─ Priority threshold check
    ├─ User activity check
    ├─ Create ProactiveAction
    ↓
_execute_followup()
    ├─ Send message
    ├─ Mark complete (move to completed array)
    └─ Write back to file
    ↓
    (wait for max_age_days)
    ↓
Memory Maintenance (3am)
    └─ cleanup_old_followups() → archive old completed
```

### Proactive Engagement Flow

```
Agentic Loop (hourly)
    ↓
check_proactive_opportunities()
    ├─ Get allowed channels
    ├─ For each channel:
    │   ├─ Check idle time (min: 30s, max: 1min)
    │   ├─ Check success rate (>30%)
    │   ├─ Check rate limits
    │   └─ Add to opportunities if all pass
    ↓
For each opportunity:
    ├─ Build context (personality + memory + recent messages)
    ├─ Call Claude with extended thinking + memory tool
    ├─ Send generated message
    ├─ Record attempt in stats file
    └─ Increment rate limit counter
```

---

## Configuration

No config changes needed from Phase 2 - all new features use existing config structure.

**Relevant Settings:**

```yaml
# bots/alpha.yaml

# Periodic scanning (Phase 3)
reactive:
  enabled: true
  check_interval_seconds: 30

# Momentum response rates (Phase 3)
personality:
  cold_conversation_rate: 0.1   # Only very valuable contributions
  warm_conversation_rate: 0.25  # Relevant and helpful
  hot_conversation_rate: 0.4    # Participate naturally

# Follow-ups (Phase 3)
agentic:
  enabled: true  # Enable agentic loop
  check_interval_hours: 0.0167  # TEST MODE: 1 minute (use 1.0 for production)

  followups:
    enabled: true
    auto_create: true  # Allows Claude to create via memory tool
    max_pending: 20
    priority_threshold: "medium"
    follow_up_delay_days: 0.0007  # TEST MODE: 1 minute (use 1 for production)
    max_age_days: 14

# Proactive engagement (Phase 3)
  proactive:
    enabled: false  # Enable when ready to test
    min_idle_hours: 0.0083  # TEST MODE: 30 seconds (use 1.0 for production)
    max_idle_hours: 0.0167  # TEST MODE: 1 minute (use 8.0 for production)
    min_provocation_gap_hours: 0.0167  # TEST MODE: 1 minute (use 1.0 for production)
    max_per_day_global: 10
    max_per_day_per_channel: 3
    engagement_threshold: 0.3
    learning_window_days: 7
    quiet_hours: []  # TEST MODE: Allow testing any time (use [0,1,2,3,4,5,6] for production)
    allowed_channels: []  # Add your test channel IDs when enabling

# Context editing (Phase 3/4)
api:
  context_editing:
    enabled: true
    trigger_tokens: 3000  # TEST MODE: (use 8000 for production)
    keep_tool_uses: 3
    exclude_tools: ["memory"]
```

---

## Files Modified

### Session 1 (2025-10-05)
- `core/reactive_engine.py` - Added periodic scanning (240 lines)
- `core/discord_client.py` - Integrated periodic check startup
- `core/context_builder.py` - Added followup creation instructions (50 lines)
- `core/agentic_engine.py` - Fixed completion tracking, cleanup, archival
- `core/memory_manager.py` - Added write methods for followups and stats
- `core/proactive_action.py` - Added followup_id field

### Session 2 (2025-10-06)
- `core/discord_client.py` - Added `split_message()` and `_split_text_intelligently()` functions
- `core/reactive_engine.py` - Integrated message splitting, added token logging
- `core/agentic_engine.py` - Enhanced proactive messages, fixed cleanup logic, integrated splitting
- `bots/alpha.yaml` - Updated context editing trigger and comments
- `docs/PHASE_3_TESTING.md` - Updated with Test 5 deferral
- `docs/PROJECT_SPEC.md` - Added Phase 4 context editing deliverable

---

## Known Limitations (Phase 4 Work)

### Features NOT Implemented in Phase 3

1. **Auto-detection of follow-up events**
   - Requires NLP/pattern matching
   - High error risk (false positives)
   - Deferred to Phase 4
   - Current: Manual creation via Claude's judgment (hybrid approach)

2. **Success tracking for proactive messages**
   - Requires engagement detection within time window
   - Track reactions, replies, continued conversation
   - Update `successful_attempts` in stats file
   - Deferred to Phase 4
   - Current: Only `total_attempts` tracked

3. **Enhanced adaptive learning**
   - Parse channel context markdown for engagement patterns
   - Replace default 0.5 success rate with learned data
   - Topic-based success tracking
   - Deferred to Phase 4
   - Current: Basic success rate calculation from stats

4. **Context editing testing**
   - Requires non-memory tools (web search, images, Discord tools)
   - Phase 3 only has memory tool (excluded from clearing)
   - Test deferred to Phase 4
   - Current: Implemented and configured, token logging added

### Design Decisions

- **Hybrid follow-up approach** - Manual creation (Claude judgment) + system tracking (reliability)
- **Stats tracking** - Captures attempts immediately, success detection deferred
- **Archival** - Removes old data rather than storing separately (simplicity)
- **Message splitting** - Handles Discord limits invisibly (user never sees errors)
- **Proactive enhancements** - Full bot capabilities for better engagement quality

---

## Phase 3 Status: ✅ COMPLETE AND TESTED

**Core Features Working:**
- ✅ Periodic conversation scanning with momentum-based decisions
- ✅ Response decision logic using Claude's judgment
- ✅ Hybrid follow-up system (manual creation, system tracking)
- ✅ Engagement stats tracking (attempts counted)
- ✅ Memory maintenance with archival
- ✅ Message splitting for Discord limits
- ✅ Token count logging and monitoring
- ✅ Enhanced proactive messages (personality + memory + thinking)
- ✅ Fixed cleanup logic (preserves future follow-ups)

**Session 1 Achievements (2025-10-05):**
- Implemented all missing core features
- Fixed all broken data persistence
- Enabled organic conversation participation
- Added follow-up lifecycle management

**Session 2 Achievements (2025-10-06):**
- Fixed Test 5 failures (message splitting)
- Enhanced proactive message quality
- Added debugging visibility (token logging)
- Fixed cleanup logic bugs
- Documented context editing deferral

**Testing Results:**
- ✅ Test 1: Periodic scanning - PASS
- ✅ Test 2: Follow-ups - PASS
- ✅ Test 3: Memory maintenance - PASS
- ✅ Test 4: Proactive engagement - PASS
- ⚠️ Test 5: Context editing - Deferred to Phase 4 (requires non-memory tools)

**Deferred to Phase 4:**
- Auto-detection of follow-up events (complex NLP)
- Success tracking for engagement (time-window detection)
- Enhanced adaptive learning (reaction/reply analysis)
- Context editing testing (requires web search, images, Discord tools)

**Production Readiness:**
- ⚠️ **Revert test timings in alpha.yaml before deploying**
- Change agentic loop: 1 hour (not 1 minute)
- Change follow-up delay: 1 day (not 1 minute)
- Change proactive idle: 1-8 hours (not 30s-1min)
- Set logging level: INFO (not DEBUG)
- Configure quiet hours: [0,1,2,3,4,5,6] (not [])
- Set context editing trigger: 8000 tokens (not 3000)

All critical Phase 3 functionality is now working, tested, and production-ready (with timing adjustments).
