"""Configuration schema using Pydantic.

在整体架构中：配置的单一数据模型与默认值，持久化到 ~/.joyhousebot/config.json。
"""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict
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
    proxy: str | None = None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    # Override global commands.native for this channel (OpenClaw channels.telegram.commands.native).
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
    auto_reply_enabled: bool = True  # If false, inbound email is read but no automatic reply is sent
    poll_interval_seconds: int = 30
    mark_seen: bool = True
    max_body_chars: int = 12000
    subject_prefix: str = "Re: "
    allow_from: list[str] = Field(default_factory=list)  # Allowed sender email addresses


class MochatMentionConfig(BaseModel):
    """Mochat mention behavior configuration."""
    require_in_groups: bool = False


class MochatGroupRule(BaseModel):
    """Mochat per-group mention requirement."""
    require_mention: bool = False


class MochatConfig(BaseModel):
    """Mochat channel configuration."""
    enabled: bool = False
    base_url: str = "https://mochat.io"
    socket_url: str = ""
    socket_path: str = "/socket.io"
    socket_disable_msgpack: bool = False
    socket_reconnect_delay_ms: int = 1000
    socket_max_reconnect_delay_ms: int = 10000
    socket_connect_timeout_ms: int = 10000
    refresh_interval_ms: int = 30000
    watch_timeout_ms: int = 25000
    watch_limit: int = 100
    retry_delay_ms: int = 500
    max_retry_attempts: int = 0  # 0 means unlimited retries
    claw_token: str = ""
    agent_user_id: str = ""
    sessions: list[str] = Field(default_factory=list)
    panels: list[str] = Field(default_factory=list)
    allow_from: list[str] = Field(default_factory=list)
    mention: MochatMentionConfig = Field(default_factory=MochatMentionConfig)
    groups: dict[str, MochatGroupRule] = Field(default_factory=dict)
    reply_delay_mode: str = "non-mention"  # off | non-mention
    reply_delay_ms: int = 120000


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
    allow_from: list[str] = Field(default_factory=list)  # Allowed user openids (empty = public access)


class ChannelsConfig(BaseModel):
    """Configuration for chat channels."""
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    mochat: MochatConfig = Field(default_factory=MochatConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    qq: QQConfig = Field(default_factory=QQConfig)


class AgentDefaults(BaseModel):
    """Default agent configuration (used when agents.agent_list is empty)."""
    workspace: str = "~/.joyhousebot/workspace"
    model: str = "anthropic/claude-opus-4-5"
    model_fallbacks: list[str] = Field(default_factory=list)
    provider: str = ""  # Optional: force provider by name (e.g. "zhipu", "openrouter"). Empty = auto-detect from model.
    max_tokens: int = 8192
    temperature: float = 0.7
    max_tool_iterations: int = 20
    memory_window: int = 50
    max_context_tokens: int | None = None  # When set, trim history so total tokens <= this (in addition to memory_window)


class AgentEntry(BaseModel):
    """OpenClaw-style per-agent config (one entry in agents.agent_list)."""
    id: str = ""  # Required, e.g. "main", "work"
    name: str = ""  # Display name; default to id if empty
    workspace: str = "~/.joyhousebot/workspace"
    model: str = "anthropic/claude-opus-4-5"
    model_fallbacks: list[str] = Field(default_factory=list)
    provider: str = ""
    max_tokens: int = 8192
    temperature: float = 0.7
    max_tool_iterations: int = 20
    memory_window: int = 50
    max_context_tokens: int | None = None  # When set, trim history so total tokens <= this
    default: bool = False  # At most one can be True; used when default_id not set
    activated: bool = True  # If True, show in chat page agent radio; default on


def _default_agent_list() -> list[AgentEntry]:
    """Default agents: JoyAgent (default), 编程 (programming), 金融/教育/成长. OpenClaw-style."""
    return [
        AgentEntry(
            id="joy",
            name="JoyAgent",
            workspace="~/.joyhousebot/agents/joy",
            default=True,
        ),
        AgentEntry(
            id="programming",
            name="编程",
            workspace="~/.joyhousebot/agents/programming",
        ),
        AgentEntry(
            id="finance",
            name="金融",
            workspace="~/.joyhousebot/agents/finance",
        ),
        AgentEntry(
            id="education",
            name="教育",
            workspace="~/.joyhousebot/agents/education",
        ),
        AgentEntry(
            id="growth",
            name="成长",
            workspace="~/.joyhousebot/agents/growth",
        ),
    ]


class AgentsConfig(BaseModel):
    """Agent configuration (single default or OpenClaw-style list)."""
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)
    # Use agent_list to avoid shadowing builtin list; alias "list" for JSON/OpenClaw compat
    agent_list: list[AgentEntry] = Field(default_factory=_default_agent_list, alias="list")
    default_id: str | None = "joy"  # Which agent id is default (JoyAgent); if None, use agent_list[].default or first


class ProviderConfig(BaseModel):
    """LLM provider configuration."""
    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)


class ProvidersConfig(BaseModel):
    """Configuration for LLM providers."""
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


class BrowserProfileConfig(BaseModel):
    """Single browser profile (cdp port/url, optional display)."""
    cdp_port: int | None = None
    cdp_url: str = ""
    color: str = ""


class BrowserConfig(BaseModel):
    """Local browser control service (OpenClaw-compatible)."""
    enabled: bool = True
    default_profile: str = "default"
    profiles: dict[str, BrowserProfileConfig] = Field(default_factory=dict)
    executable_path: str = ""  # Chromium path; empty = auto-detect
    headless: bool = False


class GatewayControlUiConfig(BaseModel):
    """Control UI / connect auth overrides (dev or rollout)."""
    # When True, allow connect without token/device (dev only; do not enable in production).
    allow_insecure_auth: bool = False


class GatewayConfig(BaseModel):
    """Gateway/server configuration."""
    host: str = "0.0.0.0"
    port: int = 18790
    # Shared secret for control connect: frontend/CLI pass auth.token matching this.
    control_token: str = ""
    # Alternative: auth.password matching this (optional).
    control_password: str = ""
    # Control UI overrides (e.g. allow_insecure_auth for local dev).
    control_ui: GatewayControlUiConfig | None = None
    # OpenClaw-compatible RPC endpoint switch (/ws/rpc).
    rpc_enabled: bool = True
    # Optional allowlist for staged cutover; empty means all methods enabled.
    rpc_canary_methods: list[str] = Field(default_factory=list)
    # If true, run best-effort shadow comparison for read-only RPC methods.
    rpc_shadow_reads: bool = False
    # Default scopes granted when connect.scopes is omitted.
    rpc_default_scopes: list[str] = Field(
        default_factory=lambda: ["operator.read", "operator.write", "operator.admin"]
    )
    # Node command policy (OpenClaw gateway.nodes.* compat, flattened).
    node_allow_commands: list[str] = Field(default_factory=list)
    node_deny_commands: list[str] = Field(default_factory=list)
    # Browser proxy routing policy (OpenClaw gateway.nodes.browser.* compat).
    node_browser_mode: str = "auto"  # auto | manual | off
    node_browser_target: str = ""
    # Control-plane worker backoff policy (seconds).
    control_plane_claim_retryable_backoff_seconds: float = 3.0
    control_plane_claim_non_retryable_backoff_seconds: float = 15.0
    control_plane_heartbeat_retryable_backoff_seconds: float = 5.0
    control_plane_heartbeat_non_retryable_backoff_seconds: float = 30.0
    # OpenClaw-aligned: one run per session at a time for chat.send/agent; when True, use lane queue (enqueue) instead of in_flight.
    chat_session_serialization: bool = True
    # Max pending requests per session lane (queue full returns in_flight); default 100.
    max_lane_pending: int = 100
    # Reserved: global max concurrent sessions (None = no limit); logic not implemented in this release.
    max_concurrent_sessions: int | None = None
    # Max chars for tool result in trace steps (None = no truncation); default 2000.
    trace_max_step_payload_chars: int | None = 2000


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


class WalletConfig(BaseModel):
    """Wallet configuration: EVM address when enabled (private key encrypted on disk)."""
    enabled: bool = False
    address: str = ""  # EVM address (0x...) when enabled; set after generating/loading wallet


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
    # When True, run command via shell (sh -c) so piping/redirects work (OpenClaw-aligned).
    shell_mode: bool = False
    # Docker container isolation (optional). When True, exec runs inside a container; falls back to direct if Docker unavailable.
    container_enabled: bool = False
    container_image: str = "alpine:3.18"
    # Host path for workspace mount; empty means use working_dir. Container path is /workspace.
    container_workspace_mount: str = ""
    container_user: str = ""  # e.g. "1000:1000"; empty = default
    container_network: str = "none"  # "none" | "host" | bridge name
    container_auto_create: bool = True  # If True, use docker run for each command; if False, expect existing container (not used in Phase 1).


class MCPServerConfig(BaseModel):
    """MCP server connection configuration (stdio or HTTP)."""
    command: str = ""  # Stdio: command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: extra env vars
    url: str = ""  # HTTP: streamable HTTP endpoint URL


class CodeRunnerConfig(BaseModel):
    """Unified code runner tool (Claude Code, Codex, OpenCode backends)."""
    enabled: bool = False  # When True, register code_runner tool (optional, allowlist can gate)
    default_backend: str = "claude_code"
    default_mode: str = "auto"  # host | container | auto
    timeout: int = 300
    # When True, code_runner waits for exec.approval.request/resolve before running (OpenClaw-style).
    require_approval: bool = False
    # Claude Code CLI
    claude_code_command: str = "claude"
    # Container mode (image must have Claude Code CLI installed, e.g. ghcr.io/13rac1/openclaw-claude-code)
    container_image: str = ""
    container_workspace_mount: str = ""
    container_user: str = ""
    container_network: str = "none"


class RetrievalConfig(BaseModel):
    """Retrieval / knowledge base: FTS5 always on; vector layer optional. Memory search: pluggable backend."""
    vector_enabled: bool = False  # When True, enable vector search (requires embedding + backend)
    vector_threshold_chunks: int = 50_000  # Enable vector when chunk count exceeds this
    embedding_provider: str = ""  # e.g. openai (for V2)
    embedding_model: str = ""  # e.g. text-embedding-3-small
    vector_backend: str = ""  # chroma | qdrant | pgvector (for V2)
    # Memory search backend: builtin (grep, default) | mcp_qmd (QMD via MCP) | sqlite_vector (SQLite+embedding index) | auto (mcp_qmd -> sqlite_vector -> builtin)
    memory_backend: str = "builtin"
    # Knowledge search backend: builtin (FTS5+Chroma, default) | qmd (QMD via MCP) | auto (try qmd then builtin)
    knowledge_backend: str = "builtin"
    # When True, pipeline pushes each indexed doc to QMD via knowledge_qmd_sync_url (POST JSON: doc_id, title, chunks)
    knowledge_qmd_sync_enabled: bool = False
    knowledge_qmd_sync_url: str = ""  # e.g. http://localhost:8181/index or QMD index endpoint
    memory_use_l0: bool = False  # When True, system prompt gets L0 (.abstract) + MEMORY.md; when False, only MEMORY.md (legacy)
    memory_first: bool = False  # When True, agent is prompted to consult L0/memory before knowledge base
    memory_top_k: int = 10
    memory_include_daily_in_context: bool = True  # When True (default, OpenClaw-aligned), inject today+yesterday memory/YYYY-MM-DD.md into context
    history_max_entries: int = 0  # When > 0, keep only last N entries in HISTORY.md (0 = no limit)
    # OpenClaw-style memory flush before consolidation: one optional LLM call to capture durable notes before we summarize
    memory_flush_before_consolidation: bool = False
    memory_flush_system_prompt: str = "Session nearing compaction. Output only valid JSON."
    memory_flush_prompt: str = "Write any lasting notes: return JSON with optional keys daily_log_entry (string for memory/YYYY-MM-DD.md) and memory_additions (string to append to MEMORY.md). If nothing to store, return {}."
    memory_vector_enabled: bool = False  # When True, re-rank memory search hits by embedding similarity (OpenClaw-aligned semantic search)
    # Memory isolation: shared (default) | session | user — when session/user, each scope has its own memory/ subdir
    memory_scope: Literal["shared", "session", "user"] = "shared"
    memory_user_id_from: Literal["sender_id", "metadata"] = "sender_id"  # Only when memory_scope=user
    memory_user_id_metadata_key: str = "user_id"  # When memory_user_id_from=metadata, read from msg.metadata[this key]


class IngestConfig(BaseModel):
    """Data acquisition: optional local vs cloud processing per source type."""
    # local = only local; cloud = only cloud (if configured); auto = prefer local, fallback cloud
    pdf_processing: str = "local"  # local | cloud | auto (cloud PDF: reserved for future)
    image_processing: str = "auto"  # local | cloud | auto (cloud = vision/OCR API when configured)
    url_processing: str = "local"  # local | cloud | auto (cloud = optional remote summarizer)
    youtube_processing: str = "auto"  # local_only | allow_cloud | auto (allow_cloud = use transcribe when no subs)
    # Cloud OCR: when image_processing is cloud/auto, optional endpoint or provider
    cloud_ocr_provider: str = ""  # openai_vision | "" (empty = no cloud OCR)
    cloud_ocr_api_key: str = ""  # or use provider's default key from providers config


class KnowledgePipelineConfig(BaseModel):
    """Knowledge base pipeline: source dir (knowledgebase) -> convert to markdown -> processed -> FTS5 index."""
    # Relative to workspace
    knowledge_source_dir: str = "knowledgebase"  # Where to watch for new files (PDF, URL-dumped, etc.)
    knowledge_processed_dir: str = "knowledge/processed"  # Where converted .md and metadata go; only this is indexed
    watch_enabled: bool = True  # When True, watch source dir and enqueue new files
    subprocess_enabled: bool = True  # When True, run knowledge pipeline in subprocess to avoid blocking startup
    convert_chunk_size: int = 1200
    convert_chunk_overlap: int = 200


class ToolsConfig(BaseModel):
    """Tools configuration."""
    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    code_runner: CodeRunnerConfig = Field(default_factory=CodeRunnerConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    knowledge_pipeline: KnowledgePipelineConfig = Field(default_factory=KnowledgePipelineConfig)
    restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory
    # Optional tools are unrestricted by default; set this allowlist to gate them.
    optional_allowlist: list[str] = Field(default_factory=list)
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class SkillEntryConfig(BaseModel):
    """Per-skill configuration (OpenClaw-style enable/disable and per-skill env)."""
    enabled: bool = True
    env: dict[str, str] | None = None  # env vars for this skill (injected when exec cwd is under workspace/skills/<name>)


class SkillsConfig(BaseModel):
    """Skills configuration: per-skill enable/disable."""
    entries: dict[str, SkillEntryConfig] = Field(default_factory=dict)


class PluginLoadConfig(BaseModel):
    """Plugin discovery load paths."""
    paths: list[str] = Field(default_factory=list)


class PluginEntryConfig(BaseModel):
    """Per-plugin state and config."""
    enabled: bool = True
    config: dict[str, object] = Field(default_factory=dict)


class PluginSlotsConfig(BaseModel):
    """Exclusive plugin category slots."""
    memory: str | None = None


class PluginInstallRecord(BaseModel):
    """Tracked plugin install metadata."""
    source: str = ""  # npm | path | archive
    spec: str = ""
    source_path: str = ""
    install_path: str = ""
    version: str = ""
    installed_at: str = ""


class AppsConfig(BaseModel):
    """Which plugin webapps are enabled in the shell. Empty list = all enabled."""
    enabled: list[str] = Field(default_factory=list)


class PluginsConfig(BaseModel):
    """OpenClaw-compatible plugin configuration."""
    enabled: bool = True
    openclaw_dir: str = ""  # OpenClaw 工作区目录，供 plugin_host 桥加载；留空用默认或 JOYHOUSEBOT_OPENCLAW_DIR
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    load: PluginLoadConfig = Field(default_factory=PluginLoadConfig)
    entries: dict[str, PluginEntryConfig] = Field(default_factory=dict)
    slots: PluginSlotsConfig = Field(default_factory=PluginSlotsConfig)
    installs: dict[str, PluginInstallRecord] = Field(default_factory=dict)


class MessagesConfig(BaseModel):
    """OpenClaw-compatible message behaviour (ack reaction, response prefix, etc.)."""
    ack_reaction_scope: str | None = None  # group-mentions | group-all | direct | all
    ack_reaction: str | None = None
    remove_ack_after_reply: bool | None = None
    response_prefix: str | None = None  # template: {model}, {provider}, {identityName}, etc.
    suppress_tool_errors: bool | None = None  # hide tool error warnings from user
    # After tool execution: user message sent to LLM to get final reply. None = use built-in concise prompt.
    after_tool_results_prompt: str | None = None
    group_chat: dict[str, Any] | None = None  # mention_patterns, history_limit (optional)


class ApprovalsExecTargetConfig(BaseModel):
    """Single delivery target for exec approval forwarding (OpenClaw ExecApprovalForwardTarget)."""
    channel: str = ""  # e.g. telegram, discord, slack
    to: str = ""  # destination id (chat_id, user_id, etc.)
    account_id: str | None = None
    thread_id: str | int | None = None


class ApprovalsExecConfig(BaseModel):
    """Exec approval forwarding (OpenClaw ExecApprovalForwardingConfig)."""
    enabled: bool = False
    mode: Literal["session", "targets", "both"] = "session"
    agent_filter: list[str] | None = None  # only forward for these agent IDs
    session_filter: list[str] | None = None  # sessionKey substring or regex patterns
    targets: list[ApprovalsExecTargetConfig] | None = None  # explicit channel/to targets


class ApprovalsConfig(BaseModel):
    """Approvals behaviour (OpenClaw approvals block)."""
    exec: ApprovalsExecConfig | None = None


class CommandsConfig(BaseModel):
    """Native/skill command registration (OpenClaw commands compat)."""
    # Enable native commands (/new, /help). "auto" = current behavior (Telegram registers, Loop handles).
    native: bool | Literal["auto"] = "auto"
    # Enable skill slash commands (reserved for future).
    native_skills: bool | Literal["auto"] = "auto"


class EnvConfig(BaseModel):
    """Inline env vars applied to process when not already set (OpenClaw env.vars compat)."""
    vars: dict[str, str] | None = None  # key -> value; applied with setdefault so existing env wins


class MetaConfig(BaseModel):
    """Config metadata (last touched version/time); migrated from OpenClaw, optionally maintained on save."""
    last_touched_at: str | None = None
    last_touched_version: str | None = None


class WizardConfig(BaseModel):
    """Onboarding/wizard state; migrated from OpenClaw when present."""
    last_run_at: str | None = None
    last_run_version: str | None = None
    last_run_command: str | None = None
    last_run_mode: str | None = None


class CapabilityItem(BaseModel):
    """单个能力项配置"""
    id: str = ""
    name: str = ""
    description: str = ""
    version: str = "1.0"
    enabled: bool = True


class CloudConnectConfig(BaseModel):
    """云端连接配置"""
    enabled: bool = False
    backend_url: str = "ws://localhost:8000/ws/cloud-connect"
    house_name: str = ""
    description: str = ""
    auto_reconnect: bool = True
    reconnect_interval: int = 30
    capabilities: list[CapabilityItem] = Field(
        default_factory=lambda: [
            CapabilityItem(id="chat.v1", name="对话", description="多轮对话与上下文记忆"),
            CapabilityItem(id="code_execution.v1", name="代码执行", description="支持多种编程语言"),
            CapabilityItem(id="file_operations.v1", name="文件操作", description="读写本地文件"),
            CapabilityItem(id="web_search.v1", name="网络搜索", description="搜索互联网信息"),
        ]
    )


class Config(BaseSettings):
    """Root configuration for joyhousebot."""
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    apps: AppsConfig = Field(default_factory=AppsConfig)
    wallet: WalletConfig = Field(default_factory=WalletConfig)
    cloud_connect: CloudConnectConfig = Field(default_factory=CloudConnectConfig)
    messages: MessagesConfig | None = None
    commands: CommandsConfig | None = None
    approvals: ApprovalsConfig | None = None
    env: EnvConfig | None = None
    meta: MetaConfig | None = None
    wizard: WizardConfig | None = None

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path (default agent's workspace)."""
        entry = self._resolve_default_entry()
        return Path(entry.workspace).expanduser()

    def _resolve_default_entry(self) -> "AgentDefaults | AgentEntry":
        """Resolve the default agent entry (defaults or first from list)."""
        if not self.agents.agent_list:
            return self.agents.defaults
        did = self.agents.default_id
        if did:
            for e in self.agents.agent_list:
                if e.id == did:
                    return e
        for e in self.agents.agent_list:
            if getattr(e, "default", False):
                return e
        return self.agents.agent_list[0]

    def get_default_agent_id(self) -> str:
        """Default agent id for cron/chat when not specified."""
        if not self.agents.agent_list:
            return "default"
        did = self.agents.default_id
        if did:
            for e in self.agents.agent_list:
                if e.id == did:
                    return e.id
        for e in self.agents.agent_list:
            if getattr(e, "default", False):
                return e.id
        return self.agents.agent_list[0].id

    def get_agent_entry(self, agent_id: str | None) -> "AgentEntry | AgentDefaults | None":
        """Get agent config by id; None = default; returns AgentEntry or AgentDefaults."""
        if not agent_id or agent_id == "default":
            return self._resolve_default_entry()
        if not self.agents.agent_list:
            return self.agents.defaults if agent_id == "default" else None
        for e in self.agents.agent_list:
            if e.id == agent_id:
                return e
        return None

    def get_agent_model_and_fallbacks(self, agent_id: str | None = None) -> tuple[str, list[str]]:
        """Resolve agent model primary and fallback chain."""
        entry = self.get_agent_entry(agent_id)
        if entry is None:
            return self.agents.defaults.model, list(self.agents.defaults.model_fallbacks)
        model = str(getattr(entry, "model", "") or self.agents.defaults.model)
        fallbacks = list(getattr(entry, "model_fallbacks", []) or [])
        # Deduplicate while preserving order and never repeat primary.
        seen = {model}
        uniq: list[str] = []
        for raw in fallbacks:
            m = str(raw or "").strip()
            if not m or m in seen:
                continue
            seen.add(m)
            uniq.append(m)
        return model, uniq

    def get_agent_list_for_api(self) -> list[dict]:
        """List of agents for API (id, name, workspace, model, is_default)."""
        if not self.agents.agent_list:
            return [{
                "id": "default",
                "name": "默认",
                "workspace": self.agents.defaults.workspace,
                "model": self.agents.defaults.model,
                "model_fallbacks": list(self.agents.defaults.model_fallbacks),
                "provider_name": self.get_provider_name(self.agents.defaults.model),
                "temperature": self.agents.defaults.temperature,
                "max_tokens": self.agents.defaults.max_tokens,
                "max_tool_iterations": self.agents.defaults.max_tool_iterations,
                "memory_window": self.agents.defaults.memory_window,
                "is_default": True,
                "activated": True,
            }]
        default_id = self.get_default_agent_id()
        out = []
        for e in self.agents.agent_list:
            out.append({
                "id": e.id,
                "name": e.name or e.id,
                "workspace": e.workspace,
                "model": e.model,
                "model_fallbacks": list(e.model_fallbacks),
                "provider_name": self.get_provider_name(e.model),
                "temperature": e.temperature,
                "max_tokens": e.max_tokens,
                "max_tool_iterations": e.max_tool_iterations,
                "memory_window": e.memory_window,
                "is_default": e.id == default_id,
                "activated": getattr(e, "activated", True),
            })
        return out

    def _match_provider(self, model: str | None = None) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config and its registry name. Returns (config, spec_name)."""
        from joyhousebot.providers.registry import PROVIDERS, find_by_name
        model_lower = (model or self.agents.defaults.model).lower()
        explicit = (self.agents.defaults.provider or "").strip().lower()

        # If provider is set explicitly, use it when it has api_key
        if explicit:
            spec = find_by_name(explicit)
            if spec:
                p = getattr(self.providers, spec.name, None)
                if p and p.api_key:
                    return p, spec.name

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

    def get_api_key(self, model: str | None = None) -> str | None:
        """Get API key for the given model. Falls back to first available key."""
        p = self.get_provider(model)
        return p.api_key if p else None
    
    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for the given model. Applies default URLs for known gateways."""
        from joyhousebot.providers.registry import find_by_name
        p, name = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        # Only gateways get a default api_base here. Standard providers
        # (like Moonshot) set their base URL via env vars in _setup_env
        # to avoid polluting the global litellm.api_base.
        if name:
            spec = find_by_name(name)
            if spec and spec.is_gateway and spec.default_api_base:
                return spec.default_api_base
        return None
    
    model_config = ConfigDict(
        env_prefix="NANOBOT_",
        env_nested_delimiter="__"
    )
