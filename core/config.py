"""
Configuration system for Discord Agents.

Loads and validates bot configurations from YAML files.

v0.6.0: Simplified configuration - ~80% fewer settings.
See internal_constants.py for hardcoded implementation details.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
import yaml
import os
import logging

from core.internal_constants import (
    RATE_LIMIT_PRESETS,
    PROACTIVE_INTENSITY_PRESETS,
    ENGAGEMENT_TRACKING_DELAY_SECONDS,
    IGNORE_THRESHOLD,
    SKILLS_CACHE_FILE,
    MCP_CONFIG_FILE,
    WEB_SEARCH_CITATIONS_ENABLED,
    PROACTIVE_LEARNING_WINDOW_DAYS,
    PROACTIVE_ENGAGEMENT_THRESHOLD,
    PROACTIVE_MIN_PROVOCATION_GAP_HOURS,
    model_supports_effort,
)


# =============================================================================
# CONFIG DATACLASSES
# =============================================================================

@dataclass
class PersonalityConfig:
    """Bot personality configuration - behavioral control via prompting"""
    base_prompt: str
    # Reactions are programmatic (not part of message generation)
    reaction_usage: str = "moderate"  # never | rare | moderate | frequent


@dataclass
class ReactiveConfig:
    """Reactive engine configuration"""
    enabled: bool = True
    always_respond_to_mentions: bool = True  # Guaranteed response to @mentions
    rate_limit: str = "moderate"  # strict | moderate | permissive | unlimited
    check_interval_seconds: int = 60  # Periodic check interval

    def get_rate_limit_values(self):
        """Get actual rate limit values from preset"""
        preset_name = self.rate_limit.lower()
        if preset_name not in RATE_LIMIT_PRESETS:
            logging.warning(f"Unknown rate_limit preset '{preset_name}', using 'moderate'")
            preset_name = "moderate"
        return RATE_LIMIT_PRESETS[preset_name]


@dataclass
class FollowupsConfig:
    """Follow-up system configuration"""
    enabled: bool = False


@dataclass
class ProactiveConfig:
    """Proactive engagement configuration"""
    enabled: bool = False
    intensity: str = "moderate"  # gentle | moderate | active
    # List of LOCAL hours (0-23) during which proactive messages are suppressed
    quiet_hours: List[int] = field(default_factory=lambda: list(range(0, 7)))
    allowed_channels: List[str] = field(default_factory=list)

    def get_intensity_values(self):
        """Get actual intensity values from preset"""
        preset_name = self.intensity.lower()
        if preset_name not in PROACTIVE_INTENSITY_PRESETS:
            logging.warning(f"Unknown proactive intensity '{preset_name}', using 'moderate'")
            preset_name = "moderate"
        return PROACTIVE_INTENSITY_PRESETS[preset_name]


@dataclass
class AgenticConfig:
    """Agentic engine configuration"""
    enabled: bool = False
    check_interval_hours: float = 1.0  # Background loop frequency
    followups: FollowupsConfig = field(default_factory=FollowupsConfig)
    proactive: ProactiveConfig = field(default_factory=ProactiveConfig)


@dataclass
class ThinkingConfig:
    """Adaptive thinking configuration (model decides when/how much to think)"""
    enabled: bool = True


@dataclass
class WebSearchConfig:
    """Web search configuration - all or nothing (no rate limits)"""
    enabled: bool = False


@dataclass
class CodeExecutionConfig:
    """Code execution tool configuration - always enabled when skills are used"""
    # Note: enabled field removed in v0.5.1 (Bug #14 fix)
    # Code execution is now automatically enabled when skills are loaded
    pass


@dataclass
class APIConfig:
    """Claude API configuration"""
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 4096
    context_messages: int = 30  # Messages to remember (rolling window)
    context_tokens: int = 80000  # Session token threshold: episodize + reseed past this (from response.usage)
    effort: Optional[str] = None  # low | medium | high | max (None = API default, high)
    consolidation_model: str = "claude-sonnet-4-6"  # weekly memory reconsolidation (Batches API)
    thinking: ThinkingConfig = field(default_factory=ThinkingConfig)
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    code_execution: CodeExecutionConfig = field(default_factory=CodeExecutionConfig)


@dataclass
class MCPConfig:
    """MCP (Model Context Protocol) server configuration"""
    enabled: bool = False


@dataclass
class SkillsConfig:
    """Skills auto-discovery configuration - always enabled"""
    # Note: enabled field removed in v0.5.1 (Bug #14 fix)
    # Skills are now always enabled; code_execution is automatically enabled with them
    include_anthropic_skills: bool = True  # Include built-in xlsx, pptx, docx, pdf
    # Default skills to load initially (v0.5.0 Progressive Disclosure)
    # Claude can request different skills via request_skill tool
    default_skills: List[str] = field(default_factory=lambda: ["pdf"])


@dataclass
class LocalStorageConfig:
    """Local storage configuration for attachments"""
    base_path: str = "persistence/attachments"


@dataclass
class RepositoryConfig:
    """Bot file repository (v0.6.1) - per-server local drive"""
    enabled: bool = True


@dataclass
class AttachmentsConfig:
    """Unified attachment system configuration"""
    enabled: bool = False
    backfill_enabled: bool = True
    backfill_days: int = 30
    local_storage: LocalStorageConfig = field(default_factory=LocalStorageConfig)
    repository: RepositoryConfig = field(default_factory=RepositoryConfig)


@dataclass
class LoggingConfig:
    """Logging configuration"""
    level: str = "INFO"
    file: str = "logs/{bot_id}.log"


@dataclass
class DiscordConfig:
    """Discord-specific configuration"""
    token_env_var: str = "DISCORD_BOT_TOKEN"  # Environment variable containing bot token
    servers: List[str] = field(default_factory=list)  # Guild IDs
    timezone: str = "UTC"  # Default server timezone (IANA format)
    status: str = "Powered by Claude"  # Bot activity status
    allow_bot_interactions: bool = False  # Allow responding to other bots
    backfill_enabled: bool = True
    backfill_days: int = 30  # 0 = unlimited


@dataclass
class BotConfig:
    """
    Complete bot configuration.

    v0.6.0: Simplified to ~30 user-facing settings.
    Implementation details are in internal_constants.py.
    """
    bot_id: str
    name: str
    description: str = ""

    discord: DiscordConfig = field(default_factory=DiscordConfig)
    personality: Optional[PersonalityConfig] = None
    reactive: ReactiveConfig = field(default_factory=ReactiveConfig)
    agentic: AgenticConfig = field(default_factory=AgenticConfig)
    api: APIConfig = field(default_factory=APIConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    # v0.5.0+ features
    mcp: MCPConfig = field(default_factory=MCPConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    vaults: List[str] = field(default_factory=list)  # channel/server IDs whose content never leaves them
    attachments: AttachmentsConfig = field(default_factory=AttachmentsConfig)

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

        with open(yaml_path, encoding="utf-8-sig") as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError(f"Empty config file: {yaml_path}")

        # Validate required fields
        if "bot_id" not in data:
            raise ValueError("Config missing required field: bot_id")
        if "name" not in data:
            raise ValueError("Config missing required field: name")

        # Check for deprecated settings and warn
        cls._warn_deprecated(data)

        # Parse nested configs
        return cls._from_dict(data)

    @classmethod
    def _warn_deprecated(cls, data: dict):
        """Warn about deprecated configuration settings"""
        deprecated_mappings = {
            "personality.formality": "Move formality instructions to personality.base_prompt",
            "personality.emoji_usage": "Move emoji instructions to personality.base_prompt",
            "personality.formatting": "Move formatting instructions to personality.base_prompt",
            "personality.mention_response_rate": "Removed - bot uses agentic judgment via mandatory prompt",
            "personality.technical_help_rate": "Removed - bot uses agentic judgment via mandatory prompt",
            "personality.humor_response_rate": "Removed - bot uses agentic judgment via mandatory prompt",
            "personality.cold_conversation_rate": "Removed - bot uses agentic judgment via mandatory prompt",
            "personality.warm_conversation_rate": "Removed - bot uses agentic judgment via mandatory prompt",
            "personality.hot_conversation_rate": "Removed - bot uses agentic judgment via mandatory prompt",
            "reactive.context_window": "Removed - use api.context_messages instead",
            "reactive.cooldowns": "Replaced with reactive.cooldown preset (fast/moderate/relaxed)",
            "rate_limiting": "Replaced with reactive.rate_limit preset (strict/moderate/permissive/unlimited)",
            "api.throttling": "Internalized - no longer configurable",
            "api.context_editing.keep_tool_uses": "Internalized - no longer configurable",
            "api.context_editing.exclude_tools": "Internalized - no longer configurable",
            "api.context_management.document_warning_threshold": "Internalized - no longer configurable",
            "api.context_management.max_total_tokens": "Renamed to api.context_tokens",
            "api.context_management.max_conversation_messages": "Renamed to api.context_messages",
            "agentic.proactive.min_idle_hours": "Replaced with proactive.intensity preset",
            "agentic.proactive.max_idle_hours": "Replaced with proactive.intensity preset",
            "agentic.proactive.min_provocation_gap_hours": "Internalized",
            "agentic.proactive.max_per_day_global": "Replaced with proactive.intensity preset",
            "agentic.proactive.max_per_day_per_channel": "Replaced with proactive.intensity preset",
            "agentic.proactive.engagement_threshold": "Internalized",
            "agentic.proactive.learning_window_days": "Internalized",
            "logging.max_size_mb": "Internalized - no longer configurable",
            "logging.backup_count": "Internalized - no longer configurable",
            "skills.cache_file": "Internalized - no longer configurable",
            "mcp.config_file": "Internalized - always uses mcp_servers.json",
            "images.compression_target": "Internalized - no longer configurable",
            "multimedia": "Removed - use attachments instead",
            "discord.backfill_unlimited": "Removed - use backfill_days: 0 for unlimited",
            "discord.backfill_in_background": "Internalized - always runs in background",
            "discord.default_timezone": "Renamed to discord.timezone",
            "skills.enabled": "Removed - skills are always enabled (Bug #14 fix)",
            "api.code_execution.enabled": "Removed - code_execution is automatically enabled with skills (Bug #14 fix)",
            "data_isolation": "Replaced by vaults: [channel/server IDs] - content of a vaulted place never leaves it; the old scope modes are gone",
        }

        def check_nested(d: dict, prefix: str = ""):
            for key, value in d.items():
                full_key = f"{prefix}.{key}" if prefix else key
                if full_key in deprecated_mappings:
                    logging.warning(f"Deprecated config '{full_key}': {deprecated_mappings[full_key]}")
                if isinstance(value, dict):
                    check_nested(value, full_key)

        check_nested(data)

    @classmethod
    def _from_dict(cls, data: dict) -> 'BotConfig':
        """Parse nested configuration dictionaries"""

        # Parse personality
        personality_data = data.get("personality", {})
        if personality_data and "base_prompt" in personality_data:
            personality = PersonalityConfig(
                base_prompt=personality_data["base_prompt"],
                reaction_usage=personality_data.get("reaction_usage", "moderate"),
            )
        else:
            personality = None

        # Parse discord config
        discord_data = data.get("discord", {})
        discord = DiscordConfig(
            token_env_var=discord_data.get("token_env_var", "DISCORD_BOT_TOKEN"),
            servers=discord_data.get("servers", []),
            timezone=discord_data.get("timezone", discord_data.get("default_timezone", "UTC")),
            status=discord_data.get("status", "Powered by Claude"),
            allow_bot_interactions=discord_data.get("allow_bot_interactions", False),
            backfill_enabled=discord_data.get("backfill_enabled", True),
            backfill_days=discord_data.get("backfill_days", 30),
        )

        # Parse reactive config
        reactive_data = data.get("reactive", {})
        reactive = ReactiveConfig(
            enabled=reactive_data.get("enabled", True),
            always_respond_to_mentions=reactive_data.get("always_respond_to_mentions", True),
            rate_limit=reactive_data.get("rate_limit", "moderate"),
            check_interval_seconds=reactive_data.get("check_interval_seconds", 60),
        )

        # Parse agentic config
        agentic_data = data.get("agentic", {})

        followups_data = agentic_data.get("followups", {})
        followups = FollowupsConfig(
            enabled=followups_data.get("enabled", False),
        )

        proactive_data = agentic_data.get("proactive", {})
        proactive = ProactiveConfig(
            enabled=proactive_data.get("enabled", False),
            intensity=proactive_data.get("intensity", "moderate"),
            quiet_hours=proactive_data.get("quiet_hours", list(range(0, 7))),
            allowed_channels=proactive_data.get("allowed_channels", []),
        )

        agentic = AgenticConfig(
            enabled=agentic_data.get("enabled", False),
            check_interval_hours=agentic_data.get("check_interval_hours", 1.0),
            followups=followups,
            proactive=proactive,
        )

        # Parse API config
        api_data = data.get("api", {})

        # "extended_thinking" fallback accepts pre-v0.6.0 configs (budget_tokens ignored)
        thinking_data = api_data.get("thinking", api_data.get("extended_thinking", {}))
        thinking = ThinkingConfig(
            enabled=thinking_data.get("enabled", True),
        )

        web_search_data = api_data.get("web_search", {})
        web_search = WebSearchConfig(
            enabled=web_search_data.get("enabled", False),
        )

        # CodeExecutionConfig no longer has enabled field (Bug #14 fix)
        # It's automatically enabled when skills are loaded
        code_execution = CodeExecutionConfig()

        # Handle legacy context_management structure
        context_management_data = api_data.get("context_management", {})
        context_messages = api_data.get(
            "context_messages",
            context_management_data.get("max_conversation_messages", 30)
        )
        context_tokens = api_data.get(
            "context_tokens",
            context_management_data.get("max_total_tokens", 100000)
        )

        api = APIConfig(
            model=api_data.get("model", "claude-sonnet-4-6"),
            max_tokens=api_data.get("max_tokens", 4096),
            context_messages=context_messages,
            context_tokens=context_tokens,
            effort=api_data.get("effort"),
            consolidation_model=api_data.get("consolidation_model", "claude-sonnet-4-6"),
            thinking=thinking,
            web_search=web_search,
            code_execution=code_execution,
        )

        # Parse logging config
        logging_data = data.get("logging", {})
        logging_config = LoggingConfig(
            level=logging_data.get("level", "INFO"),
            file=logging_data.get("file", "logs/{bot_id}.log"),
        )

        # Parse v0.5.0+ features
        mcp_data = data.get("mcp", {})
        mcp = MCPConfig(
            enabled=mcp_data.get("enabled", False),
        )

        # SkillsConfig no longer has enabled field (Bug #14 fix)
        # Skills are always enabled
        skills_data = data.get("skills", {})
        skills = SkillsConfig(
            include_anthropic_skills=skills_data.get("include_anthropic_skills", True),
            default_skills=skills_data.get("default_skills", ["pdf"]),
        )

        # Parse attachments config
        attachments_data = data.get("attachments", {})
        attachments = AttachmentsConfig(
            enabled=attachments_data.get("enabled", False),
            backfill_enabled=attachments_data.get("backfill_enabled", True),
            backfill_days=attachments_data.get("backfill_days", 30),
            repository=RepositoryConfig(
                enabled=attachments_data.get("repository", {}).get("enabled", True)
            ),
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
            logging=logging_config,
            mcp=mcp,
            skills=skills,
            vaults=[str(v) for v in (data.get("vaults") or [])],
            attachments=attachments,
        )

    def validate(self) -> List[str]:
        """
        Basic configuration validation for production readiness.

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Required fields
        if not self.bot_id or not self.bot_id.strip():
            errors.append("bot_id is required and cannot be empty")

        if not self.name or not self.name.strip():
            errors.append("name is required and cannot be empty")

        # Environment variable checks
        if not self.discord.token_env_var:
            errors.append("discord.token_env_var is required")
        elif not os.getenv(self.discord.token_env_var):
            errors.append(f"Missing environment variable: {self.discord.token_env_var}")

        if not os.getenv("ANTHROPIC_API_KEY"):
            errors.append("Missing ANTHROPIC_API_KEY in environment")

        # Type validation
        if not isinstance(self.discord.servers, list):
            errors.append("discord.servers must be a list")

        # Warn for empty servers (not error, just warning)
        if not self.discord.servers:
            logging.warning(f"[{self.bot_id}] No servers configured - bot won't join any servers")

        # Validate API config
        if self.api.max_tokens <= 0:
            errors.append("api.max_tokens must be positive")

        if self.api.effort is not None and self.api.effort not in ("low", "medium", "high", "max"):
            errors.append(
                f"api.effort must be one of low/medium/high/max, got '{self.api.effort}'"
            )

        if self.api.effort is not None and not model_supports_effort(self.api.model):
            errors.append(
                f"api.effort is set but model '{self.api.model}' does not support the "
                f"effort parameter (every API call would 400) - remove api.effort or "
                f"switch to an effort-capable model"
            )

        if self.api.context_tokens <= 0:
            errors.append("api.context_tokens must be positive")

        if not (5 <= self.api.context_messages <= 100):
            errors.append(
                f"api.context_messages must be between 5 and 100, got {self.api.context_messages}"
            )

        # Validate presets
        if self.reactive.rate_limit.lower() not in RATE_LIMIT_PRESETS:
            errors.append(
                f"reactive.rate_limit must be one of {list(RATE_LIMIT_PRESETS.keys())}, "
                f"got '{self.reactive.rate_limit}'"
            )

        if self.agentic.proactive.intensity.lower() not in PROACTIVE_INTENSITY_PRESETS:
            errors.append(
                f"agentic.proactive.intensity must be one of {list(PROACTIVE_INTENSITY_PRESETS.keys())}, "
                f"got '{self.agentic.proactive.intensity}'"
            )

        if not isinstance(self.vaults, list) or any(not str(v).strip() for v in self.vaults):
            errors.append("vaults must be a list of channel/server IDs")

        if not self.api.consolidation_model or not self.api.consolidation_model.strip():
            errors.append("api.consolidation_model cannot be empty")

        # Validate logging level
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
        if self.logging.level not in valid_levels:
            errors.append(f"logging.level must be one of {valid_levels}, got '{self.logging.level}'")

        # Validate reaction_usage if personality is set
        if self.personality:
            valid_reactions = ["never", "rare", "moderate", "frequent"]
            if self.personality.reaction_usage.lower() not in valid_reactions:
                errors.append(
                    f"personality.reaction_usage must be one of {valid_reactions}, "
                    f"got '{self.personality.reaction_usage}'"
                )

        return errors

    # =========================================================================
    # HELPER METHODS FOR ACCESSING INTERNAL CONSTANTS
    # =========================================================================

    def get_rate_limiting_config(self):
        """Get rate limiting configuration from preset"""
        preset = self.reactive.get_rate_limit_values()
        return {
            "short": {
                "duration_minutes": preset.short_duration_minutes,
                "max_responses": preset.short_max_responses,
            },
            "long": {
                "duration_minutes": preset.long_duration_minutes,
                "max_responses": preset.long_max_responses,
            },
            "ignore_threshold": IGNORE_THRESHOLD,
            "engagement_tracking_delay": ENGAGEMENT_TRACKING_DELAY_SECONDS,
        }

    def get_skills_config(self):
        """Get full skills configuration with internal values"""
        return {
            "enabled": True,  # Always enabled (Bug #14 fix)
            "skills_dir": "skills",  # Standardized
            "cache_file": SKILLS_CACHE_FILE,
            "include_anthropic_skills": self.skills.include_anthropic_skills,
        }

    def get_mcp_config(self):
        """Get full MCP configuration with internal values"""
        return {
            "enabled": self.mcp.enabled,
            "config_file": MCP_CONFIG_FILE,
        }

    def get_web_search_config(self):
        """Get web search configuration - all or nothing, no rate limits"""
        return {
            "enabled": self.api.web_search.enabled,
            "citations_enabled": WEB_SEARCH_CITATIONS_ENABLED,
        }

    def get_proactive_config(self):
        """Get full proactive configuration with internal and preset values"""
        intensity = self.agentic.proactive.get_intensity_values()
        return {
            "enabled": self.agentic.proactive.enabled,
            "min_idle_hours": intensity.min_idle_hours,
            "max_idle_hours": intensity.max_idle_hours,
            "min_provocation_gap_hours": PROACTIVE_MIN_PROVOCATION_GAP_HOURS,
            "max_per_day_global": intensity.max_per_day_global,
            "max_per_day_per_channel": intensity.max_per_day_per_channel,
            "engagement_threshold": PROACTIVE_ENGAGEMENT_THRESHOLD,
            "learning_window_days": PROACTIVE_LEARNING_WINDOW_DAYS,
            "quiet_hours": self.agentic.proactive.quiet_hours,
            "allowed_channels": self.agentic.proactive.allowed_channels,
        }

