"""Supported model endpoints for native HTTP adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    name: str
    keywords: tuple[str, ...]
    protocol: str
    default_api_base: str = ""
    is_gateway: bool = False
    is_local: bool = False

    @property
    def env_key(self) -> str:
        return {
            "custom": "OPENAI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "aihubmix": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "zhipu": "ZHIPUAI_API_KEY",
            "dashscope": "DASHSCOPE_API_KEY",
            "moonshot": "MOONSHOT_API_KEY",
            "minimax": "MINIMAX_API_KEY",
            "vllm": "",
            "groq": "GROQ_API_KEY",
        }[self.name]


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec("custom", (), "openai", "", is_gateway=True),
    ProviderSpec(
        "openrouter",
        ("openrouter",),
        "openai",
        "https://openrouter.ai/api/v1",
        is_gateway=True,
    ),
    ProviderSpec(
        "aihubmix", ("aihubmix",), "openai", "https://aihubmix.com/v1", is_gateway=True
    ),
    ProviderSpec("anthropic", ("anthropic", "claude"), "anthropic", "https://api.anthropic.com/v1"),
    ProviderSpec("openai", ("openai", "gpt", "o1", "o3", "o4"), "openai", "https://api.openai.com/v1"),
    ProviderSpec("deepseek", ("deepseek",), "openai", "https://api.deepseek.com/v1"),
    ProviderSpec(
        "gemini",
        ("gemini",),
        "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai",
    ),
    ProviderSpec("zhipu", ("zhipu", "glm", "zai"), "openai", "https://open.bigmodel.cn/api/paas/v4"),
    ProviderSpec(
        "dashscope",
        ("qwen", "dashscope"),
        "openai",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    ProviderSpec("moonshot", ("moonshot", "kimi"), "openai", "https://api.moonshot.ai/v1"),
    ProviderSpec("minimax", ("minimax",), "openai", "https://api.minimax.io/v1"),
    ProviderSpec("vllm", ("vllm",), "openai", "", is_local=True),
    ProviderSpec("groq", ("groq",), "openai", "https://api.groq.com/openai/v1"),
)


def find_by_name(name: str) -> ProviderSpec | None:
    normalized = name.strip().lower()
    return next((item for item in PROVIDERS if item.name == normalized), None)


def find_by_model(model: str) -> ProviderSpec | None:
    normalized = model.lower()
    for item in PROVIDERS:
        if item.is_gateway or item.is_local:
            continue
        if any(keyword in normalized for keyword in item.keywords):
            return item
    return None
