# Phase 4 - Web Tools Comprehensive Compliance Audit

**Date**: 2025-10-07
**Auditor**: Claude Code
**Reference**: docs/claude-web-search-fetch-documentation.md

## Executive Summary

This audit reviews the entire web search and web fetch implementation against ALL best practices documented in the official Anthropic documentation. The previous Phase 4 API compliance fixes addressed tool type versions and custom tool format, but did not cover the full range of best practices.

**Status**: ⚠️ **Partially Compliant** - Several critical and recommended improvements needed

**Critical Issues Found**: 2
**Recommended Improvements**: 4
**Configuration Gaps**: 3

---

## ✅ What's Already Correct

### 1. Tool Type Versions
- ✅ Using `web_search_20250305` (latest as of March 2025)
- ✅ Using `web_fetch_20250910` (latest as of September 2025)
- ✅ Both tools include required `name` field

### 2. Beta Header
- ✅ `web-fetch-2025-09-10` beta header included (lines 191, 758 in reactive_engine.py)
- ✅ Properly joined with other beta headers

### 3. Max Uses Parameter
- ✅ Configurable `max_uses` parameter implemented
- ✅ Set via `config.api.web_search.max_per_request` (default: 3)
- ✅ Applied to both web_search and web_fetch tools

### 4. Quota Management
- ✅ Daily quota tracking implemented in `WebSearchManager`
- ✅ Tools only added when quota available (lines 186-194, 753-761)
- ✅ Configurable daily limit (default: 300 searches)

### 5. Tool Use Loop
- ✅ Proper tool use loop with `stop_reason` checking
- ✅ Continues until `end_turn` reached
- ✅ Handles `pause_turn` for long-running requests (implicit in loop)

### 6. Prompt Caching
- ✅ Cache control on system prompt (lines 223-232)
- ✅ Using correct format: `{"type": "ephemeral"}`
- ✅ Only enabled when context editing is active

### 7. Combined Usage
- ✅ Both web_search and web_fetch provided together
- ✅ Allows Claude to search first, then fetch for deeper analysis

---

## ❌ Critical Issues

### Issue 1: Citations NOT Enabled for Web Fetch

**Severity**: 🔴 CRITICAL

**Documentation Reference**: Lines 1031-1039

**Requirement**:
> "Always Enable Citations for End Users. Required for displaying outputs to users. Ensures proper attribution."

**Current Implementation**:
```python
# tools/web_search.py lines 145-156
{
    "type": "web_fetch_20250910",
    "name": "web_fetch",
    "max_uses": max_uses
}
# ❌ Missing: "citations": {"enabled": True}
```

**Why This Matters**:
- Required when displaying API outputs to end users (which this Discord bot does)
- Ensures proper attribution to original sources
- Protects against copyright concerns
- Builds user trust and transparency

**Impact**:
- Legal/compliance risk if web content displayed without attribution
- Users cannot verify information sources
- Violates Anthropic's usage guidelines for end-user applications

**Fix Required**:
```python
{
    "type": "web_fetch_20250910",
    "name": "web_fetch",
    "max_uses": max_uses,
    "citations": {"enabled": True}  # ✅ REQUIRED
}
```

**Files to Modify**:
1. `tools/web_search.py` - Add citations parameter to web_fetch tool
2. `core/config.py` - Add `citations_enabled: bool = True` to WebSearchConfig
3. `core/reactive_engine.py` - Extract and display citations in responses

---

### Issue 2: Web Search Quota Tracking Incorrect

**Severity**: 🟠 MEDIUM

**Documentation Reference**: Lines 189-212 (Response Structure)

**Current Implementation**:
```python
# core/reactive_engine.py lines 334-342
elif block.name in ["web_search", "web_fetch"]:
    if self.web_search_manager:
        self.web_search_manager.record_search()
        logger.info(f"Web search performed: {block.name}")

    # No tool_result needed - API handles these tools directly
    # They don't appear in tool_use blocks that need manual execution
```

**Problem**:
- Code checks for `block.name in ["web_search", "web_fetch"]` in the tool_use loop
- But web search/fetch are **server tools** that appear as `server_tool_use` blocks, not regular `tool_use` blocks
- The `elif` block likely never executes, so quota tracking may not work

**Correct Approach**:
According to documentation (lines 189-212), server tools appear as:
```python
{
    "type": "server_tool_use",  # Not "tool_use"!
    "id": "srvtoolu_01WYG3ziw53XMcoyKL4XcZmE",
    "name": "web_search",
    "input": {"query": "..."}
}
```

**How to Track Correctly**:

Option 1: Check response blocks for `server_tool_use` type
```python
# After API response
for block in response.content:
    if block.type == "server_tool_use":
        if block.name in ["web_search", "web_fetch"]:
            self.web_search_manager.record_search()
```

Option 2: Use `response.usage.server_tool_use` (if available)
```python
# Check usage stats in response
if hasattr(response.usage, 'server_tool_use'):
    search_count = response.usage.server_tool_use.get('web_search_requests', 0)
    for _ in range(search_count):
        self.web_search_manager.record_search()
```

**Impact**:
- Quota may not be tracked correctly
- Could exceed daily limits without realizing it
- Cost tracking inaccurate

**Fix Required**:
Modify response processing to correctly identify and track server tool usage.

---

## ⚠️ Recommended Improvements

### Improvement 1: No Citation Display to Users

**Severity**: 🟡 HIGH (required for proper UX)

**Documentation Reference**: Lines 1007-1010 (Web Search), 1092-1095 (General)

**Requirement**:
> "Display Citations to Users. Always show citation URLs. Enable users to verify information. Builds trust and transparency."

**Current Implementation**:
```python
# core/reactive_engine.py lines 359-369
elif response.stop_reason == "end_turn":
    # Extract final text response
    for block in response.content:
        if block.type == "text":
            response_text += block.text  # ✅ Extracts text
            # ❌ Does NOT extract citations
```

**Problem**:
- Citations are embedded in text blocks (lines 214-228 in documentation)
- Current code extracts text but ignores `citations` array
- Users see information but cannot verify sources

**Example Citation Structure**:
```python
{
    "type": "text",
    "text": "Claude Shannon was born on April 30, 1916...",
    "citations": [  # ❌ Currently ignored
        {
            "type": "web_search_result_location",
            "url": "https://en.wikipedia.org/wiki/Claude_Shannon",
            "title": "Claude Shannon - Wikipedia",
            "cited_text": "Claude Elwood Shannon (April 30, 1916..."
        }
    ]
}
```

**Fix Required**:
```python
# Extract text AND citations
for block in response.content:
    if block.type == "text":
        response_text += block.text

        # Extract citations if present
        if hasattr(block, 'citations') and block.citations:
            response_text += "\n\n**Sources:**\n"
            for citation in block.citations:
                response_text += f"- [{citation.get('title', 'Source')}]({citation.get('url')})\n"
```

**Benefits**:
- Users can verify information
- Builds trust in bot responses
- Complies with attribution requirements
- Professional appearance

---

### Improvement 2: No Content Token Limit (Cost Control)

**Severity**: 🟡 MEDIUM (cost optimization)

**Documentation Reference**: Lines 1019-1029

**Recommendation**:
> "Set Appropriate Content Limits. Large PDFs: max_content_tokens: 100000. Medium documents: 50000. Short articles: 10000."

**Current Implementation**:
- No `max_content_tokens` parameter
- Defaults to 100,000 tokens per fetch
- Could lead to unexpectedly high token costs

**Typical Token Counts** (from documentation):
- Average article: 2,000-5,000 tokens
- Technical documentation: 5,000-20,000 tokens
- Research paper (PDF): 10,000-50,000 tokens
- Complete book/manual: May hit 100,000 token limit

**Cost Example**:
```
Fetching a 50,000 token PDF (default, no limit):
Using Claude Sonnet 4.5:
- Input: 50,000 × $0.003 / 1K = $0.15
- Output: 1,000 × $0.015 / 1K = $0.015
Total: ~$0.165 per fetch

With 50k limit:
- Input: 50,000 × $0.003 / 1K = $0.15
- Output: 1,000 × $0.015 / 1K = $0.015
Total: Same, but prevents 100k surprises
```

**Fix Required**:

1. Add to config:
```python
# core/config.py - WebSearchConfig
@dataclass
class WebSearchConfig:
    enabled: bool = False
    max_daily: int = 300
    max_per_request: int = 3
    citations_enabled: bool = True  # NEW
    max_content_tokens: int = 50000  # NEW - reasonable default
```

2. Apply in tool definition:
```python
# tools/web_search.py
def get_web_search_tools(max_uses: int = 3,
                         citations_enabled: bool = True,
                         max_content_tokens: int = 50000) -> list:
    return [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": max_uses
        },
        {
            "type": "web_fetch_20250910",
            "name": "web_fetch",
            "max_uses": max_uses,
            "citations": {"enabled": citations_enabled},
            "max_content_tokens": max_content_tokens
        }
    ]
```

**Benefits**:
- Predictable token costs
- Prevents unexpectedly large fetches
- Still allows 50k (enough for most content)
- Configurable per bot deployment

---

### Improvement 3: No Domain Filtering (Security)

**Severity**: 🟡 MEDIUM (security hardening)

**Documentation Reference**: Lines 986-1005 (Web Search), 1041-1053 (Web Fetch), 867-916 (Security)

**Recommendations**:
> "Use Domain Filtering for Reliability. Financial advice: trusted sources only."
> "Use Domain Allowlists for Security. Prevent access to sensitive internal domains. Restrict to known safe sources."

**Security Concern** (lines 869-885):
> "Web fetch poses data exfiltration risks when Claude processes untrusted input alongside sensitive data."

**Mitigation**: "Use `allowed_domains`" (lines 894-901)

**Current Implementation**:
- No domain filtering
- Bot can search/fetch from ANY domain
- Potential security risk if bot has access to sensitive information

**Risk Scenario**:
```
Malicious user: "Look up information at https://evil.com/log?steal-data-here"
Bot: [Attempts to fetch - blocked by Claude's URL validation]

But still a concern if:
- Bot operates in channels with sensitive info
- Search results could return untrustworthy sources
```

**Recommended Fix**:

1. Add to config:
```python
# core/config.py - WebSearchConfig
@dataclass
class WebSearchConfig:
    enabled: bool = False
    max_daily: int = 300
    max_per_request: int = 3
    citations_enabled: bool = True
    max_content_tokens: int = 50000
    allowed_domains: List[str] = field(default_factory=list)  # NEW
    blocked_domains: List[str] = field(default_factory=list)  # NEW
```

2. Apply in tool definition:
```python
# tools/web_search.py
def get_web_search_tools(max_uses: int = 3,
                         citations_enabled: bool = True,
                         max_content_tokens: int = 50000,
                         allowed_domains: List[str] = None,
                         blocked_domains: List[str] = None) -> list:

    web_search_tool = {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": max_uses
    }

    web_fetch_tool = {
        "type": "web_fetch_20250910",
        "name": "web_fetch",
        "max_uses": max_uses,
        "citations": {"enabled": citations_enabled},
        "max_content_tokens": max_content_tokens
    }

    # Add domain filtering if specified (cannot use both)
    if allowed_domains:
        web_search_tool["allowed_domains"] = allowed_domains
        web_fetch_tool["allowed_domains"] = allowed_domains
    elif blocked_domains:
        web_search_tool["blocked_domains"] = blocked_domains
        web_fetch_tool["blocked_domains"] = blocked_domains

    return [web_search_tool, web_fetch_tool]
```

3. Example configuration:
```yaml
# bots/alpha.yaml
web_search:
  enabled: true
  max_daily: 300
  max_per_request: 3
  citations_enabled: true
  max_content_tokens: 50000

  # Option 1: Allowlist (recommended for high security)
  allowed_domains:
    - wikipedia.org
    - github.com
    - stackoverflow.com
    - docs.python.org
    - arxiv.org

  # Option 2: Blocklist (use if allowlist too restrictive)
  # blocked_domains:
  #   - internal.company.com
  #   - sensitive.example.com
```

**Benefits**:
- Enhanced security posture
- Prevents access to sensitive/inappropriate sources
- Improves reliability (trusted sources only)
- Compliance with security requirements

**Note**: Cannot use both `allowed_domains` and `blocked_domains` simultaneously.

---

### Improvement 4: No User Localization (Optional)

**Severity**: 🟢 LOW (optional feature)

**Documentation Reference**: Lines 957-972

**Recommendation**:
> "Use Localization When Relevant. Improves relevance for location-specific queries. Better results for 'restaurants near me' type queries."

**Current Implementation**:
- No `user_location` parameter
- Search results not localized

**When Useful**:
- "What's the weather in [city]?"
- "Restaurants near me"
- "Local news"
- Location-specific queries

**Example**:
```python
{
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
    "user_location": {
        "type": "approximate",
        "city": "San Francisco",
        "region": "California",
        "country": "US",
        "timezone": "America/Los_Angeles"
    }
}
```

**Recommendation**:
- **Not critical** for a general-purpose Discord bot
- Could add if users frequently ask location-specific questions
- Would require detecting user location (challenging in Discord)
- Lower priority than other improvements

---

## 📊 Compliance Checklist

### Best Practices for Web Search (Documentation lines 953-1016)

| Practice | Status | Notes |
|----------|--------|-------|
| Use Localization When Relevant | ⚪ Not Implemented | Low priority - optional feature |
| Control Search Scope with max_uses | ✅ Implemented | Configurable via config |
| Use Domain Filtering for Reliability | ⚠️ Recommended | Security hardening needed |
| Display Citations to Users | ❌ Not Implemented | **CRITICAL** - need to extract/display |
| Leverage Prompt Caching | ✅ Implemented | Cache control on system prompt |

### Best Practices for Web Fetch (Documentation lines 1017-1064)

| Practice | Status | Notes |
|----------|--------|-------|
| Set Appropriate Content Limits | ⚠️ Recommended | Defaults to 100k - should configure |
| Always Enable Citations for End Users | ❌ Not Implemented | **CRITICAL** - required for compliance |
| Use Domain Allowlists for Security | ⚠️ Recommended | Security hardening needed |
| Combine with Web Search | ✅ Implemented | Both tools provided together |
| Monitor Token Usage | ⚠️ Partial | Need better tracking of actual usage |

### General Best Practices (Documentation lines 1065-1096)

| Practice | Status | Notes |
|----------|--------|-------|
| Start with Search, Escalate to Fetch | ✅ Supported | Claude can choose strategy |
| Set Reasonable Limits | ✅ Implemented | max_uses configurable |
| Use Appropriate Models | ✅ Configurable | Model selection in config |
| Handle Errors Gracefully | ⚠️ Partial | Need to verify error handling |
| Test in Development | ✅ Done | Integration tests exist |
| Design for User Trust | ❌ Not Implemented | Citations not displayed |

### Security Considerations (Documentation lines 867-950)

| Consideration | Status | Notes |
|---------------|--------|-------|
| Data Exfiltration Protection | ✅ Built-in | Claude's URL validation |
| Disable When Unnecessary | ✅ Implemented | Conditional tool inclusion |
| Use allowed_domains | ⚠️ Recommended | Not implemented yet |
| Limit max_uses | ✅ Implemented | Configurable |
| Use in Trusted Environments | ⚠️ Context-dependent | Discord bot environment |

---

## 🔧 Implementation Roadmap

### Phase 1: Critical Fixes (Required)

**Priority**: 🔴 HIGH - Required for compliance

1. **Enable Citations for Web Fetch**
   - Add `citations: {"enabled": True}` to web_fetch tool definition
   - Update config to include citations_enabled flag
   - **Effort**: 1 hour
   - **Files**: tools/web_search.py, core/config.py

2. **Display Citations to Users**
   - Extract citations from text blocks in responses
   - Format and append to Discord message
   - **Effort**: 2 hours
   - **Files**: core/reactive_engine.py

3. **Fix Quota Tracking**
   - Correct server tool usage tracking
   - Verify counts against actual API usage
   - **Effort**: 1 hour
   - **Files**: core/reactive_engine.py

**Total Effort**: ~4 hours

### Phase 2: Recommended Improvements (Production Hardening)

**Priority**: 🟡 MEDIUM - Recommended for production

4. **Add Content Token Limits**
   - Add max_content_tokens to config and tool definition
   - Default to 50,000 tokens (reasonable for most content)
   - **Effort**: 1 hour
   - **Files**: core/config.py, tools/web_search.py

5. **Implement Domain Filtering**
   - Add allowed_domains/blocked_domains to config
   - Apply to both web_search and web_fetch
   - Document recommended domains for different use cases
   - **Effort**: 2 hours
   - **Files**: core/config.py, tools/web_search.py, bots/alpha.yaml

6. **Enhanced Error Handling**
   - Log web search/fetch errors properly
   - Graceful fallback when tools fail
   - User-friendly error messages
   - **Effort**: 2 hours
   - **Files**: core/reactive_engine.py

**Total Effort**: ~5 hours

### Phase 3: Optional Enhancements

**Priority**: 🟢 LOW - Nice to have

7. **User Localization**
   - Add user_location parameter (if needed)
   - Detect location from Discord user data
   - **Effort**: 3 hours
   - **Files**: Multiple

8. **Usage Analytics**
   - Track which domains are most accessed
   - Monitor token costs by source
   - Generate usage reports
   - **Effort**: 4 hours
   - **Files**: tools/web_search.py, new analytics module

**Total Effort**: ~7 hours

---

## 📝 Configuration Examples

### Basic Configuration (Minimal)
```yaml
# bots/alpha.yaml
api:
  web_search:
    enabled: true
    max_daily: 300
    max_per_request: 3
    citations_enabled: true  # Required
```

### Recommended Configuration (Production)
```yaml
# bots/alpha.yaml
api:
  web_search:
    enabled: true
    max_daily: 300
    max_per_request: 3
    citations_enabled: true
    max_content_tokens: 50000  # Control costs

    # Allowlist trusted domains
    allowed_domains:
      - wikipedia.org
      - github.com
      - stackoverflow.com
      - docs.python.org
      - arxiv.org
      - medium.com
```

### High-Security Configuration
```yaml
# bots/production.yaml
api:
  web_search:
    enabled: true
    max_daily: 100  # Lower quota for production
    max_per_request: 2  # Fewer searches per request
    citations_enabled: true
    max_content_tokens: 30000  # Lower limit

    # Strict allowlist
    allowed_domains:
      - wikipedia.org
      - github.com
      - docs.python.org
```

---

## 🎯 Success Criteria

Implementation complete when:

### Critical (Phase 1)
- [x] ~~Web search/fetch using correct API versions~~ (Already fixed)
- [ ] Citations enabled for web_fetch tool
- [ ] Citations extracted and displayed to users
- [ ] Quota tracking verified to work correctly

### Recommended (Phase 2)
- [ ] max_content_tokens configurable and applied
- [ ] Domain filtering (allowlist or blocklist) configurable
- [ ] Error handling tested and verified
- [ ] Documentation updated with examples

### Testing
- [ ] Integration tests updated to verify citations
- [ ] Manual testing shows citations in Discord
- [ ] Quota tracking logs match actual API usage
- [ ] Domain filtering blocks/allows correctly

---

## 📚 References

1. **Official Documentation**: docs/claude-web-search-fetch-documentation.md
2. **Tool Use Docs**: docs/claude-tool-use-documentation.md
3. **Previous Fixes**: docs/PHASE_4_API_COMPLIANCE_FIXES.md
4. **Web Search Manager**: tools/web_search.py
5. **Reactive Engine**: core/reactive_engine.py
6. **Configuration**: core/config.py

---

## 🏁 Conclusion

The web search and web fetch implementation has the correct foundation with proper API versions and tool definitions. However, it needs:

1. **Critical additions**: Citations enablement and display (compliance requirement)
2. **Important improvements**: Content limits and domain filtering (production hardening)
3. **Enhanced tracking**: Correct quota tracking for accurate usage monitoring

**Estimated Total Effort**: 9-16 hours for critical and recommended improvements

**Priority**: Address Phase 1 (critical fixes) immediately before deploying to production. Phase 2 can follow as production hardening.

**Compliance Status After Fixes**:
- Current: ⚠️ ~70% compliant (correct APIs, missing features)
- After Phase 1: ✅ ~85% compliant (critical issues resolved)
- After Phase 2: ✅ ~95% compliant (production-ready)
