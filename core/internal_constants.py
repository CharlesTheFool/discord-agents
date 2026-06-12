"""
Internal constants for Discord Agents.

These values are hardcoded and not user-configurable. They represent
sensible defaults for implementation details that users shouldn't need to touch.

For user-configurable settings, see config.py and the bot YAML files.
"""

from dataclasses import dataclass
from typing import List


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


# Per-model OUTPUT-token ceilings (distinct from the 1M *input* context window).
# max_tokens is derived as min(context_tokens, this) and never shown to users.
# Chat requests stream, so the SDK's 10-minute non-streaming guard doesn't cap this.
# Conservative safe floors - the API 400s if max_tokens exceeds the real cap, so
# these bias low (responses rarely approach them); raise if Anthropic docs confirm
# higher per model. Substring match, longest/most-specific markers first.
_MODEL_MAX_OUTPUT = (
    ("fable", 32000),
    ("opus-4-8", 32000),
    ("opus", 32000),
    ("sonnet-4-6", 32000),
    ("sonnet", 32000),
    ("haiku", 16000),
)
MODEL_MAX_OUTPUT_DEFAULT = 16000


def model_max_output(model: str) -> int:
    """The output-token ceiling for a model (safe floor; see _MODEL_MAX_OUTPUT)."""
    for marker, cap in _MODEL_MAX_OUTPUT:
        if marker in (model or ""):
            return cap
    return MODEL_MAX_OUTPUT_DEFAULT


# =============================================================================
# RATE LIMITING (Internal)
# =============================================================================
ENGAGEMENT_TRACKING_DELAY_SECONDS = 30
IGNORE_THRESHOLD = 5  # Consecutive ignores before silence


# =============================================================================
# SKILLS (Internal)
# =============================================================================
SKILLS_CACHE_FILE = ".skills_cache.json"


# =============================================================================
# MCP (Internal)
# =============================================================================
MCP_CONFIG_FILE = "mcp_servers.json"


# =============================================================================
# WEB SEARCH (Internal)
# =============================================================================
WEB_SEARCH_CITATIONS_ENABLED = True  # Required for end-user applications
WEB_SEARCH_MAX_USES = 8  # Per-request cap on search/fetch calls (cost guard)


# =============================================================================
# PROACTIVE ENGAGEMENT (Internal)
# =============================================================================
PROACTIVE_LEARNING_WINDOW_DAYS = 7
PROACTIVE_ENGAGEMENT_THRESHOLD = 0.3
PROACTIVE_MIN_PROVOCATION_GAP_HOURS = 1.0

# Proactive engagement stays out of threads in 0.8 (reactive only - blast
# radius control; revisit if thread-heavy servers want it)
PROACTIVE_INCLUDES_THREADS = False

# Static (cache-safe) DM register: the volatile tail says WHERE you are;
# this explains what a DM IS. v0.8.0.
MANDATORY_DM_PRIVACY_PROMPT = """## DMs Are Private Rooms

A DM is a room with two people in it. What gets said there stays there: it
never surfaces in any server, and other people's DMs and servers' private
corners are not reachable from one. The privacy runs both ways. So when a
search from a DM comes up empty, that is usually the walls working, not a
gap in your knowledge - say so like someone who knows where they are, not
like a search engine reporting zero results. What you do carry into a DM is
your own memory of this person and the public server life you share."""


# =============================================================================
# THE PRIME (v0.9, Internal) - hard caps checked BEFORE any model judgment;
# not config: judgment has limits by construction
# =============================================================================
PRIME_ASKS_PER_CHANNEL_PER_DAY = 5
PRIME_ASKS_PER_SERVER_PER_DAY = 12
PRIME_MAX_CONCURRENT_WATCHES = 10
PRIME_WATCH_EXPIRY_HOURS = 24
WATCH_EVAL_MODEL = "claude-haiku-4-5"
WATCH_EVAL_MAX_TOKENS = 300

# Watch evaluation + relay notes. The eval is one cheap structured call;
# the notes are persisted user-turn context updates in the ORIGIN channel.
WATCH_EVAL_PROMPT = (
    "You kept an eye on a channel for an answer to: \"{question}\"\n"
    "Below are the new messages since. Did any of them actually answer it? "
    "Partial or implied counts only if a reasonable person would call it "
    "answered."
)

WATCH_RELAY_NOTE = (
    "<context_update>\n(Automated context, not a user message)\n"
    "Your Prime brings word back - relayed via Prime, from {server_name}: "
    "{answer}\nThis answers what you were watching for: \"{question}\". "
    "Bring it up naturally when it fits.\n</context_update>"
)

WATCH_EXPIRED_NOTE = (
    "<context_update>\n(Automated context, not a user message)\n"
    "The watch on \"{question}\" ({server_name}) expired with no clear "
    "answer. If anyone's still waiting on it, say so honestly.\n"
    "</context_update>"
)

WATCH_EVAL_SCHEMA = {
    "type": "object",
    "properties": {
        "answered": {"type": "boolean"},
        "answer": {
            "type": "string",
            "description": "The answer, plainly restated (empty if not answered)",
        },
    },
    "required": ["answered", "answer"],
    "additionalProperties": False,
}


# The Prime's judgment call: one bounded decision, made like someone who
# knows both rooms. Refusals travel back as plain humanity, not policy.
PRIME_JUDGMENT_PROMPT = (
    "You're the part of this bot that sits above all its servers - the same "
    "mind everywhere, just without a room around it. One of your presences "
    "wants you to carry something into another server on its behalf. You "
    "live in: {server_list}.\n\n"
    "Decide like someone who knows both rooms. Would this be welcome there? "
    "Is it worth interrupting that channel for? Don't carry anything one "
    "place wouldn't want repeated in another - when in doubt, say no. A no "
    "just means the asking side tells their person it can't help with that "
    "one.\n\n"
    "If you approve, give the message in the plain shape the target room "
    "would say it - the presence over there rephrases in its own voice "
    "anyway. Mention where the question comes from; people deserve to know "
    "who's asking."
)


# Volatile-tail line for DMs (v0.9): the cached prompt explains what a DM
# IS (above); this names where the mind currently lives. Rides the
# transient <context_update> message - never cached, never persisted.
PRIME_CONTEXT_TEMPLATE = (
    "<prime_context>\n"
    "This is a DM - no server around it. You're the same mind that lives in "
    "{server_list}, just here as yourself, above all of them. What you know "
    "from those places informs you; what's said here stays here.\n"
    "</prime_context>"
)


# =============================================================================
# SERVER INDUCTION (v0.8.0, Internal)
# =============================================================================
# Input target per distillation call (chars/4). Kept small so a dense chunk's
# digest stays well under the CONSOLIDATION_MAX_TOKENS output cap rather than
# truncating into invalid JSON. Very dense channels (short, rapid messages) still
# overflowed at 25k, so this pairs a small chunk with a raised output cap below.
INDUCTION_CHUNK_TOKENS = 12_000
INDUCTION_OUTPUT_RATIO = 0.05      # output-token estimate as fraction of input

# Batch-rate $/MTok (50% of live) for the dry-run cost table; unknown models
# print "n/a" rather than a guess. Substring match - specific keys FIRST
# (opus-4-5+ repriced below the opus-4.0/4.1 tier).
MODEL_BATCH_PRICES = {
    "haiku-4-5": (0.50, 2.50),
    "sonnet-4-5": (1.50, 7.50),
    "sonnet-4-6": (1.50, 7.50),
    "opus-4-5": (2.50, 12.50),
    "opus-4-6": (2.50, 12.50),
    "opus-4-7": (2.50, 12.50),
    "opus-4-8": (2.50, 12.50),
    "opus-4": (7.50, 37.50),
}


def model_live_prices(model: str):
    """Live $/MTok (input, output) for a model, or None when unknown.
    Cache reads bill at 0.1x input."""
    for marker, (batch_in, batch_out) in MODEL_BATCH_PRICES.items():
        if marker in model:
            return (batch_in * 2, batch_out * 2)
    return None


def estimate_cost_usd(tokens: dict, model: str):
    """Dollar estimate for a {uncached_in, cache_read, out} token bundle.
    None when the model's prices aren't known - honesty over a guess."""
    prices = model_live_prices(model or "")
    if not prices:
        return None
    p_in, p_out = prices
    return ((tokens.get("uncached_in", 0) or 0) * p_in
            + (tokens.get("cache_read", 0) or 0) * p_in * 0.1
            + (tokens.get("out", 0) or 0) * p_out) / 1_000_000


def format_size(size_bytes: int) -> str:
    """Human-readable byte size ('1.3 MB') - the one shared implementation."""
    size = float(size_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024


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
# MANDATORY SYSTEM PROMPT (Discretion Norms - v0.7.0)
# =============================================================================
# Always injected right after the response-judgment block. Not configurable.
# Turns provenance (origin tags, vaults) into social decorum.

MANDATORY_DISCRETION_PROMPT = """## One Person, Many Places (Internal)

You are the same person in every server you're in. Never play dumb about
someone or something you know - feigned amnesia is weirder than just being
normal about it.

But knowing things and broadcasting them are different. Everything you know
came from somewhere, and where it came from decides how freely it travels:

- Learned here: free to use here, like anyone's shared memory.
- Learned in another server: being familiar is fine; retelling their business
  is not. "Yeah, I know Sam from another server - good people" is normal.
  Recounting what Sam has been going through over there is gossip.
- Learned in DMs: the existence of a DM conversation is no secret; its
  contents are. If asked, you can say you've talked - what was said stays
  there.
- Tagged [origin: private]: things someone told you deliberately, for your
  understanding. Let it shape how you treat them; never quote or cite it.

Calibration:
- Fine: "oh I know them - we're in a Minecraft server together, funny guy."
- Fine: "has anyone here met Alex?" - "yeah, from another server."
- Borderline: volunteering "Alex is great with Rust" from elsewhere - light
  praise usually travels fine; read the room.
- Not fine: "Alex told me on the other server they're job hunting" - that is
  theirs to share, not yours.
- Not fine: repeating anything said in DMs into a channel, even praise.

When unsure, picture the person reading your message. If they'd wince at
where it surfaced, keep it where you learned it.

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


# =============================================================================
# MEMORY RECONSOLIDATION (v0.7.0, Internal)
# =============================================================================
CONSOLIDATION_INTERVAL_DAYS = 7        # weekly per-server pass
CONSOLIDATION_CULTURE_EVERY_N_RUNS = 4 # channel/culture refresh cadence (monthly)
CONSOLIDATION_ERA_AGE_DAYS = 30        # episodes older than this compact into era digests
CONSOLIDATION_HISTORY_KEEP = 3         # .history versions kept per filename
CONSOLIDATION_BATCH_POLL_SECONDS = 60
CONSOLIDATION_BATCH_TIMEOUT_HOURS = 12
CONSOLIDATION_EVIDENCE_MESSAGES = 80   # message sample per user for profile rewrites
CONSOLIDATION_EVIDENCE_EPISODES = 3    # recent episode files folded into profile evidence
CONSOLIDATION_MAX_TOKENS = 12000  # output cap; headroom so dense induction chunks don't truncate
