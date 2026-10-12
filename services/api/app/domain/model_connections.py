"""Provider catalog and validated workspace model connection commands."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Protocol = Literal["openai", "anthropic", "gemini", "azure_openai"]

# Presets are endpoints, not claims that every model is available to every key.
PROVIDER_CATALOG: tuple[dict, ...] = (
    {
        "id": "openai",
        "name": "OpenAI",
        "protocol": "openai",
        "base_url": "https://api.openai.com/v1",
        "group": "国际",
    },
    {
        "id": "anthropic",
        "name": "Anthropic Claude",
        "protocol": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "group": "国际",
    },
    {
        "id": "google",
        "name": "Google Gemini",
        "protocol": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "group": "国际",
    },
    {
        "id": "azure",
        "name": "Azure OpenAI",
        "protocol": "azure_openai",
        "base_url": "",
        "group": "云平台",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "protocol": "openai",
        "base_url": "https://api.deepseek.com",
        "group": "国内",
    },
    {
        "id": "qwen-cn",
        "name": "阿里云百炼 · 中国站",
        "protocol": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "group": "国内",
    },
    {
        "id": "qwen-intl",
        "name": "阿里云百炼 · 国际站",
        "protocol": "openai",
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "group": "国际",
    },
    {
        "id": "moonshot",
        "name": "Moonshot / Kimi",
        "protocol": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "group": "国内",
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "protocol": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "group": "国内",
    },
    {
        "id": "siliconflow",
        "name": "硅基流动",
        "protocol": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "group": "国内",
    },
    {
        "id": "volcengine",
        "name": "火山引擎 · 方舟",
        "protocol": "openai",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "group": "国内",
    },
    {
        "id": "xai",
        "name": "xAI Grok",
        "protocol": "openai",
        "base_url": "https://api.x.ai/v1",
        "group": "国际",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "protocol": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "group": "聚合",
    },
    {
        "id": "groq",
        "name": "Groq Cloud",
        "protocol": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "group": "国际",
    },
    {
        "id": "together",
        "name": "Together AI",
        "protocol": "openai",
        "base_url": "https://api.together.xyz/v1",
        "group": "国际",
    },
    {
        "id": "mistral",
        "name": "Mistral AI",
        "protocol": "openai",
        "base_url": "https://api.mistral.ai/v1",
        "group": "国际",
    },
    {
        "id": "cohere",
        "name": "Cohere",
        "protocol": "openai",
        "base_url": "https://api.cohere.ai/compatibility/v1",
        "group": "国际",
    },
    {
        "id": "fireworks",
        "name": "Fireworks AI",
        "protocol": "openai",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "group": "国际",
    },
    {
        "id": "perplexity",
        "name": "Perplexity",
        "protocol": "openai",
        "base_url": "https://api.perplexity.ai",
        "group": "国际",
    },
    {
        "id": "ollama",
        "name": "Ollama · 本地",
        "protocol": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "group": "本地",
    },
    {
        "id": "lmstudio",
        "name": "LM Studio · 本地",
        "protocol": "openai",
        "base_url": "http://127.0.0.1:1234/v1",
        "group": "本地",
    },
    {
        "id": "custom",
        "name": "自定义 / OpenAI 兼容接口",
        "protocol": "openai",
        "base_url": "",
        "group": "自定义",
    },
)
PRESETS = {item["id"]: item for item in PROVIDER_CATALOG}


class ConnectionCreate(BaseModel):
    provider: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=2, max_length=80)
    protocol: Protocol = "openai"
    base_url: str = Field(min_length=8, max_length=512)
    api_key: str = Field(default="", max_length=4096, repr=False)
    model_id: str = Field(default="", max_length=200)
    is_default: bool = False

    @model_validator(mode="after")
    def provider_protocol(self):
        preset = PRESETS.get(self.provider)
        if preset is None:
            raise ValueError("不支持的供应商，请选择自定义连接")
        if self.provider != "custom" and preset["protocol"] != self.protocol:
            raise ValueError("该供应商的连接协议不匹配")
        return self


class ConnectionUpdate(BaseModel):
    protocol: Protocol | None = None
    name: str | None = Field(default=None, min_length=2, max_length=80)
    base_url: str | None = Field(default=None, min_length=8, max_length=512)
    api_key: str | None = Field(default=None, max_length=4096, repr=False)
    model_id: str | None = Field(default=None, max_length=200)
    is_default: bool | None = None


class ModelTestRequest(BaseModel):
    model_id: str | None = Field(default=None, min_length=1, max_length=200)
    samples: int = Field(default=1, ge=1, le=3)
