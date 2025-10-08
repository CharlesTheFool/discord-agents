"""
Configuration system for Discord-Claude Bot Framework.

Loads and validates bot configurations from YAML files.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict
import yaml


@dataclass
class PersonalityConfig:
    """Bot personality and engagement settings"""
    base_prompt: str
    formality: float = 0.2  # 0=casual, 1=formal
    emoji_usage: str = "never"  # never | rare | moderate | frequent
    reaction_usage: str = "moderate"  # never | rare | moderate | frequent
    formatting: str = "minimal"  # minimal | moderate | rich

    # Engagement rates
    mention_response_rate: float = 1.0  # Always respond to mentions
    technical_help_rate: float = 0.8
    humor_response_rate: float = 0.4
    cold_conversation_rate: float = 0.1
    warm_conversation_rate: float = 0.25
    hot_conversation_rate: float = 0.4


@dataclass
class CooldownsConfig:
    """Response cooldown settings"""
    per_user: int = 40  # seconds
    single_message: int = 45  # seconds
    multi_message: int = 75  # seconds
    heavy_activity: int = 105  # seconds


@dataclass
class ReactiveConfig:
    """Reactive engine configuration"""
    enabled: bool = True
    check_interval_seconds: int = 30
    context_window: int = 20  # Recent messages to include
    cooldowns: CooldownsConfig = field(default_factory=CooldownsConfig)


@dataclass
class FollowupsConfig:
    """Follow-up system configuration (Phase 3)"""
    enabled: bool = False  # Disabled for Phase 1
    auto_create: bool = True
    max_pending: int = 20
    priority_threshold: str = "medium"
    follow_up_delay_days: int = 1
    max_age_days: int = 14


@dataclass
class ProactiveConfig:
    """Proactive engagement configuration (Phase 3)"""
    enabled: bool = False  # Disabled for Phase 1
    min_idle_hours: float = 1.0
    max_idle_hours: float = 8.0
    min_provocation_gap_hours: float = 1.0
    max_per_day_global: int = 10
    max_per_day_per_channel: int = 3
    engagement_threshold: float = 0.3
    learning_window_days: int = 7
    quiet_hours: List[int] = field(default_factory=lambda: [0, 6])
    allowed_channels: List[str] = field(default_factory=list)


@dataclass
class AgenticConfig:
    """Agentic engine configuration"""
    enabled: bool = False  # Disabled for Phase 1
    check_interval_hours: int = 1
    followups: FollowupsConfig = field(default_factory=FollowupsConfig)
    proactive: ProactiveConfig = field(default_factory=ProactiveConfig)


@dataclass
class ContextEditingConfig:
    """Context editing configuration"""
    enabled: bool = True
    trigger_tokens: int = 100000
    keep_tool_uses: int = 3
    exclude_tools: List[str] = field(default_factory=lambda: ["memory"])


@dataclass
class ExtendedThinkingConfig:
    """Extended thinking configuration"""
    enabled: bool = True
    budget_tokens: int = 10000  # Max tokens for thinking (min: 1024)


@dataclass
class ThrottlingConfig:
    """API throttling settings"""
    min_delay_seconds: float = 1.0
    max_concurrent: int = 10


@dataclass
class WebSearchConfig:
    """Web search configuration (Phase 4)"""
    enabled: bool = False  # Disabled for Phase 1
    max_daily: int = 300
    max_per_request: int = 3
    citations_enabled: bool = True  # Required for end-user applications


@dataclass
class APIConfig:
    """Claude API configuration"""
    model: str = "claude-sonnet-4-5-20250929"
    max_tokens: int = 4096
    extended_thinking: ExtendedThinkingConfig = field(default_factory=ExtendedThinkingConfig)
    context_editing: ContextEditingConfig = field(default_factory=ContextEditingConfig)
    throttling: ThrottlingConfig = field(default_factory=ThrottlingConfig)
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    # Note: temperature removed - not compatible with extended thinking


@dataclass
class RateLimitWindowConfig:
    """Rate limit window configuration"""
    duration_minutes: int
    max_responses: int


@dataclass
class RateLimitingConfig:
    """Rate limiting configuration (SimpleRateLimiter)"""
    short: RateLimitWindowConfig = field(
        default_factory=lambda: RateLimitWindowConfig(duration_minutes=5, max_responses=20)
    )
    long: RateLimitWindowConfig = field(
        default_factory=lambda: RateLimitWindowConfig(duration_minutes=60, max_responses=200)
    )
    ignore_threshold: int = 5  # Consecutive ignores before silence
    engagement_tracking_delay: int = 30  # Seconds


@dataclass
class ImagesConfig:
    """Image processing configuration (Phase 4)"""
    enabled: bool = False  # Disabled for Phase 1
    max_per_message: int = 5
    compression_target: float = 0.73  # 73% of API limit


@dataclass
class LoggingConfig:
    """Logging configuration"""
    level: str = "INFO"
    file: str = "logs/{bot_id}.log"
    max_size_mb: int = 50
    backup_count: int = 3


@dataclass
class DiscordConfig:
    """Discord-specific configuration"""
    token_env_var: str = "DISCORD_BOT_TOKEN"  # Environment variable containing bot token
    servers: List[str] = field(default_factory=list)  # Guild IDs

    # Historical message backfill
    backfill_enabled: bool = True  # Fetch historical messages on startup
    backfill_days: int = 30  # How many days of history to fetch (ignored if backfill_unlimited=True)
    backfill_unlimited: bool = False  # Fetch ALL message history (ignores backfill_days)
    backfill_in_background: bool = True  # Run backfill in background (don't block startup)


@dataclass
class BotConfig:
    """
    Complete bot configuration.

    Loaded from YAML files in bots/ directory.
    """
    bot_id: str
    name: str
    description: str = ""

    discord: DiscordConfig = field(default_factory=DiscordConfig)
    personality: Optional[PersonalityConfig] = None
    reactive: ReactiveConfig = field(default_factory=ReactiveConfig)
    agentic: AgenticConfig = field(default_factory=AgenticConfig)
    api: APIConfig = field(default_factory=APIConfig)
    rate_limiting: RateLimitingConfig = field(default_factory=RateLimitingConfig)
    images: ImagesConfig = field(default_factory=ImagesConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def load(cls, yaml_path: Path) -> 'BotConfig':
        """
        Load bot configuration from YAML file.

        Args:
            yaml_path: Path to bot config YAML file

        Returns:
            BotConfig instance

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config is invalid
        """
        if not yaml_path.exists():
            raise FileNotFoundError(f"Config file not found: {yaml_path}")

        with open(yaml_path) as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError(f"Empty config file: {yaml_path}")

        # Validate required fields
        if "bot_id" not in data:
            raise ValueError("Config missing required field: bot_id")
        if "name" not in data:
            raise ValueError("Config missing required field: name")

        # Parse nested configs
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> 'BotConfig':
        """Parse nested configuration dictionaries"""
        # Parse personality
        personality_data = data.get("personality", {})
        if personality_data and "base_prompt" in personality_data:
            personality = PersonalityConfig(
                base_prompt=personality_data["base_prompt"],
                formality=personality_data.get("formality", 0.2),
                emoji_usage=personality_data.get("emoji_usage", "never"),
                reaction_usage=personality_data.get("reaction_usage", "moderate"),
                formatting=personality_data.get("formatting", "minimal"),
                mention_response_rate=personality_data.get("mention_response_rate", 1.0),
                technical_help_rate=personality_data.get("technical_help_rate", 0.8),
                humor_response_rate=personality_data.get("humor_response_rate", 0.4),
                cold_conversation_rate=personality_data.get("cold_conversation_rate", 0.1),
                warm_conversation_rate=personality_data.get("warm_conversation_rate", 0.25),
                hot_conversation_rate=personality_data.get("hot_conversation_rate", 0.4),
            )
        else:
            personality = None

        # Parse discord config
        discord_data = data.get("discord", {})
        discord = DiscordConfig(
            token_env_var=discord_data.get("token_env_var", "DISCORD_BOT_TOKEN"),
            servers=discord_data.get("servers", [])
        )

        # Parse reactive config
        reactive_data = data.get("reactive", {})
        cooldowns_data = reactive_data.get("cooldowns", {})
        cooldowns = CooldownsConfig(
            per_user=cooldowns_data.get("per_user", 40),
            single_message=cooldowns_data.get("single_message", 45),
            multi_message=cooldowns_data.get("multi_message", 75),
            heavy_activity=cooldowns_data.get("heavy_activity", 105),
        )
        reactive = ReactiveConfig(
            enabled=reactive_data.get("enabled", True),
            check_interval_seconds=reactive_data.get("check_interval_seconds", 30),
            context_window=reactive_data.get("context_window", 20),
            cooldowns=cooldowns,
        )

        # Parse agentic config
        agentic_data = data.get("agentic", {})

        # Parse followups config
        followups_data = agentic_data.get("followups", {})
        followups = FollowupsConfig(
            enabled=followups_data.get("enabled", False),
            auto_create=followups_data.get("auto_create", True),
            max_pending=followups_data.get("max_pending", 20),
            priority_threshold=followups_data.get("priority_threshold", "medium"),
            follow_up_delay_days=followups_data.get("follow_up_delay_days", 1),
            max_age_days=followups_data.get("max_age_days", 14),
        )

        # Parse proactive config
        proactive_data = agentic_data.get("proactive", {})
        proactive = ProactiveConfig(
            enabled=proactive_data.get("enabled", False),
            min_idle_hours=proactive_data.get("min_idle_hours", 1.0),
            max_idle_hours=proactive_data.get("max_idle_hours", 8.0),
            min_provocation_gap_hours=proactive_data.get("min_provocation_gap_hours", 1.0),
            max_per_day_global=proactive_data.get("max_per_day_global", 10),
            max_per_day_per_channel=proactive_data.get("max_per_day_per_channel", 3),
            engagement_threshold=proactive_data.get("engagement_threshold", 0.3),
            learning_window_days=proactive_data.get("learning_window_days", 7),
            quiet_hours=proactive_data.get("quiet_hours", [0, 6]),
            allowed_channels=proactive_data.get("allowed_channels", []),
        )

        agentic = AgenticConfig(
            enabled=agentic_data.get("enabled", False),
            check_interval_hours=agentic_data.get("check_interval_hours", 1),
            followups=followups,
            proactive=proactive,
        )

        # Parse API config
        api_data = data.get("api", {})

        extended_thinking_data = api_data.get("extended_thinking", {})
        extended_thinking = ExtendedThinkingConfig(
            enabled=extended_thinking_data.get("enabled", True),
            budget_tokens=extended_thinking_data.get("budget_tokens", 10000),
        )

        context_editing_data = api_data.get("context_editing", {})
        context_editing = ContextEditingConfig(
            enabled=context_editing_data.get("enabled", True),
            trigger_tokens=context_editing_data.get("trigger_tokens", 100000),
            keep_tool_uses=context_editing_data.get("keep_tool_uses", 3),
            exclude_tools=context_editing_data.get("exclude_tools", ["memory"]),
        )

        throttling_data = api_data.get("throttling", {})
        throttling = ThrottlingConfig(
            min_delay_seconds=throttling_data.get("min_delay_seconds", 1.0),
            max_concurrent=throttling_data.get("max_concurrent", 10),
        )

        web_search_data = api_data.get("web_search", {})
        web_search = WebSearchConfig(
            enabled=web_search_data.get("enabled", False),
            max_daily=web_search_data.get("max_daily", 300),
            max_per_request=web_search_data.get("max_per_request", 3),
            citations_enabled=web_search_data.get("citations_enabled", True),
        )

        api = APIConfig(
            model=api_data.get("model", "claude-sonnet-4-5-20250929"),
            max_tokens=api_data.get("max_tokens", 4096),
            extended_thinking=extended_thinking,
            context_editing=context_editing,
            throttling=throttling,
            web_search=web_search,
        )

        # Parse rate limiting config
        rate_limiting_data = data.get("rate_limiting", {})
        short_data = rate_limiting_data.get("short", {})
        long_data = rate_limiting_data.get("long", {})

        rate_limiting = RateLimitingConfig(
            short=RateLimitWindowConfig(
                duration_minutes=short_data.get("duration_minutes", 5),
                max_responses=short_data.get("max_responses", 20),
            ),
            long=RateLimitWindowConfig(
                duration_minutes=long_data.get("duration_minutes", 60),
                max_responses=long_data.get("max_responses", 200),
            ),
            ignore_threshold=rate_limiting_data.get("ignore_threshold", 5),
            engagement_tracking_delay=rate_limiting_data.get("engagement_tracking_delay", 30),
        )

        # Parse logging config
        logging_data = data.get("logging", {})
        logging_config = LoggingConfig(
            level=logging_data.get("level", "INFO"),
            file=logging_data.get("file", "logs/{bot_id}.log"),
            max_size_mb=logging_data.get("max_size_mb", 50),
            backup_count=logging_data.get("backup_count", 3),
        )

        return cls(
            bot_id=data["bot_id"],
            name=data["name"],
            description=data.get("description", ""),
            discord=discord,
            personality=personality,
            reactive=reactive,
            agentic=agentic,
            api=api,
            rate_limiting=rate_limiting,
            logging=logging_config,
        )

    def validate(self) -> None:
        """
        Validate configuration values.

        Raises:
            ValueError: If configuration is invalid
        """
        # Validate bot_id
        if not self.bot_id or not self.bot_id.strip():
            raise ValueError("bot_id cannot be empty")

        # Validate name
        if not self.name or not self.name.strip():
            raise ValueError("name cannot be empty")

        # Validate personality rates
        if self.personality:
            rates = [
                ("mention_response_rate", self.personality.mention_response_rate),
                ("technical_help_rate", self.personality.technical_help_rate),
                ("humor_response_rate", self.personality.humor_response_rate),
                ("cold_conversation_rate", self.personality.cold_conversation_rate),
                ("warm_conversation_rate", self.personality.warm_conversation_rate),
                ("hot_conversation_rate", self.personality.hot_conversation_rate),
            ]
            for name, rate in rates:
                if not 0 <= rate <= 1:
                    raise ValueError(f"{name} must be between 0 and 1")

        # Validate rate limiting
        if self.rate_limiting.short.max_responses < 1:
            raise ValueError("short window max_responses must be >= 1")
        if self.rate_limiting.long.max_responses < 1:
            raise ValueError("long window max_responses must be >= 1")
        if self.rate_limiting.ignore_threshold < 1:
            raise ValueError("ignore_threshold must be >= 1")

        # Validate logging level
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
        if self.logging.level not in valid_levels:
            raise ValueError(f"logging.level must be one of: {valid_levels}")
