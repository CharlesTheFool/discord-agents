# Phase 3: Minimal Testing Sequence

**Purpose:** Maximize test coverage with minimal effort through dense, multi-feature tests.

---

## Pre-Test Setup

```yaml
# bots/alpha.yaml
agentic:
  enabled: true
  check_interval_hours: 1

  followups:
    enabled: true
    max_age_days: 14

  proactive:
    enabled: false  # Enable in Test 4
```

Start bot: `python3 bot_manager.py alpha`

---

## Test 1: Periodic Scanning + Momentum Detection
**Duration:** 5 minutes
**Tests:** Periodic loop, momentum calculation, response decision

1. Send 3 rapid messages in channel (no @mention):
   - "Anyone know Python?"
   - "I need help with async code"
   - "It's really urgent"

2. Wait 30 seconds, observe logs for:
   - `DEBUG` - "Message from {user} ... (stored, added to pending)"
   - `DEBUG` - "Periodic check started"
   - Check momentum calculation (should be HOT: <15min gaps)
   - `INFO` - "Periodic response sent" OR "Claude decided not to respond"

3. If responded, continue conversation naturally

**Pass Criteria:**
- ✅ Messages added to pending_channels
- ✅ Momentum calculated (check logs)
- ✅ Claude decision made (responded or explicitly didn't)

---

## Test 2: Follow-Up Creation + Execution
**Duration:** 10 minutes
**Tests:** System prompt, memory tool, completion tracking, cleanup

1. **Trigger followup creation:**
   - @mention bot: "Hey I have a dentist appointment tomorrow at 2pm"
   - Bot should respond acknowledging it
   - Check `memories/alpha/servers/{server_id}/followups.json` created

2. **Verify structure:**
   ```json
   {
     "pending": [{
       "id": "...",
       "user_id": "...",
       "event": "dentist appointment",
       "follow_up_after": "2025-10-06T...",
       "priority": "low|medium|high"
     }],
     "completed": []
   }
   ```

3. **Manually trigger execution** (don't wait 24 hours):
   - Edit `follow_up_after` to 1 minute in the past
   - Wait for next agentic loop iteration (~1 hour, or restart bot)
   - Check logs: `INFO` - "Found {N} due follow-ups"
   - Verify bot sends followup message in channel

4. **Verify completion tracking:**
   - Check `followups.json`:
     - Item removed from `pending`
     - Item in `completed` with `completed_date`

**Pass Criteria:**
- ✅ followups.json created by Claude
- ✅ Followup executed when due
- ✅ Item moved to completed array
- ✅ File written back correctly

---

## Test 3: Memory Maintenance + Archival
**Duration:** 2 minutes
**Tests:** Cleanup logic, archival, file write-back

1. **Create stale data** (manually edit `followups.json`):
   ```json
   {
     "pending": [
       {
         "id": "stale-1",
         "mentioned_date": "2025-09-20T00:00:00Z",
         "user_id": "123",
         "event": "old event",
         "follow_up_after": "2025-09-21T00:00:00Z",
         "priority": "low",
         "channel_id": "456",
         "context": "test"
       }
     ],
     "completed": [
       {
         "id": "old-completed-1",
         "completed_date": "2025-09-15T00:00:00Z",
         "mentioned_date": "2025-09-10T00:00:00Z",
         "event": "ancient event",
         "user_id": "789"
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
     - Stale pending item removed (>14 days old)
     - Old completed item removed (>14 days old)
   - Check logs: `INFO` - "Cleaned up {N} pending and archived {M} completed follow-ups"

**Pass Criteria:**
- ✅ Stale items removed from both arrays
- ✅ File written back
- ✅ Logs show cleanup count

---

## Test 4: Proactive Engagement + Stats Tracking
**Duration:** 3 minutes (active wait for idle time)
**Tests:** Engagement opportunities, rate limits, stats tracking

**Setup:**
```yaml
# bots/alpha.yaml
agentic:
  proactive:
    enabled: true
    min_idle_hours: 0.05  # 3 minutes for testing
    max_idle_hours: 8.0
    allowed_channels:
      - "{YOUR_CHANNEL_ID}"  # Add actual channel ID
```

1. **Let channel idle:**
   - Don't send any messages for 3 minutes
   - Wait for agentic loop (hourly check)
   - Logs should show: `INFO` - "Found {N} proactive engagement opportunities"

2. **Verify message sent:**
   - Bot sends proactive message
   - Check logs: `INFO` - "Sent proactive message to channel"

3. **Verify stats tracking:**
   - Check file: `memories/alpha/servers/{server_id}/channels/{channel_id}_stats.json`
   ```json
   {
     "total_attempts": 1,
     "successful_attempts": 0
   }
   ```
   - Logs: `DEBUG` - "Recorded proactive attempt ... (total: 1)"

4. **Test rate limits:**
   - Keep triggering proactive (edit idle time, restart)
   - Verify stops at `max_per_day_per_channel: 3`
   - Logs: `DEBUG` - "Per-channel daily limit reached"

**Pass Criteria:**
- ✅ Proactive message sent after idle period
- ✅ Stats file created with attempt count
- ✅ Rate limits enforced

---

## Test 5: Context Editing (Phase 2 completion)
**Duration:** 2 minutes
**Tests:** Token management, tool use clearing

1. **Long conversation with memory ops:**
   - @mention bot 10 times asking different questions
   - Make bot use memory tool (mention user details)
   - Continue until >8000 input tokens

2. **Verify context management:**
   - Check logs for: `[CONTEXT_MGMT] Cleared {N} tool use(s), {M} tokens`
   - Verify percentage cleared shown
   - Conversation continues smoothly (no errors)

**Pass Criteria:**
- ✅ Context management triggered
- ✅ Tool uses cleared (logged)
- ✅ Memory tool operations preserved (not cleared)

---

## Quick Validation Checklist

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
- [ ] "[CONTEXT_MGMT] Cleared"

---

## Summary

**5 tests cover all Phase 3 features:**

| Test | Duration | Features Tested |
|------|----------|-----------------|
| 1 | 5 min | Periodic scanning, momentum, decision logic |
| 2 | 10 min | Follow-up creation, execution, completion tracking |
| 3 | 2 min | Memory maintenance, archival, cleanup |
| 4 | 3 min | Proactive engagement, stats tracking, rate limits |
| 5 | 2 min | Context editing, token management |

**Total:** ~22 minutes of active testing

**What's NOT Tested (Phase 4 scope):**
- Auto-detection of follow-up events (requires NLP)
- Success tracking for proactive messages (requires engagement detection)
- Image processing, web search, Discord tools
