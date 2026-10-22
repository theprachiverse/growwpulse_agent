"""Configuration management for the Groww Review Pulse Agent.

Combines:
- `.env` for secrets and runtime overrides (via `pydantic-settings`)
- `config/config.yaml` for pipeline behaviour and constraints
- `config/mcp_servers.yaml` for MCP server registrations
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PlayStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    package_id: str = "com.nextbillion.groww"
    country: str = "in"
    lang: str = "en"
    max_reviews: int = Field(default=10000, gt=0)
    page_size: int = Field(default=200, gt=0, le=200)


class AppStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    app_id: str = "1404871703"
    country: str = "in"
    max_pages: int = Field(default=10, ge=1, le=10)
    max_reviews: int = Field(default=500, gt=0)
    accumulate_weekly: bool = True


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    play_store: PlayStoreConfig = Field(default_factory=PlayStoreConfig)
    app_store: AppStoreConfig = Field(default_factory=AppStoreConfig)


class IngestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_weeks: int = Field(
        default=10,
        description="Review window in weeks; must be between 8 and 12",
    )
    min_reviews: int = Field(
        default=50,
        ge=0,
        description="Below this many clean reviews the pulse gets a low-volume banner",
    )
    skip_on_low_volume: bool = Field(
        default=False,
        description="If true, abort the run instead of adding a low-volume banner",
    )
    min_words: int = Field(
        default=8,
        ge=0,
        description="Word count threshold; reviews with <= min_words are dropped",
    )
    english_only: bool = Field(
        default=True,
        description="Keep only English reviews; filter out other languages",
    )
    strip_emojis: bool = Field(
        default=True,
        description="Strip emojis from review text and title",
    )

    @field_validator("window_weeks")
    @classmethod
    def validate_window_weeks(cls, v: int) -> int:
        if not (8 <= v <= 12):
            raise ValueError(f"window_weeks must be between 8 and 12 (inclusive), got {v}")
        return v


class PrivacyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enable_presidio: bool = Field(
        default=False,
        description="Scrub person names with Presidio (needs presidio + spaCy model installed)",
    )


class RankingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_weighting: Literal["volume", "balanced"] = "volume"


class ThemingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_themes: int = Field(default=5, ge=1, le=5, description="Max themes; hard cap <= 5")
    top_k: int = Field(default=3, ge=1, le=5)
    batch_size: int = Field(
        default=50,
        ge=5,
        le=100,
        description="Batch size for LLM review classification (e.g. 10, 25, 50)",
    )
    residual_pass: bool = Field(
        default=True,
        description="If 'other' is large and a theme slot is free, look for one more theme in it",
    )
    residual_other_threshold: float = Field(default=0.15, ge=0.0, le=1.0)
    residual_min_capture: float = Field(
        default=0.03, ge=0.0, le=1.0, description="New theme must hold this share of all reviews"
    )
    seed_taxonomy: list[str] = Field(
        default_factory=lambda: [
            "onboarding",
            "kyc",
            "payments_upi",
            "withdrawals",
            "statements",
            "app_performance",
            "trading_fno",
            "customer_support",
            "charges",
        ]
    )

    @field_validator("max_themes")
    @classmethod
    def validate_max_themes(cls, v: int) -> int:
        if v > 5:
            raise ValueError(f"max_themes must be <= 5 per specification, got {v}")
        return v


class ComposeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_words: int = Field(default=250, ge=50, le=250, description="Word limit; hard cap <= 250")
    target_words: int = Field(
        default=220,
        ge=50,
        le=250,
        description="Shorten the pulse until it is at or below this; max_words stays the hard cap",
    )
    quote_words: list[int] = Field(
        default_factory=lambda: [8, 40],
        description="Allowed [min, max] words in verbatim quotes",
    )

    @field_validator("max_words")
    @classmethod
    def validate_max_words(cls, v: int) -> int:
        if v > 250:
            raise ValueError(f"max_words must be <= 250 per specification, got {v}")
        return v

    @model_validator(mode="after")
    def validate_target_words(self) -> "ComposeConfig":
        if self.target_words > self.max_words:
            if "target_words" not in self.model_fields_set:
                self.target_words = self.max_words  # lowered cap, default target follows it
                return self
            raise ValueError(
                f"target_words ({self.target_words}) must be <= max_words ({self.max_words})"
            )
        return self

    @field_validator("quote_words")
    @classmethod
    def validate_quote_words(cls, v: list[int]) -> list[int]:
        if len(v) != 2 or v[0] >= v[1] or v[0] <= 0:
            raise ValueError(f"quote_words must be [min, max] with min < max, got {v}")
        return v


class LLMTemperatures(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classify: float = Field(default=0.0, ge=0.0, le=1.0)
    compose: float = Field(default=0.4, ge=0.0, le=1.0)


class BudgetConfig(BaseModel):
    """Daily Groq limits remembered across runs (ledger: <data_dir>/llm_budget.json)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_runs_per_day: int = Field(default=4, ge=1)
    daily_token_limit: int = Field(default=200_000, gt=0)  # Groq free-tier TPD for gpt-oss-120b
    safety_margin: float = Field(default=0.9, gt=0.0, le=1.0)  # plan to use at most this share
    default_run_tokens: int = Field(default=150_000, gt=0)  # estimate until real runs are recorded
    tokens_per_minute: int = Field(
        default=8_000, ge=0, description="Groq free-tier TPM; calls are paced under it (0 = off)"
    )
    # Cost model behind the UI's pre-run estimate (an uncached run; the cache only lowers it).
    est_tokens_per_review: int = Field(default=56, gt=0)
    est_tokens_per_batch: int = Field(default=1_300, gt=0)  # prompt overhead + reasoning per call
    est_fixed_tokens: int = Field(default=30_000, ge=0)  # theme discovery, residual, compose


class ClassifyLLMConfig(BaseModel):
    """Optional second Groq model for per-review classification (its own daily quota/ledger)."""

    model_config = ConfigDict(extra="forbid")

    model: str | None = None  # None = classify with `model_chat`
    strategy: Literal["hybrid", "ml_only"] = Field(
        default="hybrid",
        description="Classification strategy: 'hybrid' (sample to LLM + ML model) or 'ml_only' (100% local centroids)",
    )
    daily_token_limit: int = Field(default=500_000, gt=0)
    tokens_per_minute: int = Field(default=6_000, ge=0)
    safety_margin: float = Field(default=0.9, gt=0.0, le=1.0)
    max_reviews_per_run: int = Field(
        default=200, ge=0, description="Cap on fresh reviews sent to the LLM per run (0 = no cap)"
    )
    max_residual_reviews: int = Field(
        default=100, ge=0, description="Same cap for the residual pass over 'other' (0 = no cap)"
    )


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_chat: str = "openai/gpt-oss-120b"
    model_embed: str = "tfidf-svd"
    base_url: str = "https://api.groq.com/openai/v1"
    timeout_s: int = Field(default=60, gt=0)
    max_retries: int = Field(default=4, ge=0)
    # gpt-oss reasons before answering and bills those tokens; "low" keeps classification cheap.
    reasoning_effort: Literal["low", "medium", "high"] | None = "low"
    max_completion_tokens: int | None = Field(default=None, gt=0)  # per-call reply cap
    temperature: LLMTemperatures = Field(default_factory=LLMTemperatures)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    classify: ClassifyLLMConfig = Field(default_factory=ClassifyLLMConfig)


class PublishConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_mode: Literal["draft", "send", "off"] = "draft"
    email_format: Literal["html", "text"] = "html"  # styled HTML email, or plain text fallback
    auto_create_doc: bool = True
    doc_title: str = "Groww Weekly Review Pulse"


class AppConfig(BaseModel):
    """Parsed YAML behaviour config."""

    model_config = ConfigDict(extra="forbid")

    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    theming: ThemingConfig = Field(default_factory=ThemingConfig)
    compose: ComposeConfig = Field(default_factory=ComposeConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    publish: PublishConfig = Field(default_factory=PublishConfig)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "AppConfig":
        file_path = Path(path)
        if not file_path.exists():
            # Return defaults if file not present
            return cls()
        with file_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)


class MCPServerEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    transport: Literal["stdio", "sse", "streamable_http"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    auth_token_env: str | None = None
    tool_map: dict[str, str] = Field(default_factory=dict)

    def is_placeholder(self) -> bool:
        """Returns True if command or any tool_map value contains angle-bracket placeholder."""
        if "<" in self.command and ">" in self.command:
            return True
        return any("<" in v and ">" in v for v in self.tool_map.values())


class MCPRegistryConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    servers: dict[str, MCPServerEntry] = Field(default_factory=dict)
    tool_allowlist: list[str] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "MCPRegistryConfig":
        file_path = Path(path)
        if not file_path.exists():
            return cls()
        with file_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)


class Settings(BaseSettings):
    """Runtime secrets and paths loaded from .env and environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: str = Field(default="groq", alias="LLM_PROVIDER")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    # Overrides llm.model_chat, e.g. to move to another free model whose quota is still unspent.
    llm_model: str | None = Field(default=None, alias="LLM_MODEL")
    pulse_recipient: str = Field(default="me@example.com", alias="PULSE_RECIPIENT")
    mcp_auth_token: str | None = Field(default=None, alias="MCP_AUTH_TOKEN")
    mcp_server_url: str | None = Field(default=None, alias="MCP_SERVER_URL")
    pulse_doc_id: str | None = Field(default=None, alias="PULSE_DOC_ID")

    config_path: Path = Field(default=Path("config/config.yaml"), alias="PULSE_CONFIG_PATH")
    mcp_config_path: Path = Field(
        default=Path("config/mcp_servers.yaml"),
        alias="PULSE_MCP_CONFIG_PATH",
    )
    data_dir: Path = Field(default=Path("data"), alias="PULSE_DATA_DIR")
    log_level: str = Field(default="INFO", alias="PULSE_LOG_LEVEL")

    _app_config: AppConfig | None = None
    _mcp_config: MCPRegistryConfig | None = None

    @property
    def app_config(self) -> AppConfig:
        if self._app_config is None:
            self._app_config = AppConfig.from_yaml(self.config_path)
        return self._app_config

    @property
    def mcp_config(self) -> MCPRegistryConfig:
        if self._mcp_config is None:
            self._mcp_config = MCPRegistryConfig.from_yaml(self.mcp_config_path)
        return self._mcp_config

    def masked_dict(self) -> dict[str, Any]:
        """Dictionary of settings safe for CLI printing/logging (secrets masked)."""
        masked_key = (
            f"{self.llm_api_key[:4]}...{self.llm_api_key[-4:]}"
            if self.llm_api_key and len(self.llm_api_key) > 8
            else ("<unset>" if not self.llm_api_key else "***")
        )
        return {
            "llm_provider": self.llm_provider,
            "llm_api_key": masked_key,
            "pulse_recipient": self.pulse_recipient,
            "config_path": str(self.config_path),
            "mcp_config_path": str(self.mcp_config_path),
            "data_dir": str(self.data_dir),
            "log_level": self.log_level,
            "app_config": self.app_config.model_dump(),
        }


def get_settings() -> Settings:
    """Helper to instantiate settings with cached singleton."""
    return Settings()
