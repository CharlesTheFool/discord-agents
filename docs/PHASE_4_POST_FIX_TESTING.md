# Phase 4 - Post-Fix Testing Plan

**Purpose**: Verify three bug fixes work correctly after bot restart

**Fixes to Test**:
1. ✅ FTS5 update trigger for edited messages
2. ✅ Embed content extraction for forwarded messages
3. ✅ Race condition prevention for duplicate responses

---

## Test 1: Edited Messages Now Searchable

**What was fixed**: Added SQL trigger to sync FTS5 search index when messages are edited

### Test Steps:
1. Send a test message: "The secret code is ALPHA"
2. Wait 2 seconds (let bot index it)
3. Edit the message to: "The secret code is BRAVO"
4. Wait 2 seconds
5. Ask bot: `@BotName search our messages for "BRAVO"`

**Expected Result**: Bot finds the edited message with "BRAVO"
**If Fails**: The FTS5 update trigger didn't work

---

## Test 2: Forwarded Messages Now Visible

**What was fixed**: Bot now extracts content from message embeds (where forwarded message content lives)

### Test Steps:
1. In a different channel, send a test message: "This is a forwarded test message with unique content XYZ123"
2. Forward that message to your test channel
3. Ask bot: `@BotName what does the forwarded message say?`
4. Then ask: `@BotName search for "XYZ123"`

**Expected Result**:
- Bot can see and describe the forwarded message content
- Search finds the forwarded message

**If Fails**:
- Bot says message is empty → embed extraction not working
- Search doesn't find it → embed content not being indexed

---

## Test 3: No Duplicate Responses (Race Condition Fixed)

**What was fixed**: Bot now tracks which messages it responded to, preventing @mention and periodic check from both responding

### Test Steps:
1. Send three rapid @mentions in quick succession (within 5 seconds):
   ```
   @BotName What's 5+5?
   @BotName What's 6+6?
   @BotName What's 7+7?
   ```

2. Count total bot responses

**Expected Result**: Exactly 3 responses (one per question)
**If Fails**: More than 3 responses = race condition still exists

**Note**: The periodic check runs every 30 seconds, so rapid mentions are most likely to trigger the race condition if it still exists.

---

## Test 4: Combined Test (All Three Together)

**Comprehensive test that exercises all fixes**:

### Test Steps:
1. **Setup**: In a different channel, send: "The test keyword is QUANTUM999"
2. **Forward**: Forward that message to test channel
3. **Rapid Mentions**: Immediately send 3 @mentions:
   ```
   @BotName What does the forwarded message contain?
   @BotName Search for QUANTUM999
   @BotName What's 8+8?
   ```
4. **Edit**: Edit your original "The test keyword is QUANTUM999" message to "The test keyword is PHOTON888"
5. **Wait**: Wait 5 seconds
6. **Search Edited**: `@BotName search for PHOTON888`

**Expected Results**:
- ✅ Bot sees forwarded message content (Fix #2)
- ✅ Bot finds "QUANTUM999" in forwarded message (Fix #2)
- ✅ Exactly 3 responses to 3 mentions (Fix #3)
- ✅ Bot finds "PHOTON888" in edited message (Fix #1)

---

## Verification Checklist

After testing, verify:

### Fix #1: Edited Messages
- [ ] Bot finds content from edited messages
- [ ] FTS5 search index updates correctly
- [ ] No errors in logs about SQL triggers

### Fix #2: Forwarded Messages
- [ ] Bot can see forwarded message text
- [ ] Forwarded messages appear in search results
- [ ] Bot responds to @mentions in forwarded context

### Fix #3: Race Condition
- [ ] No duplicate responses to rapid @mentions
- [ ] Logs show "Already responded to message..." when preventing duplicates
- [ ] Exactly 1 response per message

---

## Log Checks

Look for these in logs:

### Success Indicators:
```
# Fix #3 - Race condition prevented
[DEBUG] Already responded to message {id} in {channel}, skipping

# Fix #2 - Embed content extracted
[DEBUG] Stored message {id} from {author}
# (Check that forwarded message content is non-empty)
```

### Failure Indicators:
```
# Missing SQL trigger
ERROR: ... SQL trigger ... messages_au ...

# Embed extraction failed
# (Forwarded message content appears as empty string)

# Race condition still present
# (Multiple responses to same message, no "Already responded" logs)
```

---

## Rollback Plan

If any test fails:

1. **Check logs** for specific error messages
2. **Verify database** has new trigger:
   ```sql
   SELECT name FROM sqlite_master WHERE type='trigger' AND name='messages_au';
   ```
3. **If Fix #1 fails**: Database needs recreation or manual trigger addition
4. **If Fix #2 fails**: Check `message.embeds` extraction logic in message_memory.py
5. **If Fix #3 fails**: Check `_responded_messages` deque is being populated

---

## Success Criteria

**All tests pass if**:
- ✅ Edited messages appear in search results within 5 seconds of edit
- ✅ Forwarded messages are visible and searchable
- ✅ No duplicate responses to rapid @mentions
- ✅ No errors in logs related to these features

**Ready for production if**: All 4 tests pass cleanly
