"""Typed configuration for the cloud API and independently deployed workers."""

import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExtensionsConfig(BaseModel):
    """Deployment boundaries for separately installed extension packages.

    ``catalog_directories`` is metadata-only source discovery. ``allowed_ids``
    is the deployment security boundary: workers may import only those entry
    points. Runtime activation is durable PostgreSQL state and is intentionally
    not represented by this immutable deployment file.
    """

    model_config = ConfigDict(extra="forbid")

    catalog_directories: list[str] = Field(default_factory=list)
    allowed_ids: list[str] = Field(default_factory=list)
    initially_active: list[str] = Field(default_factory=list)
    allow_console_activation: bool = False
    discover_entry_points: bool = True
    settings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Transitional input for deployments created before the catalog/activation
    # split. It is treated as both allowed and initially active, but new config
    # files must use the explicit fields above.
    enabled: list[str] = Field(default_factory=list)


class ProviderConfig(BaseModel):
    """LLM provider configuration."""

    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)
    request_timeout_seconds: float = Field(default=120.0, ge=1, le=3600)
    models: list[dict[str, Any]] = Field(default_factory=list)
    revision_id: str | None = None


class ProvidersConfig(BaseModel):
    """Configuration for LLM providers."""

    model_config = ConfigDict(extra="forbid")

    # Explicit single-provider route selected by LLM_PROVIDER.  This is
    # deployment state populated by the loader, not a credential.
    default_provider: str = ""
    # Provider extensions own the corresponding names, endpoint defaults and
    # environment aliases. Core accepts only this vendor-neutral map.
    settings: dict[str, ProviderConfig] = Field(default_factory=dict)

    def get_provider_config(self, name: str) -> ProviderConfig | None:
        normalized = str(name).strip().lower()
        return self.settings.get(normalized)

    def iter_provider_configs(self) -> dict[str, ProviderConfig]:
        return dict(self.settings)


class GatewayConfig(BaseModel):
    """Cloud API and worker runtime configuration."""

    model_config = ConfigDict(extra="forbid")

    allow_insecure_auth: bool = False
    # Browser origins allowed by CORS. Cloud deployments must set this to the
    # real frontend origin(s); "*" is never a safe value with bearer auth.
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    # Outbound shards per channel. The same chat is always mapped to one shard.
    channel_send_workers: int = Field(default=4, ge=1, le=32)
    channel_send_max_attempts: int = Field(default=10, ge=1, le=100)
    # Per-worker safety cap for concurrent agent sessions. Cluster/user quotas
    # are enforced separately; None remains available for trusted local use.
    max_concurrent_sessions: int | None = 8


class AuthProfileConfig(BaseModel):
    provider: str = ""
    enabled: bool = True
    api_key: str = ""
    token: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)


class AuthCooldownsConfig(BaseModel):
    billing_backoff_hours: float = 5.0
    billing_backoff_hours_by_provider: dict[str, float] = Field(default_factory=dict)
    billing_max_hours: float = 24.0
    failure_window_hours: float = 24.0


class AuthConfig(BaseModel):
    profiles: dict[str, AuthProfileConfig] = Field(default_factory=dict)
    order: dict[str, list[str]] = Field(default_factory=dict)
    cooldowns: AuthCooldownsConfig = Field(default_factory=AuthCooldownsConfig)


class ToolsConfig(BaseModel):
    """Core Tool governance; implementations configure themselves as extensions."""

    model_config = ConfigDict(extra="forbid")

    # Optional network/integration tools are disabled unless explicitly enabled.
    optional_allowlist: list[str] = Field(default_factory=list)


class SkillEntryConfig(BaseModel):
    """Per-skill enablement and environment configuration."""

    enabled: bool = True


class SkillsConfig(BaseModel):
    """Skills configuration: per-skill enable/disable."""

    entries: dict[str, SkillEntryConfig] = Field(default_factory=dict)


class MessagesConfig(BaseModel):
    """Channel message acknowledgement and response behaviour."""

    ack_reaction_scope: str | None = None  # group-mentions | group-all | direct | all
    ack_reaction: str | None = None
    remove_ack_after_reply: bool | None = None
    response_prefix: str | None = None  # template: {model}, {provider}, {identityName}, etc.
    suppress_tool_errors: bool | None = None  # hide tool error warnings from user
    # After tool execution: user message sent to LLM to get final reply. None = use built-in concise prompt.
    after_tool_results_prompt: str | None = None


class CommandsConfig(BaseModel):
    """Channel-native command handling."""

    # Enable native commands (/new, /help). "auto" = current behavior (Telegram registers, Loop handles).
    native: bool | Literal["auto"] = "auto"


class RuntimeStoreConfig(BaseModel):
    """Durable agent-runtime storage and distributed worker settings."""

    database_url: str = ""
    pool_min_size: int = 1
    pool_max_size: int = 10
    auto_migrate: bool = True
    blob_directory: str = ""
    blob_inline_threshold_bytes: int = Field(default=65536, ge=0, le=16 * 1024 * 1024)
    input_asset_directory: str = "~/.joyhousebot/input-assets"
    input_asset_max_bytes: int = Field(
        default=25 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024
    )
    lease_seconds: int = 60
    # PostgreSQL NOTIFY is the normal wake-up path.  This is only the durable
    # recovery cadence for listener reconnects and startup races.
    poll_interval_seconds: float = Field(default=0.2, ge=0.1, le=5.0)


class RuntimeConfig(BaseModel):
    """Native runtime process role and persistence configuration."""

    store: RuntimeStoreConfig = Field(default_factory=RuntimeStoreConfig)
    worker_name: str = ""
    scratch_root: str = "~/.joyhousebot/runtime-scratch"
    # Used only when seeding a genuinely empty Agent catalog. The resulting
    # revision freezes the exact value; workers never resolve a moving alias.
    bootstrap_model: str = ""


class Config(BaseModel):
    """Root configuration for joyhousebot."""

    extensions: ExtensionsConfig = Field(default_factory=ExtensionsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    messages: MessagesConfig | None = None
    commands: CommandsConfig | None = None

    def _match_provider(
        self, model: str | None = None
    ) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config and its registry name. Returns (config, spec_name)."""
        from joyhousebot.providers.registry import find_by_name, provider_specs

        model_lower = (model or "").lower()

        # LLM_PROVIDER is an explicit route, especially for gateways such as
        # OpenRouter that serve model families owned by several vendors.  It
        # must win over model-prefix inference and unrelated native keys.
        preferred_name = self.providers.default_provider.strip().lower()
        preferred_spec = find_by_name(self, preferred_name) if preferred_name else None
        if preferred_spec is not None:
            preferred = self._provider_config(preferred_spec)
            if preferred and (preferred.api_key or preferred_spec.is_local):
                return preferred, preferred_spec.name

        # Match by keyword (order follows PROVIDERS registry)
        for spec in provider_specs(self):
            p = self._provider_config(spec)
            if (
                p
                and any(kw in model_lower for kw in spec.keywords)
                and (p.api_key or spec.is_local)
            ):
                return p, spec.name

        # Fallback: gateways first, then others (follows registry order)
        for spec in provider_specs(self):
            p = self._provider_config(spec)
            if p and (p.api_key or spec.is_local):
                return p, spec.name
        return None, None

    def _provider_config(self, spec: Any) -> "ProviderConfig | None":
        """Resolve a provider-native key only after its extension is loaded."""
        provider = self.providers.get_provider_config(spec.name)
        native_key = (
            (os.environ.get(spec.env_key) or "").strip()
            if str(getattr(spec, "env_key", "") or "").strip()
            else ""
        )
        if provider is None and native_key:
            provider = ProviderConfig(api_key=native_key)
            self.providers.settings[spec.name] = provider
        elif provider is not None and native_key:
            provider.api_key = native_key
        return provider

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """Get matched provider config (api_key, api_base, extra_headers). Falls back to first available."""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """Get the registry name selected by the enabled provider extensions."""
        _, name = self._match_provider(model)
        return name

    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for the given model. Applies default URLs for known gateways."""
        from joyhousebot.providers.registry import find_by_name

        p, name = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        if name:
            spec = find_by_name(self, name)
            if spec and spec.default_api_base:
                return spec.default_api_base
        return None

    def get_bootstrap_model(self) -> str:
        """Return a provider-neutral, exact model id for an empty catalog."""
        configured = self.runtime.bootstrap_model.strip()
        return configured or "unconfigured/model"

    model_config = ConfigDict(env_prefix="JOYHOUSEBOT_", env_nested_delimiter="__")
