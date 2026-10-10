"""Normalize untrusted LLM draft responses into the server-owned AgentSpec schema.

Only the four declarative fields are accepted from a language model. Tools,
permissions, memory, environment and stop conditions remain server controlled.
Invalid provider output is never silently labelled a successful generation.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from ..domain.specs import AgentSpec, Goal


class DraftFormatError(RuntimeError):
    """A model responded, but failed to provide valid structured Agent data."""


def _as_object(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
        content = "\n".join(text_parts)
    if not isinstance(content, str) or not content.strip():
        raise DraftFormatError("模型没有返回配置内容")
    raw = content.strip()
    if len(raw) > 30_000:
        raise DraftFormatError("模型返回的配置超过安全长度限制")
    fence = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, re.I)
    if fence:
        raw = fence.group(1).strip()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        first = raw.find("{")
        if first < 0:
            raise DraftFormatError("模型没有返回 JSON 对象") from None
        # Some compatible gateways prepend prose even with JSON mode enabled.
        try:
            parsed, _ = json.JSONDecoder().raw_decode(raw[first:])
        except (ValueError, TypeError) as exc:
            raise DraftFormatError("模型生成的 JSON 格式不完整") from exc
    if not isinstance(parsed, dict):
        raise DraftFormatError("模型生成的配置根节点必须是对象")
    return parsed


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalize_draft(message: dict[str, Any]) -> AgentSpec:
    data = _as_object(message.get("content"))
    for key in ("spec", "agent", "data", "result"):
        if (
            key in data
            and isinstance(data[key], dict)
            and not any(name in data for name in ("name", "task", "goal"))
        ):
            data = data[key]
            break

    name = _text(data.get("name")) or _text(data.get("title"))
    if not 2 <= len(name) <= 100:
        raise DraftFormatError("模型生成的 Agent 名称缺失或长度不符合要求")

    goal = data.get("goal")
    nested_goal = goal if isinstance(goal, dict) else {}
    task = (
        _text(data.get("task"))
        or _text(nested_goal.get("task"))
        or _text(goal)
        or _text(data.get("objective"))
    )
    if not 5 <= len(task) <= 4000:
        raise DraftFormatError("模型生成的任务目标缺失或长度不符合要求")

    criteria = data.get("success_criteria", nested_goal.get("success_criteria"))
    if isinstance(criteria, str):
        criteria = [criteria]
    if criteria is None:
        criteria = ["提供可核验的结果，并说明未能确认的部分"]
    if (
        not isinstance(criteria, list)
        or not criteria
        or any(not isinstance(item, str) or not item.strip() for item in criteria)
    ):
        raise DraftFormatError("模型生成的成功标准不是有效的非空字符串列表")
    if len(criteria) > 15:
        raise DraftFormatError("模型生成的成功标准数量超出限制")
    criteria = [item.strip()[:500] for item in criteria]
    description = _text(data.get("description"))[:1000]
    try:
        return AgentSpec(
            name=name,
            description=description,
            goal=Goal(task=task, success_criteria=criteria),
        )
    except (ValueError, TypeError, ValidationError) as exc:
        raise DraftFormatError("模型提供的配置值不符合 AgentSpec 校验规则") from exc
