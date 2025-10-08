# Phase 4 - Minimal Testing Checklist

**Purpose**: Test all Phase 4 features with minimal effort and maximum coverage

**Duration**: ~15-20 minutes

**Prerequisites**:
- Bot running with Phase 4 features enabled
- Access to Discord test server
- Test image file ready (any JPG/PNG)

---

## Setup Verification

### ✅ Test 0: Bot Online
**Action**: Check bot status
**Expected**: Bot shows as online in Discord

**Pass Criteria**: ✅ Bot is online
**Status**: [ ]

---

## Web Search Tests (Critical - Phase 1 Fixes)

### ✅ Test 1: Basic Web Search
**Action**: Send message:
```
@BotName What are the latest developments in quantum computing?
```

**Expected**:
- Bot responds with current information
- Response includes a **"Sources:"** section at the bottom
- Sources formatted as clickable links: `[Title](URL)`

**Pass Criteria**:
- ✅ Bot searches and responds
- ✅ Sources section appears
- ✅ Links are clickable

**Status**: [ ]

---

### ✅ Test 2: Web Search Quota Tracking
**Action**: Check logs after Test 1

**Expected**: Log should show:
```
Server tool used: web_search
```

**Pass Criteria**: ✅ Quota tracking log appears
**Status**: [ ]

---

## Web Fetch Tests (Critical - Phase 1 Fixes)

### ✅ Test 3: Web Fetch with URL
**Action**: Send message:
```
@BotName Please analyze this article: https://en.wikipedia.org/wiki/Claude_Shannon
```

**Expected**:
- Bot fetches and analyzes the page
- Response includes analysis of content
- **Sources:** section with the Wikipedia link

**Pass Criteria**:
- ✅ Bot fetches article
- ✅ Provides analysis
- ✅ Citations displayed

**Status**: [ ]

---

### ✅ Test 4: Combined Search + Fetch
**Action**: Send message:
```
@BotName Find recent articles about AI safety and analyze the most relevant one
```

**Expected**:
- Bot searches for articles
- Bot fetches one for deeper analysis
- Response shows synthesis of information
- **Sources:** section with multiple links

**Pass Criteria**:
- ✅ Bot uses both tools
- ✅ Multiple sources listed
- ✅ Coherent analysis

**Status**: [ ]

---

## Citation Display Tests (Critical - Phase 1 Fixes)

### ✅ Test 5: Citation Format Verification
**Action**: Review responses from Tests 1-4

**Expected Format**:
```
[Bot's response text...]

**Sources:**
- [Article Title](https://example.com/article)
- [Another Source](https://example.com/source)
```

**Pass Criteria**:
- ✅ "Sources:" header is bold
- ✅ Each source on new line with dash
- ✅ Links work in Discord (clickable)

**Status**: [ ]

---

## Discord Tools Tests

### ✅ Test 6: Message Search (FTS5)
**Action**: Send message:
```
@BotName Search our conversation history for messages about "quantum computing"
```

**Expected**:
- Bot searches message database
- Returns relevant messages with timestamps
- Shows author names

**Pass Criteria**:
- ✅ Search executes
- ✅ Results returned
- ✅ Formatting clear

**Status**: [ ]

---

### ✅ Test 7: User Info Lookup
**Action**: Send message:
```
@BotName What do you know about my Discord activity?
```

**Expected**:
- Bot looks up your user info from cache
- Shows message count
- Shows last seen time
- Shows username/display name

**Pass Criteria**:
- ✅ User info retrieved
- ✅ Stats accurate
- ✅ No errors

**Status**: [ ]

---

## Image Processing Tests

### ✅ Test 8: Image Upload and Processing
**Action**:
1. Upload a test image (JPG or PNG, any size)
2. Add message: `@BotName What's in this image?`

**Expected**:
- Bot processes image (may compress if large)
- Bot describes image content
- Response shows understanding of image

**Pass Criteria**:
- ✅ Image received
- ✅ Description accurate
- ✅ No compression errors (check logs)

**Status**: [ ]

---

### ✅ Test 9: Large Image Handling
**Action**:
1. Upload large image (>5MB if available)
2. Add message: `@BotName Describe this image`

**Expected**:
- Bot compresses image (check logs for compression)
- Bot still processes and responds
- No errors

**Pass Criteria**:
- ✅ Compression logged
- ✅ Response generated
- ✅ No crashes

**Status**: [ ]

---

## Integration Tests

### ✅ Test 10: Web Search + Images
**Action**: Send message:
```
@BotName Search for images of the Eiffel Tower and tell me about it
```

**Expected**:
- Bot searches for information
- Bot provides details about Eiffel Tower
- **Sources:** section present

**Pass Criteria**:
- ✅ Information retrieved
- ✅ Sources shown
- ✅ Coherent response

**Status**: [ ]

---

### ✅ Test 11: Discord Tools + Message Context
**Action**:
1. Have a conversation about a topic (3-4 messages)
2. Then ask: `@BotName Search for what we discussed about [topic]`

**Expected**:
- Bot searches conversation history
- Finds recent messages
- References your conversation

**Pass Criteria**:
- ✅ Finds messages
- ✅ Context maintained
- ✅ Accurate results

**Status**: [ ]

---

### ✅ Test 12: Multiple Tools in One Request
**Action**: Send message:
```
@BotName Search for recent news about SpaceX, analyze the most interesting article, and show me what we've discussed about space before
```

**Expected**:
- Bot uses web_search
- Bot uses web_fetch
- Bot uses discord_tools
- All results synthesized
- **Sources:** section present

**Pass Criteria**:
- ✅ Multiple tools used
- ✅ Results combined coherently
- ✅ Citations displayed

**Status**: [ ]

---

## Error Handling Tests

### ✅ Test 13: Invalid URL
**Action**: Send message:
```
@BotName Analyze this page: https://this-site-definitely-does-not-exist-12345.com
```

**Expected**:
- Bot attempts to fetch
- Bot reports error gracefully
- No crash

**Pass Criteria**:
- ✅ Graceful error message
- ✅ Bot remains responsive
- ✅ No stack traces to user

**Status**: [ ]

---

### ✅ Test 14: Empty Search
**Action**: Send message:
```
@BotName Search our messages for "qwxyzqwxyzqwxyz"
```

**Expected**:
- Bot searches
- Reports no results found
- Remains responsive

**Pass Criteria**:
- ✅ "No results" message
- ✅ No errors
- ✅ Bot still works

**Status**: [ ]

---

## Performance Tests

### ✅ Test 15: Rapid Sequential Requests
**Action**: Send 3 messages quickly:
```
@BotName What's 2+2?
@BotName What's 3+3?
@BotName What's 4+4?
```

**Expected**:
- Bot responds to all three
- Responses in order
- No crashes or hangs

**Pass Criteria**:
- ✅ All responses received
- ✅ Correct answers
- ✅ No rate limit errors

**Status**: [ ]

---

## Engagement Tracking Tests

### ✅ Test 16: Engagement Detection
**Action**:
1. Bot sends a message (from Test 15 or earlier)
2. React to bot's message with any emoji
3. Wait 30 seconds
4. Check logs

**Expected Log**:
```
Engagement detected for message [id]
```

**Pass Criteria**: ✅ Engagement logged
**Status**: [ ]

---

## Quota Management Tests

### ✅ Test 17: Quota Status Check
**Action**: Check bot logs for quota information

**Expected**: Should see lines like:
```
Web search tools added to API request (max_uses=3, citations=True)
Web search recorded: X/300 today
```

**Pass Criteria**: ✅ Quota tracking visible
**Status**: [ ]

---

## Final Verification

### ✅ Test 18: Complete Workflow
**Action**: Send one comprehensive message:
```
@BotName I need help understanding quantum computing. Search for recent articles, analyze the best one, check if we've discussed this before, and explain it to me like I'm 5. If you need any images, describe what would help.
```

**Expected**:
- Bot searches web
- Bot fetches article
- Bot searches message history
- Bot synthesizes everything
- **Sources:** section present
- Clear, simple explanation

**Pass Criteria**:
- ✅ All tools used appropriately
- ✅ Response is coherent
- ✅ Citations present
- ✅ No errors

**Status**: [ ]

---

## Critical Log Checks

After completing all tests, check logs for:

### ✅ Verification Checklist

**Server Tool Usage**:
- [ ] Logs show `Server tool used: web_search`
- [ ] Logs show `Server tool used: web_fetch`
- [ ] Logs show quota tracking: `Web search recorded: X/300 today`

**Citations**:
- [ ] No errors about missing citation data
- [ ] All web responses include Sources section

**Discord Tools**:
- [ ] FTS5 search queries logged
- [ ] User cache operations logged

**Image Processing**:
- [ ] Image compression logged (if images >5MB tested)
- [ ] No PIL/image errors

**Errors**:
- [ ] No Python tracebacks
- [ ] No API errors
- [ ] No quota exceeded errors

---

## Test Results Summary

**Total Tests**: 18
**Tests Passed**: ___
**Tests Failed**: ___
**Critical Issues**: ___

---

## Quick Smoke Test (5 minutes)

If you're short on time, run these 5 tests only:

1. **Test 1** - Basic web search with citations
2. **Test 3** - Web fetch with URL
3. **Test 6** - Discord message search
4. **Test 8** - Image processing
5. **Test 18** - Complete workflow

These cover all major Phase 4 features.

---

## Troubleshooting

### Citations Not Appearing
**Check**:
1. `bots/alpha.yaml` has `citations_enabled: true`
2. Logs show `citations=True` when adding tools
3. Web search/fetch actually executed (check for server_tool_use logs)

### Quota Not Tracking
**Check**:
1. Logs show `Server tool used: web_search` or `web_fetch`
2. Quota file exists: `data/alpha_web_search_stats.json`
3. No errors in quota manager

### Discord Tools Not Working
**Check**:
1. Database files exist in `data/` directory
2. FTS5 virtual table created (check logs on startup)
3. User cache initialized

### Images Not Processing
**Check**:
1. PIL/Pillow installed
2. Image size logs appear
3. No compression errors

---

## Success Criteria

**Phase 4 is working if**:
- ✅ Web search returns current information with sources
- ✅ Web fetch analyzes URLs with citations
- ✅ Citations appear in all web tool responses
- ✅ Discord tools search messages and users
- ✅ Images are processed and described
- ✅ All tools work together in complex requests
- ✅ No critical errors in logs

**Critical Failures**:
- ❌ No citations appearing (compliance issue)
- ❌ Quota not tracking (cost tracking issue)
- ❌ Server tool errors
- ❌ Bot crashes on any test

---

## Notes Section

Use this space to record observations, issues, or unexpected behavior:

```
Test 1: ___________________________________________

Test 2: ___________________________________________

Test 3: ___________________________________________

[etc.]
```

---

## Completion

**Date Tested**: ___________
**Bot Version**: Alpha
**Tester**: ___________
**Overall Result**: [ ] PASS  [ ] FAIL  [ ] PARTIAL

**Ready for Production**: [ ] YES  [ ] NO  [ ] NEEDS FIXES
