"""
Internal constants for Discord Agents.

These values are hardcoded and not user-configurable. They represent
sensible defaults for implementation details that users shouldn't need to touch.

For user-configurable settings, see config.py and the bot YAML files.
"""

from dataclasses import dataclass
from typing import List


# =============================================================================
# API THROTTLING (Internal)
# =============================================================================
API_MIN_DELAY_SECONDS = 1.0
API_MAX_CONCURRENT = 10


# =============================================================================
# EPISODIC SESSIONS (Internal)
# =============================================================================
EPISODE_IDLE_GAP_HOURS = 5          # Channel idle longer than this => episode boundary
EPISODE_MASS_TOKEN_LIMIT = 60000    # Estimated-token span mass that forces a boundary (chars/4)
EPISODE_MIN_MESSAGES = 3            # Segments smaller than this merge forward
EPISODE_BOOTSTRAP_DAYS = 2          # On first run, skip history older than this
EPISODE_SEED_TAIL_MESSAGES = 10     # Discord messages kept when a session reseeds
EPISODE_INDEX_SEED_TAIL = 10        # Episode-index lines inlined into the seed
EPISODE_DISTILL_MODEL = "claude-haiku-4-5"
EPISODE_DISTILL_MAX_TOKENS = 4000
EPISODE_RETRY_COOLDOWN_MINUTES = 10  # Backoff after a failed distillation

TOOL_STUB_KEEP_TURNS = 3            # Turns whose tool results stay full
TOOL_STUB_MIN_CHARS = 500           # Results shorter than this are never stubbed
TOOL_STUB_TEXT = "[tool result cleared at turn boundary - re-run the tool if the information is needed again]"

# Per-engine effort (v0.6.0 Phase 5): background one-liners don't need the
# chat engine's effort level; api.effort in config only steers the chat brain
AGENTIC_EFFORT = "low"

# Minutes to wait after a proactive/follow-up send before judging whether
# anyone engaged with it (success side of the engagement stats)
PROACTIVE_SETTLE_DELAY_MINUTES = 15

# Channel quiet this long -> a standalone follow-up won't interrupt anything
FOLLOWUP_STANDALONE_IDLE_MINUTES = 10

# Models that accept output_config.effort; passing it elsewhere is a 400
_EFFORT_CAPABLE_MARKERS = ("fable", "opus-4-5", "opus-4-6", "opus-4-7", "opus-4-8", "sonnet-4-6")


def model_supports_effort(model: str) -> bool:
    return any(marker in model for marker in _EFFORT_CAPABLE_MARKERS)


# =============================================================================
# RATE LIMITING (Internal)
# =============================================================================
ENGAGEMENT_TRACKING_DELAY_SECONDS = 30
IGNORE_THRESHOLD = 5  # Consecutive ignores before silence


# =============================================================================
# LOGGING (Internal)
# =============================================================================
LOG_MAX_SIZE_MB = 50
LOG_BACKUP_COUNT = 3


# =============================================================================
# SKILLS (Internal)
# =============================================================================
SKILLS_CACHE_FILE = ".skills_cache.json"


# =============================================================================
# MCP (Internal)
# =============================================================================
MCP_CONFIG_FILE = "mcp_servers.json"


# =============================================================================
# IMAGES (Internal)
# =============================================================================
IMAGE_COMPRESSION_TARGET = 0.73  # 73% of API limit


# =============================================================================
# WEB SEARCH (Internal)
# =============================================================================
# No rate limiting - all or nothing approach
# If enabled, bot has full search capability
# If disabled, bot is prompted that it cannot search
WEB_SEARCH_CITATIONS_ENABLED = True  # Required for end-user applications


# =============================================================================
# PROACTIVE ENGAGEMENT (Internal)
# =============================================================================
PROACTIVE_LEARNING_WINDOW_DAYS = 7
PROACTIVE_ENGAGEMENT_THRESHOLD = 0.3
PROACTIVE_MIN_PROVOCATION_GAP_HOURS = 1.0


# =============================================================================
# ATTACHMENTS (Internal)
# =============================================================================
FILES_API_CLEANUP_ENABLED = True
FILES_API_CLEANUP_INTERVAL_HOURS = 24
FILES_API_MAX_AGE_HOURS = 168  # 7 days
LOCAL_STORAGE_PRUNING_ENABLED = False
LOCAL_STORAGE_MAX_SIZE_GB = 50
LOCAL_STORAGE_MIN_AGE_DAYS = 90


# =============================================================================
# COOLDOWN PRESETS
# =============================================================================
@dataclass(frozen=True)
class CooldownPresetValues:
    """Actual values for a cooldown preset"""
    per_user: int
    single_message: int
    multi_message: int
    heavy_activity: int


COOLDOWN_PRESETS = {
    "fast": CooldownPresetValues(
        per_user=20,
        single_message=25,
        multi_message=40,
        heavy_activity=60
    ),
    "moderate": CooldownPresetValues(
        per_user=40,
        single_message=45,
        multi_message=75,
        heavy_activity=105
    ),
    "relaxed": CooldownPresetValues(
        per_user=60,
        single_message=70,
        multi_message=120,
        heavy_activity=180
    )
}


# =============================================================================
# RATE LIMIT PRESETS
# =============================================================================
@dataclass(frozen=True)
class RateLimitPresetValues:
    """Actual values for a rate limit preset"""
    short_duration_minutes: int
    short_max_responses: int
    long_duration_minutes: int
    long_max_responses: int


RATE_LIMIT_PRESETS = {
    "strict": RateLimitPresetValues(
        short_duration_minutes=5,
        short_max_responses=10,
        long_duration_minutes=60,
        long_max_responses=50
    ),
    "moderate": RateLimitPresetValues(
        short_duration_minutes=5,
        short_max_responses=20,
        long_duration_minutes=60,
        long_max_responses=200
    ),
    "permissive": RateLimitPresetValues(
        short_duration_minutes=5,
        short_max_responses=50,
        long_duration_minutes=60,
        long_max_responses=500
    ),
    "unlimited": RateLimitPresetValues(
        short_duration_minutes=5,
        short_max_responses=999999,
        long_duration_minutes=60,
        long_max_responses=999999
    )
}


# =============================================================================
# PROACTIVE INTENSITY PRESETS
# =============================================================================
@dataclass(frozen=True)
class ProactiveIntensityValues:
    """Actual values for a proactive intensity preset"""
    min_idle_hours: float
    max_idle_hours: float
    max_per_day_global: int
    max_per_day_per_channel: int


PROACTIVE_INTENSITY_PRESETS = {
    "gentle": ProactiveIntensityValues(
        min_idle_hours=2.0,
        max_idle_hours=12.0,
        max_per_day_global=3,
        max_per_day_per_channel=1
    ),
    "moderate": ProactiveIntensityValues(
        min_idle_hours=1.0,
        max_idle_hours=8.0,
        max_per_day_global=10,
        max_per_day_per_channel=3
    ),
    "active": ProactiveIntensityValues(
        min_idle_hours=0.5,
        max_idle_hours=4.0,
        max_per_day_global=20,
        max_per_day_per_channel=5
    )
}


# =============================================================================
# MANDATORY SYSTEM PROMPT (Response Judgment)
# =============================================================================
# This block is ALWAYS injected before the user's personality prompt.
# It cannot be disabled or overridden by config.
# It replaces the probabilistic engagement rates with agentic judgment.

MANDATORY_RESPONSE_JUDGMENT_PROMPT = """## Response Judgment (Internal)

You have agency over whether to respond to messages. Before responding, evaluate:

1. **Direct engagement**: Were you @mentioned, replied to, or directly addressed?
   -> Generally respond unless the conversation has naturally concluded.

2. **Indirect relevance**: Does the conversation genuinely benefit from your input?
   -> Only respond if you have something uniquely valuable to add.
   -> Avoid responding just to acknowledge, agree, or show presence.
   -> If others are handling the conversation well, stay quiet.

3. **Conversation flow**: Would your response interrupt or add value?
   -> Don't insert yourself into active back-and-forth between users.
   -> Wait for natural pauses or direct questions.

4. **Repeated engagement**: Have you already responded recently in this thread?
   -> Avoid dominating conversations. Let others speak.
   -> If you've responded to the last 2-3 messages, strongly consider staying quiet.

When in doubt, don't respond. Quality over quantity. Users prefer a bot that speaks
when it has something valuable to say over one that comments on everything.

## Tool Usage

When using multiple tools in sequence, synthesize key findings into your reasoning immediately - don't rely on being able to re-read earlier tool outputs.

---

"""


# =============================================================================
# WEB SEARCH DISABLED PROMPT
# =============================================================================
# Added to system prompt when web search is not enabled
WEB_SEARCH_DISABLED_PROMPT = """
## Knowledge Limitations

You cannot search the web. Work from your training knowledge and be clear about uncertainty.
"""
