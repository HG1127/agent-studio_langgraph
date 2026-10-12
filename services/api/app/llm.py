"""Workspace-scoped model gateway with legacy environment-variable fallback.

Only authenticated API code selects Workspace. Worker uses immutable Run binding.
No fake responses are generated when a provider is unavailable.
"""

from __future__ import annotations

import os

from .infrastructure.model_transport import chat_completion
from .infrastructure.sqlite import model_connection_repository as connections


class ModelNotConfigured(RuntimeError):
    pass


class ModelGateway:
    def __init__(self, connection: dict | None = None):
        if connection is None:
            api_key = os.getenv("MODEL_API_KEY", "").strip()
            if not api_key:
                raise ModelNotConfigured(
                    "模型尚未连接。请在「模型连接」中设置工作空间默认模型，或配置 MODEL_API_KEY。"
                )
            connection = {
                "provider": "custom",
                "legacy_env": True,
                "protocol": "openai",
                "base_url": os.getenv("MODEL_BASE_URL", "https://api.openai.com/v1"),
                "model_id": os.getenv("MODEL_NAME", "gpt-4.1-mini"),
                "api_key": api_key,
            }
        if not connection.get("model_id"):
            raise ModelNotConfigured("已保存连接，但尚未指定模型 ID。请在「模型连接」中选择模型。")
        self.connection = connection

    @classmethod
    def for_workspace(cls, workspace_id: str) -> ModelGateway:
        selected = connections.get_default(workspace_id)
        if selected:
            return cls(connections.get_runtime_connection(selected["id"], workspace_id))
        return cls()

    @classmethod
    def for_run(cls, run_id: str) -> ModelGateway:
        from .infrastructure.sqlite.run_repository import get_run

        run = get_run(run_id)
        if not run or not run.get("workspace_id"):
            raise ModelNotConfigured("Run 缺失授权工作空间，拒绝调用模型")
        if run.get("model_connection_id"):
            selected = connections.get_runtime_connection(
                run["model_connection_id"], run["workspace_id"]
            )
            if selected is None:
                raise ModelNotConfigured("Run 绑定的模型连接已被删除，请恢复连接后重试")
            selected["model_id"] = run["model_name"]
            return cls(selected)
        # Historical/env-only Runs never silently switch to a newly created default.
        return cls()

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_mode: bool = False,
        timeout_seconds: float = 60,
    ) -> dict:
        message, _, _ = chat_completion(
            self.connection,
            messages,
            tools=tools,
            json_mode=json_mode,
            timeout=timeout_seconds,
        )
        return message


def model_for_workspace(workspace_id: str | None) -> dict | None:
    if workspace_id:
        selected = connections.get_default(workspace_id)
        if selected:
            return connections.get_runtime_connection(selected["id"], workspace_id)
    return None


def model_for_run(run_id: str) -> dict | None:
    from .infrastructure.sqlite.run_repository import get_run

    run = get_run(run_id)
    if not run or not run.get("workspace_id"):
        raise ModelNotConfigured("Run 缺少授权工作空间，拒绝调用模型")
    if run.get("model_connection_id"):
        selected = connections.get_runtime_connection(
            run["model_connection_id"], run["workspace_id"]
        )
        if selected is None:
            raise ModelNotConfigured("Run 绑定的模型连接已删除，请恢复连接后重试")
        selected["model_id"] = run["model_name"]
        return selected
    return None


def generate_draft(description: str, workspace_id: str | None = None):
    """Create a real-model Agent draft. Normalize and validate server-side.

    Upstream providers may wrap JSON in Markdown or return a nested goal object.
    One bounded repair request is allowed; failure is reported as an upstream
    format error, never blamed on the user's natural-language description.
    """
    from .application.draft_normalizer import DraftFormatError, normalize_draft

    gateway = ModelGateway(model_for_workspace(workspace_id))
    instruction = (
        "你是 Agent Studio 配置助手。只从用户需求提取声明性配置，"
        "只返回一个 JSON 对象："
        '{"name":"2至100字","description":"可省略",'
        '"task":"至少5字的任务目标",'
        '"success_criteria":["至少一条可验证的成功标准"]}。'
        "不要编造已经连接的工具、权限、知识库或执行结果。"
    )
    messages = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": description},
    ]
    reply = gateway.chat(messages, json_mode=True)
    try:
        return normalize_draft(reply)
    except DraftFormatError:
        # Retry the provider ONCE with a concrete schema reminder; do not
        # echo untrusted provider output back as instructions or leak secrets.
        repair_messages = [
            {
                "role": "system",
                "content": (
                    instruction + "上一次回答无法通过结构校验。请重新生成，仅返回可解析 JSON；"
                    "name/task 都必须是字符串，success_criteria 必须是非空字符串数组。"
                ),
            },
            {"role": "user", "content": description},
        ]
        retry = gateway.chat(repair_messages, json_mode=True)
        try:
            return normalize_draft(retry)
        except DraftFormatError as exc:
            raise DraftFormatError(
                "模型已响应，但连续两次未返回有效的 Agent 配置。"
                "建议检查当前模型是否支持 JSON 格式，或先使用「手动配置草稿」。"
            ) from exc
