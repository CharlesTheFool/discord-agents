# Beta Features Tracking

This document tracks beta features used in the Discord-Claude bot framework that may require updates when they graduate to stable.

**Last Updated:** 2025-10-04

---

## Active Beta Features

### 1. Context Management (`context-management-2025-06-27`)

**Status:** Active (Beta)
**SDK Version Required:** anthropic>=0.69.0
**Introduced:** September 29, 2025

**Current Implementation:**
- **File:** `core/reactive_engine.py`
- **Lines:** 157-173 (parameter construction), 206-209 (endpoint selection), 215-234 (response handling)
- **Beta Header:** `anthropic-beta: context-management-2025-06-27,prompt-caching-2024-07-31`
- **Endpoint:** `anthropic.beta.messages.create()` (when context editing enabled)
- **Parameter Structure:**
  ```python
  "context_management": {
      "edits": [
          {
              "type": "clear_tool_uses_20250919",
              "trigger": {"type": "input_tokens", "value": 100000},
              "keep": {"type": "tool_uses", "value": 3},
              "exclude_tools": ["memory"]
          }
      ]
  }
  ```

**Response Structure:**
- `response.context_management.applied_edits[]` - Array of applied edits
- `edit.cleared_tool_uses` - Number of tool uses cleared
- `edit.cleared_input_tokens` - Number of tokens cleared
- `response.usage.input_tokens` - Current token count (after clearing)

**Known Discrepancies from Docs:**
- ❌ `response.context_management.original_input_tokens` - Does NOT exist
- ✅ Must calculate: `original = current + cleared`
- ✅ Applied edits is array, not object with IDs as keys

**Migration Path (when stable):**
1. Check for stable API version announcement
2. Update or remove beta header
3. May switch from `beta.messages.create()` back to `messages.create()`
4. Verify response structure hasn't changed
5. Update parameter structure if needed
6. Test thoroughly in staging environment

**Configuration:**
- Enable/disable: `bots/{bot_id}.yaml` → `api.context_editing.enabled`
- Trigger threshold: `api.context_editing.trigger_tokens` (default: 100000)
- Keep recent: `api.context_editing.keep_tool_uses` (default: 3)
- Exclude tools: `api.context_editing.exclude_tools` (default: ["memory"])

---

## Graduated Beta Features

### Prompt Caching (`prompt-caching-2024-07-31`)

**Status:** Stable (still using beta header for compatibility)
**SDK Version:** anthropic>=0.61.0
**Graduated:** Unknown

**Current Implementation:**
- **File:** `core/reactive_engine.py`
- **Lines:** 175-177 (cache control on system prompt)
- **Beta Header:** Included in combined header with context management
- **Usage:** `{"type": "text", "text": "...", "cache_control": {"type": "ephemeral"}}`

**Notes:**
- May be stable but still requires beta header
- Combined header: `context-management-2025-06-27,prompt-caching-2024-07-31`
- No migration needed unless header requirements change

---

## Debugging Notes

### Context Management Bugs Fixed (2025-10-04)

**Issue 1: Wrong parameter structure**
- **Error:** `TypeError: AsyncMessages.create() got an unexpected keyword argument 'context_management'`
- **Cause:** Using `messages.create()` instead of `beta.messages.create()`
- **Fix:** Conditional endpoint selection based on `context_editing.enabled`
- **Commit:** Added in reactive_engine.py lines 206-209

**Issue 2: Incorrect parameter nesting**
- **Error:** Parameter rejected by API
- **Cause:** Missing `edits` array wrapper, wrong field structure for `trigger` and `keep`
- **Original (wrong):**
  ```python
  "context_management": {
      "clear_tool_uses_20250919": {
          "trigger": {"input_tokens": 100000}
      }
  }
  ```
- **Fixed (correct):**
  ```python
  "context_management": {
      "edits": [{
          "type": "clear_tool_uses_20250919",
          "trigger": {"type": "input_tokens", "value": 100000}
      }]
  }
  ```
- **Commit:** Fixed in reactive_engine.py lines 157-173

**Issue 3: Wrong response attributes**
- **Error:** `AttributeError: 'BetaContextManagementResponse' object has no attribute 'tool_uses_cleared'`
- **Cause:** Using incorrect attribute names from documentation
- **Fix:**
  - Use `edit.cleared_tool_uses` not `cm.tool_uses_cleared`
  - Use `edit.cleared_input_tokens` not `cm.input_tokens_cleared`
  - Calculate `original_tokens = current + cleared` instead of using `cm.original_input_tokens`
- **Commit:** Fixed in reactive_engine.py lines 215-234

**Verification:**
- Tested with 5 @mentions (see logs 17:51-17:59)
- Memory tool operations working
- No errors in production
- Context management parameter accepted by API (HTTP 200)

---

## Future Beta Feature Candidates

Features to watch for in Anthropic releases:

1. **Extended Thinking** - Currently using `thinking` parameter (may still be beta)
2. **Memory Tool** - Using `memory_20250818` type (may be beta)
3. **Multi-modal inputs** - When image processing added (Phase 4)
4. **Web search tools** - When search integration added (Phase 4)

---

## Testing Checklist

When beta features graduate to stable:

- [ ] Check Anthropic SDK changelog for graduation announcement
- [ ] Review API documentation for parameter/response changes
- [ ] Update beta headers (remove or update version)
- [ ] Test in development environment with new SDK version
- [ ] Verify response structure matches expectations
- [ ] Check for deprecation warnings in logs
- [ ] Update configuration documentation
- [ ] Update this tracking document
- [ ] Deploy to production with monitoring

---

## References

- [Anthropic Context Management Announcement](https://www.anthropic.com/news/context-management)
- [Context Editing Documentation](https://docs.claude.com/en/docs/build-with-claude/context-editing)
- [Memory Tool Documentation](https://docs.claude.com/en/docs/agents-and-tools/tool-use/memory-tool)
- [Anthropic SDK Python Changelog](https://github.com/anthropics/anthropic-sdk-python/blob/main/CHANGELOG.md)
- [SDK Examples - Memory](https://github.com/anthropics/anthropic-sdk-python/blob/main/examples/memory/basic.py)
