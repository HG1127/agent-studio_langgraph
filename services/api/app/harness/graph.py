"""LangGraph-based *single-worker development* Harness.

StateGraph execution is persisted with SQLiteSaver, separate from the task ledger.
Run a worker via 'python -m app.worker'. One worker, read-only tools only. Production
requires PostgreSQL, tenant authentication, stronger leases, and write idempotency.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from .. import db
from ..domain.specs import AgentSpec
from ..llm import ModelGateway, model_for_run
from ..tools import execute_tool, function_definitions

# Restrict LangGraph checkpoint deserialization to known-safe types.
os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"


class RunControlled(Exception):
    """Pause or cancel requested by the user."""


class WorkState(TypedDict, total=False):
    messages: list[dict[str, Any]]
    pending_tools: list[dict[str, Any]]
    model_rounds: int
    tool_calls: int
    fingerprints: dict[str, int]
    output: str
    final_status: str
    stop_reason: str
    plan: list[str]


def _control(run_id: str, max_duration_seconds: int) -> float:
    current = db.get_run(run_id)
    if not current:
        raise RunControlled("Run no longer exists")
    if current["status"] in ("PAUSE_REQUESTED", "CANCEL_REQUESTED"):
        raise RunControlled(current["status"])
    if current["status"] != "RUNNING":
        raise RunControlled(f"Run status changed: {current['status']}")
    start = current["started_at"]
    if start:
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(start)).total_seconds()
        if elapsed >= max_duration_seconds:
            return 0.0
        return max_duration_seconds - elapsed
    return float(max_duration_seconds)


def build_graph(checkpointer: SqliteSaver, run_id: str, spec: AgentSpec):
    """Graph nodes are deliberately deterministic except model and *read-only* tools."""
    bounds = spec.stop_conditions
    allowed = set(spec.tools.enabled).intersection(spec.permissions.allowed_tools)
    tool_definitions = function_definitions(sorted(allowed))

    def prepare(state: WorkState) -> dict:
        remaining = _control(run_id, bounds.max_duration_seconds)
        if remaining <= 0:
            return {"final_status": "PARTIAL", "stop_reason": "max_duration"}
        steps = [
            "明确目标与成功条件",
            "检查可用的工具与上下文",
            "调用模型并在必要时执行获授权的只读工具",
            "检查输出，解释尚未核实的内容",
        ]
        db.append_event(run_id, "plan", {"steps": steps}, key="plan")
        return {"plan": steps}

    def decide(state: WorkState) -> dict:
        remaining = _control(run_id, bounds.max_duration_seconds)
        if remaining <= 0:
            return {"final_status": "PARTIAL", "stop_reason": "max_duration"}
        rounds = state.get("model_rounds", 0)
        if rounds >= bounds.max_model_rounds:
            return {"final_status": "PARTIAL", "stop_reason": "max_model_rounds"}
        # Tool discovery is server-owned; model may only request registered tools.
        raw = ModelGateway(model_for_run(run_id)).chat(
            state["messages"], tool_definitions, timeout_seconds=remaining
        )
        remaining = _control(run_id, bounds.max_duration_seconds)
        if remaining <= 0:
            return {"final_status": "PARTIAL", "stop_reason": "max_duration"}
        calls = raw.get("tool_calls") or []
        # A malformed provider response must be reported; do not invent answers.
        if not isinstance(calls, list):
            raise RuntimeError("模型工具请求结构不正确")
        message = {"role": "assistant", "content": raw.get("content")}
        if calls:
            message["tool_calls"] = calls
        updated = list(state["messages"]) + [message]
        db.append_event(
            run_id,
            "model_step",
            {
                "round": rounds + 1,
                "tool_requests": len(calls),
                "has_text": bool(raw.get("content")),
            },
            key=f"model:{rounds + 1}",
        )
        if not calls:
            return {
                "messages": updated,
                "model_rounds": rounds + 1,
                "pending_tools": [],
                "output": raw.get("content") or "",
            }
        return {
            "messages": updated,
            "model_rounds": rounds + 1,
            "pending_tools": calls,
        }

    def use_tools(state: WorkState) -> dict:
        messages = list(state["messages"])
        count = state.get("tool_calls", 0)
        fingerprints = dict(state.get("fingerprints", {}))
        for item in state.get("pending_tools", []):
            remaining = _control(run_id, bounds.max_duration_seconds)
            if remaining <= 0:
                return {
                    "tool_calls": count,
                    "messages": messages,
                    "final_status": "PARTIAL",
                    "stop_reason": "max_duration",
                    "fingerprints": fingerprints,
                }
            if count >= bounds.max_tool_calls:
                return {
                    "tool_calls": count,
                    "messages": messages,
                    "final_status": "PARTIAL",
                    "stop_reason": "max_tool_calls",
                    "fingerprints": fingerprints,
                }
            if not isinstance(item, dict):
                raise RuntimeError("模型返回了无法识别的工具调用")
            command = item.get("function") or {}
            name = command.get("name", "")
            raw_args = command.get("arguments") or "{}"
            if not isinstance(raw_args, str) or not isinstance(name, str):
                raise RuntimeError("模型工具参数结构不正确")
            call_id = item.get("id")
            if not isinstance(call_id, str) or not call_id:
                raise RuntimeError("模型缺少工具调用 ID")
            fingerprint = name + ":" + raw_args
            fingerprints[fingerprint] = fingerprints.get(fingerprint, 0) + 1
            if fingerprints[fingerprint] > bounds.max_no_progress_steps:
                return {
                    "tool_calls": count,
                    "messages": messages,
                    "final_status": "PARTIAL",
                    "stop_reason": "no_progress",
                    "fingerprints": fingerprints,
                }
            count += 1
            try:
                result = execute_tool(name, json.loads(raw_args), allowed)
                db.append_event(
                    run_id, "tool_completed", {"tool": name, "result": result}, key=f"tool:{count}"
                )
            except (PermissionError, ValueError, TypeError) as exc:
                result = {"error": str(exc)}
                db.append_event(
                    run_id, "tool_rejected", {"tool": name, "reason": str(exc)}, key=f"tool:{count}"
                )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        return {
            "tool_calls": count,
            "messages": messages,
            "pending_tools": [],
            "fingerprints": fingerprints,
        }

    def verify(state: WorkState) -> dict:
        remaining = _control(run_id, bounds.max_duration_seconds)
        if remaining <= 0:
            return {"final_status": "PARTIAL", "stop_reason": "max_duration"}
        response = state.get("output", "")
        if not isinstance(response, str) or not response.strip():
            raise RuntimeError("模型未返回可用答案；没有通过最低结构检查")
        # This verifies non-empty output only. Business accuracy Evals are NOT implemented.
        db.append_event(
            run_id,
            "completed",
            {"reason": "model_final_response", "quality_evaluated": False},
            key="completed",
        )
        return {"final_status": "SUCCEEDED"}

    def stop(state: WorkState) -> dict:
        reason = state.get("stop_reason") or "unknown"
        db.append_event(run_id, "stopped", {"reason": reason}, key=f"stop:{reason}")
        return {"final_status": "PARTIAL"}

    def route_prepare(state: WorkState) -> str:
        return "stop" if state.get("final_status") == "PARTIAL" else "decide"

    def route_decide(state: WorkState) -> str:
        if state.get("final_status") == "PARTIAL":
            return "stop"
        return "tools" if state.get("pending_tools") else "verify"

    def route_tools(state: WorkState) -> str:
        return "stop" if state.get("final_status") == "PARTIAL" else "decide"

    builder = StateGraph(WorkState)
    builder.add_node("prepare", prepare)
    builder.add_node("decide", decide)
    builder.add_node("tools", use_tools)
    builder.add_node("verify", verify)
    builder.add_node("stop", stop)
    builder.add_edge(START, "prepare")
    builder.add_conditional_edges("prepare", route_prepare, {"stop": "stop", "decide": "decide"})
    builder.add_conditional_edges(
        "decide", route_decide, {"stop": "stop", "tools": "tools", "verify": "verify"}
    )
    builder.add_conditional_edges("tools", route_tools, {"stop": "stop", "decide": "decide"})
    builder.add_edge("stop", END)
    builder.add_edge("verify", END)
    return builder.compile(checkpointer=checkpointer)


def initial_state(run: dict, spec: AgentSpec) -> WorkState:
    system = (
        f"你是 {spec.name}，目标：{spec.goal.task}。"
        f"成功标准：{'；'.join(spec.goal.success_criteria)}。"
        "你只能请求服务端允许的只读工具。"
        "不能虚构来源、数据、工具执行或成功结果。对于不能核实的信息，要明确说明。"
    )
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": run["input"]},
        ],
        "pending_tools": [],
        "model_rounds": 0,
        "tool_calls": 0,
        "fingerprints": {},
        "output": "",
        "final_status": "",
        "stop_reason": "",
        "plan": [],
    }
