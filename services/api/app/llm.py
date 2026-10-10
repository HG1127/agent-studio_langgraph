"""A real OpenAI-compatible chat completions model adapter; no fake fallback."""
import json
import os

import httpx


class ModelNotConfigured(RuntimeError):
    pass


class ModelGateway:
    def __init__(self):
        self.api_key = os.getenv("MODEL_API_KEY", "").strip()
        self.base_url = os.getenv("MODEL_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.model = os.getenv("MODEL_NAME", "gpt-4.1-mini")
        if not self.api_key:
            raise ModelNotConfigured("模型尚未连接。请设置 MODEL_API_KEY 后再运行；平台不会返回模拟执行结果。")

    def chat(self, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False, timeout_seconds: float = 60) -> dict:
        payload = {"model": self.model, "messages": messages, "temperature": 0.2}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            with httpx.Client(timeout=max(1.0, min(float(timeout_seconds), 60.0))) as client:
                response = client.post(
                    self.base_url + "/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise RuntimeError("模型调用失败，请检查模型配置、额度与网络连接。") from exc


def generate_draft(description: str):
    """Real model creates high-level data; server builds and validates all 12 dimensions."""
    from .schema import AgentSpec, Goal
    gateway = ModelGateway()
    message = gateway.chat(
        [
            {"role": "system", "content": (
                "你是 Agent Studio 配置助手。依据用户需求抽取字段并只返回 JSON 对象："
                "name(2-100字),description,task,success_criteria(至少一项字符串数组)。"
                "不能声称未授权工具已连接，不能虚构研究结论。"
            )},
            {"role": "user", "content": description},
        ],
        json_mode=True,
    )
    try:
        value = json.loads(message["content"])
        spec = AgentSpec(
            name=value["name"],
            description=value.get("description", ""),
            goal=Goal(
                task=value["task"],
                success_criteria=value["success_criteria"],
            ),
        )
        return spec
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("模型生成的结构不符合 AgentSpec，请修改输入并重试。") from exc
