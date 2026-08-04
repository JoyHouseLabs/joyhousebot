"""Typed configuration for the cloud API and independently deployed workers."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings


class WhatsAppConfig(BaseModel):
    """WhatsApp channel configuration."""

    enabled: bool = False
    bridge_url: str = "ws://localhost:3001"
    bridge_token: str = ""  # Shared token for bridge auth (optional, recommended)
    allow_from: list[str] = Field(default_factory=list)  # Allowed phone numbers


class TelegramConfig(BaseModel):
    """Telegram channel configuration."""

    enabled: bool = False
    token: str = ""  # Bot token from @BotFather
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs or usernames
    proxy: str | None = (
        None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    )
    # Override global native-command handling for this channel.
    commands_native: bool | Literal["auto"] | None = None


class FeishuConfig(BaseModel):
    """Feishu/Lark channel configuration using WebSocket long connection."""

    enabled: bool = False
    app_id: str = ""  # App ID from Feishu Open Platform
    app_secret: str = ""  # App Secret from Feishu Open Platform
    encrypt_key: str = ""  # Encrypt Key for event subscription (optional)
    verification_token: str = ""  # Verification Token for event subscription (optional)
    allow_from: list[str] = Field(default_factory=list)  # Allowed user open_ids


class DingTalkConfig(BaseModel):
    """DingTalk channel configuration using Stream mode."""

    enabled: bool = False
    client_id: str = ""  # AppKey
    client_secret: str = ""  # AppSecret
    allow_from: list[str] = Field(default_factory=list)  # Allowed staff_ids


class DiscordConfig(BaseModel):
    """Discord channel configuration."""

    enabled: bool = False
    token: str = ""  # Bot token from Discord Developer Portal
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377  # GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT


class EmailConfig(BaseModel):
    """Email channel configuration (IMAP inbound + SMTP outbound)."""

    enabled: bool = False
    consent_granted: bool = False  # Explicit owner permission to access mailbox data

    # IMAP (receive)
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_use_ssl: bool = True

    # SMTP (send)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    from_address: str = ""

    # Behavior
    auto_reply_enabled: bool = (
        True  # If false, inbound email is read but no automatic reply is sent
    )
    poll_interval_seconds: int = 30
    mark_seen: bool = True
    max_body_chars: int = 12000
    subject_prefix: str = "Re: "
    allow_from: list[str] = Field(default_factory=list)  # Allowed sender email addresses


class SlackDMConfig(BaseModel):
    """Slack DM policy configuration."""

    enabled: bool = True
    policy: str = "open"  # "open" or "allowlist"
    allow_from: list[str] = Field(default_factory=list)  # Allowed Slack user IDs


class SlackConfig(BaseModel):
    """Slack channel configuration."""

    enabled: bool = False
    mode: str = "socket"  # "socket" supported
    webhook_path: str = "/slack/events"
    bot_token: str = ""  # xoxb-...
    app_token: str = ""  # xapp-...
    user_token_read_only: bool = True
    group_policy: str = "mention"  # "mention", "open", "allowlist"
    group_allow_from: list[str] = Field(default_factory=list)  # Allowed channel IDs if allowlist
    dm: SlackDMConfig = Field(default_factory=SlackDMConfig)


class QQConfig(BaseModel):
    """QQ channel configuration using botpy SDK."""

    enabled: bool = False
    app_id: str = ""  # 机器人 ID (AppID) from q.qq.com
    secret: str = ""  # 机器人密钥 (AppSecret) from q.qq.com
    allow_from: list[str] = Field(
        default_factory=list
    )  # Allowed user openids (empty = public access)


class ChannelsConfig(BaseModel):
    """Configuration for chat channels."""

    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    qq: QQConfig = Field(default_factory=QQConfig)


class ProviderConfig(BaseModel):
    """LLM provider configuration."""

    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)


class ProvidersConfig(BaseModel):
    """Configuration for LLM providers."""

    # Explicit single-provider route selected by LLM_PROVIDER.  This is
    # deployment state populated by the loader, not a credential.
    default_provider: str = ""
    custom: ProviderConfig = Field(default_factory=ProviderConfig)  # Any OpenAI-compatible endpoint
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)  # 阿里云通义千问
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)  # AiHubMix API gateway


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
    mode: str = "api_key"  # api_key | oauth | token
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


class WebSearchConfig(BaseModel):
    """Web search tool configuration."""

    api_key: str = ""  # Brave Search API key
    max_results: int = 5


class WebToolsConfig(BaseModel):
    """Web tools configuration."""

    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(BaseModel):
    """Shell exec tool configuration."""

    timeout: int = 60
    # Run through sh -c when shell syntax such as pipes is required.
    shell_mode: bool = False
    # Cloud-safe default: shell execution requires an isolated container and
    # fails closed when that sandbox is unavailable.
    container_image: str = "alpine:3.18"
    # Host path for workspace mount; empty means use working_dir. Container path is /workspace.
    container_workspace_mount: str = ""
    container_user: str = "65534:65534"  # nobody; set "" to run as the image default user
    container_network: str = "none"  # "none" | bridge name; "host" is rejected (falls back to "none")
    # Container resource limits (fail-closed sandbox defaults).
    container_memory: str = "512m"
    container_cpus: str = "1"
    container_pids_limit: int = 256


class MCPServerConfig(BaseModel):
    """MCP server connection configuration (stdio or HTTP)."""

    enabled: bool = True
    command: str = ""  # Stdio: command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: extra env vars
    url: str = ""  # HTTP: streamable HTTP endpoint URL


class RetrievalConfig(BaseModel):
    """Platform defaults for durable knowledge and memory behavior.

    Agent Revision ``memory_policy`` is the authoritative per-Agent override.
    These settings provide only the shared scope and consolidation defaults;
    they do not automatically enable personal memory for every Agent.
    """

    memory_first: bool = (
        False  # When True, agent is prompted to consult L0/memory before knowledge base
    )
    memory_top_k: int = 10
    memory_include_daily_in_context: bool = True  # Inject today and yesterday's daily memory.
    history_max_entries: int = 0  # When > 0, keep only last N entries in HISTORY.md (0 = no limit)
    # Optional model call to capture durable notes before consolidation.
    memory_flush_before_consolidation: bool = False
    memory_flush_system_prompt: str = "Session nearing compaction. Output only valid JSON."
    memory_flush_prompt: str = "Write any lasting notes: return JSON with optional keys daily_log_entry (string for memory/YYYY-MM-DD.md) and memory_additions (string to append to MEMORY.md). If nothing to store, return {}."
    # Multi-user safe by default. "shared" is an explicit project-wide opt-in.
    memory_scope: Literal["shared", "session", "user"] = "user"
    memory_user_id_from: Literal["sender_id", "metadata"] = (
        "sender_id"  # Only when memory_scope=user
    )
    memory_user_id_metadata_key: str = (
        "user_id"  # When memory_user_id_from=metadata, read from msg.metadata[this key]
    )


class ToolsConfig(BaseModel):
    """Tools configuration."""

    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    # Never expose arbitrary host paths to a user run. Explicit local-only
    # deployments may opt out, but cloud composition keeps this enabled.
    restrict_to_workspace: bool = True
    # Optional network/integration tools are disabled unless explicitly enabled.
    optional_allowlist: list[str] = Field(default_factory=list)
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    capability_plugins: list[str] = Field(default_factory=list)
    discover_capability_plugins: bool = False


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
    group_chat: dict[str, Any] | None = None  # mention_patterns, history_limit (optional)


class CommandsConfig(BaseModel):
    """Channel-native command handling."""

    # Enable native commands (/new, /help). "auto" = current behavior (Telegram registers, Loop handles).
    native: bool | Literal["auto"] = "auto"
    # Enable skill slash commands (reserved for future).
    native_skills: bool | Literal["auto"] = "auto"


class EnvConfig(BaseModel):
    """Inline env vars applied when the process does not define them."""

    vars: dict[str, str] | None = None  # key -> value; applied with setdefault so existing env wins


class RuntimeStoreConfig(BaseModel):
    """Durable agent-runtime storage and distributed worker settings."""

    database_url: str = ""
    pool_min_size: int = 1
    pool_max_size: int = 10
    auto_migrate: bool = True
    lease_seconds: int = 60
    # PostgreSQL NOTIFY is the normal wake-up path.  This is only the durable
    # recovery cadence for listener reconnects and startup races.
    poll_interval_seconds: float = Field(default=0.2, ge=0.1, le=5.0)


class RuntimeConfig(BaseModel):
    """Native runtime process role and persistence configuration."""

    store: RuntimeStoreConfig = Field(default_factory=RuntimeStoreConfig)
    worker_name: str = ""
    scratch_root: str = "~/.joyhousebot/runtime-scratch"


class Config(BaseSettings):
    """Root configuration for joyhousebot."""

    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    messages: MessagesConfig | None = None
    commands: CommandsConfig | None = None
    env: EnvConfig | None = None

    def _match_provider(
        self, model: str | None = None
    ) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config and its registry name. Returns (config, spec_name)."""
        from joyhousebot.providers.registry import PROVIDERS, find_by_name

        model_lower = (model or "").lower()

        # LLM_PROVIDER is an explicit route, especially for gateways such as
        # OpenRouter that serve model families owned by several vendors.  It
        # must win over model-prefix inference and unrelated native keys.
        preferred_name = self.providers.default_provider.strip().lower()
        preferred_spec = find_by_name(preferred_name) if preferred_name else None
        if preferred_spec is not None:
            preferred = getattr(self.providers, preferred_spec.name, None)
            if preferred and (preferred.api_key or preferred_spec.is_local):
                return preferred, preferred_spec.name

        # Match by keyword (order follows PROVIDERS registry)
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and any(kw in model_lower for kw in spec.keywords) and p.api_key:
                return p, spec.name

        # Fallback: gateways first, then others (follows registry order)
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and p.api_key:
                return p, spec.name
        return None, None

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """Get matched provider config (api_key, api_base, extra_headers). Falls back to first available."""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """Get the registry name of the matched provider (e.g. "deepseek", "openrouter")."""
        _, name = self._match_provider(model)
        return name

    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for the given model. Applies default URLs for known gateways."""
        from joyhousebot.providers.registry import find_by_name

        p, name = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        if name:
            spec = find_by_name(name)
            if spec and spec.default_api_base:
                return spec.default_api_base
        return None

    model_config = ConfigDict(env_prefix="JOYHOUSEBOT_", env_nested_delimiter="__")
