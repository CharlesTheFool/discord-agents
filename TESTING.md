# Phase 1 Testing Guide

## ✅ Setup Complete - Ready to Test!

Everything is configured and ready to run.

**Phase 1 Scope:** Foundation - basic connectivity and responses
- ✅ Bot connects to Discord
- ✅ Responds to @mentions with Claude-generated text
- ✅ Stores all messages (including bot's own) in SQLite
- ✅ Tracks message edits and deletes
- ✅ Rate limiting (short + long windows)
- ✅ Engagement tracking (reactions + replies)
- ✅ Ignore threshold / silencing
- ✅ Clean shutdown (cancels background tasks)
- ✅ Conversation logging
- ✅ Bot sees its own previous responses
- ❌ **No memory tool yet** (deferred to Phase 2)
- ❌ **No context editing yet** (deferred to Phase 2)
- ❌ **No emoji/mention/reply resolution yet** (deferred to Phase 2)

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the Bot

```bash
python bot_manager.py spawn alpha
```

Expected output:
```
INFO Bot connected: Claude (Alpha) (ID: ...)
INFO Logged into 1 servers
INFO Bot is ready!
```

### 3. Test in Discord

Go to your test server and:
1. **@mention the bot**: `@Claude (Alpha) hello!`
2. Bot should respond within a few seconds

---

## Test Checklist

### ✅ Basic Functionality

- [ ] **Bot comes online** - Shows as online in Discord
- [ ] **Responds to @mentions** - @mention bot, get Claude response
- [ ] **Context awareness** - Send a few messages, then @mention - bot should reference recent conversation
- [ ] **Message edits tracked** - Edit a previous message, @mention bot - should see updated content
- [ ] **Message deletes tracked** - Delete a message - bot's context should reflect deletion
- [ ] **Logs working** - Check `logs/alpha.log` for activity

### ✅ Rate Limiting

- [ ] **Rapid @mentions** - Send 5-10 @mentions quickly
- [ ] **Rate limit triggers** - After ~20 in 5 minutes, should see rate limit message
- [ ] **Check logs** - Should see "rate_limit_short" or "rate_limit_long"

### ✅ Engagement Tracking

- [ ] **React to bot** - Add emoji reaction to bot's message
- [ ] **Check logs** - After 30s, should see "Engagement! Ignore count now X"
- [ ] **Ignore bot** - Send message, don't react
- [ ] **Check logs** - After 30s, should see "Ignored count now X"

### ✅ Data Persistence

- [ ] **Check database** - `persistence/alpha_messages.db` should exist
- [ ] **Check messages stored** - Database contains conversation history
- [ ] **No memories yet** - Phase 1 doesn't write to `memories/` (coming in Phase 2)

---

## Common Issues

### Bot won't start

**Error: "ALPHA_BOT_TOKEN not set in .env file"**
- Check `.env` file exists in project root
- Verify `ALPHA_BOT_TOKEN=...` is present

**Error: "Bot config not found"**
- Verify `bots/alpha.yaml` exists
- Check you're running from project root directory

### Bot connects but doesn't respond

**Check Discord permissions:**
- Bot has "Read Messages/View Channels"
- Bot has "Send Messages"
- Bot has "Message Content Intent" enabled in Discord Developer Portal

**Check server configuration:**
- `bots/alpha.yaml` has correct server ID in `discord.servers`
- You're @mentioning in a channel the bot can see

### Bot responds with error

**Check logs:**
```bash
tail -f logs/alpha.log
```

Look for:
- API errors (invalid Anthropic key?)
- Permission errors
- Rate limiting messages

---

## Phase 1 Completion Test Flow

**Run this complete flow in a fresh Discord channel to validate Phase 1 is ready.**

**Estimated time: 8 minutes**

### Setup
```bash
# Start fresh
python bot_manager.py spawn alpha
```

### Test Sequence

#### 1. Context & Self-Awareness (2 min)
```
1. Send: "this is test message one"
2. Send: "the password is ALPHA"
3. @bot what messages do you see?
   ✓ Bot lists both messages
4. @bot what did you just say?
   ✓ Bot references its own previous response
```

**Validates:**
- ✅ Context awareness
- ✅ Bot stores and sees its own messages

#### 2. Message Edit Tracking (1 min)
```
5. Edit message #2 to: "the password is BRAVO"
6. @bot what's the password?
   ✓ Bot says "BRAVO" (not ALPHA)
```

**Validates:**
- ✅ Message edit tracking
- ✅ Database updates on edits

#### 3. Message Delete Tracking (1 min)
```
7. Delete message #1
8. @bot what's the first message in this conversation?
   ✓ Bot says message #2 (the password message)
```

**Validates:**
- ✅ Message delete tracking
- ✅ Database cleanup on deletes

#### 4. Engagement Tracking - Positive (30 sec)
```
9. @bot say something interesting
   ✓ Bot responds
10. React with any emoji (👍, ❤️, etc.)
11. Wait 30 seconds
    ✓ Check logs/alpha_conversations.log → "[ENGAGEMENT] ✓ ENGAGED (reactions)"
```

**Validates:**
- ✅ Reaction detection
- ✅ Engagement logging

#### 5. Engagement Tracking - Negative (30 sec)
```
12. @bot what's 5+5?
    ✓ Bot responds
13. Don't react or reply
14. Wait 30 seconds
    ✓ Check logs → "[ENGAGEMENT] ✗ IGNORED"
```

**Validates:**
- ✅ Ignore tracking
- ✅ Engagement background tasks

#### 6. Rate Limiting - Short Window (1 min)
```
15. Send 21 rapid @mentions (spam the same message)
    ✓ Bot responds to first ~19-20 mentions
16. Check response to mention #21
    ✓ Bot says "I'm currently rate-limited"
    ✓ Check logs → "5min: 20/20" or similar
```

**Validates:**
- ✅ Short window rate limiting (5 min / 20 responses)
- ✅ Rate limit messaging
- ✅ Semaphore prevents multiple concurrent responses

#### 7. Ignore Threshold / Silencing (2 min)
```
17. Wait 5 minutes for rate limit reset
18. @bot test 1 → don't react (wait 30s)
19. @bot test 2 → don't react (wait 30s)
20. @bot test 3 → don't react (wait 30s)
21. @bot test 4 → don't react (wait 30s)
22. @bot test 5 → don't react (wait 30s)
23. @bot are you still there?
    ✓ Bot says "I'm currently rate-limited"
    ✓ Check logs → "ignored: 5/5 [SILENCED]"
```

**Validates:**
- ✅ Ignore threshold triggers silencing
- ✅ Silencing persists across mentions

#### 8. Clean Shutdown (10 sec)
```
24. Press Ctrl+C in terminal
    ✓ Shutdown completes within 1-2 seconds
    ✓ Logs show "ReactiveEngine shutdown complete"
    ✓ No hanging processes
```

**Validates:**
- ✅ Background task cancellation
- ✅ Graceful shutdown
- ✅ Database cleanup

---

## Validation Checklist

After completing the test flow, verify:

### Files Created
- [ ] `persistence/alpha_messages.db` exists
- [ ] `logs/alpha.log` exists
- [ ] `logs/alpha_conversations.log` exists

### Database Contents
```bash
# Check message count
sqlite3 persistence/alpha_messages.db "SELECT COUNT(*) FROM messages;"
# Should show ~24+ messages (user + bot messages)
```

### Conversation Log Format
```bash
# Check log structure
tail -50 logs/alpha_conversations.log
```

Expected format:
```
============================================================
=== 2025-10-03 HH:MM:SS | #channel-name | username ===
[@MENTION] <@123456> message content

[DECISION] Respond: YES (mention detected)
[RATE_LIMIT] 5min: X/20, 1hr: Y/200, ignored: Z/5

--- BOT RESPONSE (N chars) ---
Response text here

[ENGAGEMENT] Tracking started (30s delay)
============================================================

[ENGAGEMENT] ✓ ENGAGED (reactions)
```

### No Errors
- [ ] No errors in `logs/alpha.log`
- [ ] No Discord permission errors
- [ ] No Anthropic API errors

---

## Stopping the Bot

Press `Ctrl+C` in the terminal where bot is running.

Expected output:
```
Shutdown requested by user (Ctrl+C)
Shutting down bot...
Cancelling N background tasks...
ReactiveEngine shutdown complete
Shutdown complete
```

---

## Phase 1 Completion Criteria

**Phase 1 is complete when:**

✅ All 8 test sequence steps pass without errors
✅ Bot sees its own previous responses in context
✅ Message edits and deletes update bot's context
✅ Rate limiting triggers correctly (short + ignore threshold)
✅ Engagement tracking detects reactions and ignores
✅ Shutdown completes quickly (1-2 seconds)
✅ Logs show proper format and no errors
✅ Database contains all messages (user + bot)

**If all criteria pass → Move to Phase 2: Intelligence & Context!**

---

## Troubleshooting Common Issues

See earlier sections for:
- Bot won't start
- Bot connects but doesn't respond
- Bot responds with errors

**New Phase 1 Issues:**

### Bot can't see its own messages
- Check `discord_client.py` line 101: Should store ALL messages before filtering
- Check database: `SELECT * FROM messages WHERE is_bot = 1;` should show bot messages

### Engagement tracking never triggers
- Wait full 30 seconds after bot response
- Check `bots/alpha.yaml`: `engagement_tracking_delay` should be 30

### Rate limit not triggering
- Check `bots/alpha.yaml`: `short.max_responses` should be 20
- Check logs for actual response count vs limit
