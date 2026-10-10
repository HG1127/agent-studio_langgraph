"""Development single-run executor: persisted events, strict tool limits, no resume yet.

This synchronous worker is *not* LangGraph / production durable execution.
The roadmap intentionally replaces it with a checkpointed worker.
"""
import json
from time import monotonic

from . import db
from .llm import ModelGateway
from .schema import AgentSpec
from .tools import execute_tool, function_definitions


def execute_run(run_id: str):
    run = db.get_run(run_id)
    if run is None:
        return
    agent = db.get_agent(run["agent_id"])
    if agent is None:
        db.update_run(run_id, "FAILED", error="Agent 不存在")
        return

    spec = AgentSpec.model_validate(agent["spec"])
    allowed = set(spec.tools.enabled) & set(spec.permissions.allowed_tools)
    stops = spec.stop_conditions
    start = monotonic()
    model_rounds = 0
    tool_calls = 0
    recent_calls: dict[str, int] = {}

    db.update_run(run_id, "RUNNING")
    db.append_event(run_id, "started", {"message": "真实模型执行开始"})

    try:
        llm = ModelGateway()
        messages = [
            {
                "role": "system",
                "content": (
                    f"你是名为{spec.name}的 AI Agent。目标：{spec.goal.task}。"
                    f"成功标准：{'；'.join(spec.goal.success_criteria)}。"
                    "允许仅调用服务端提供的只读工具。不可声称没有调用过的工具已经执行。"
                    "对无法确认的信息必须明确说明，不得虚构来源。"
                ),
            },
            {"role": "user", "content": run["input"]},
        ]
        tools = function_definitions(sorted(allowed))

        while model_rounds < stops.max_model_rounds:
            if monotonic() - start > stops.max_duration_seconds:
                db.append_event(run_id, "stopped", {"reason": "max_duration"})
                db.update_run(run_id, "PARTIAL", error="任务达到时间限制")
                return
            model_rounds += 1
            answer = llm.chat(messages, tools)
            requested = answer.get("tool_calls") or []
            assistant_message = {"role": "assistant", "content": answer.get("content")}
            if requested:
                assistant_message["tool_calls"] = requested
            messages.append(assistant_message)
            db.append_event(
                run_id, "model_step",
                {"round": model_rounds, "tool_requests": len(requested),
                 "has_text": bool(answer.get("content"))},
            )

            if not requested:
                content = answer.get("content") or ""
                if not content:
                    raise RuntimeError("模型未返回有效的答案或工具调用")
                db.append_event(run_id, "completed", {"reason": "model_final_response"})
                db.update_run(run_id, "SUCCEEDED", output=content)
                return

            for requested_tool in requested:
                if monotonic() - start > stops.max_duration_seconds:
                    db.append_event(run_id, "stopped", {"reason": "max_duration"})
                    db.update_run(run_id, "PARTIAL", error="任务达到时间限制")
                    return
                if tool_calls >= stops.max_tool_calls:
                    db.append_event(run_id, "stopped", {"reason": "max_tool_calls"})
                    db.update_run(run_id, "PARTIAL", error="达到工具调用次数上限")
                    return
                call = requested_tool.get("function") or {}
                name = call.get("name", "")
                raw_args = call.get("arguments") or "{}"
                fingerprint = name + ":" + raw_args
                recent_calls[fingerprint] = recent_calls.get(fingerprint, 0) + 1
                if recent_calls[fingerprint] > stops.max_no_progress_steps:
                    db.append_event(run_id, "stopped", {"reason": "no_progress"})
                    db.update_run(run_id, "PARTIAL", error="连续重复调用未产生新进展")
                    return
                tool_calls += 1
                try:
                    result = execute_tool(name, json.loads(raw_args), allowed)
                    db.append_event(run_id, "tool_completed", {"tool": name, "result": result})
                except (PermissionError, ValueError, TypeError) as exc:
                    result = {"error": str(exc)}
                    db.append_event(run_id, "tool_rejected", {"tool": name, "reason": str(exc)})
                messages.append({
                    "role": "tool", "tool_call_id": requested_tool["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })

        db.append_event(run_id, "stopped", {"reason": "max_model_rounds"})
        db.update_run(run_id, "PARTIAL", error="达到模型交互轮次上限")
    except Exception as exc:
        # Never leak model credentials or raw user secrets in user-visible logs.
        from .llm import ModelNotConfigured
        message = str(exc) if isinstance(exc, ModelNotConfigured) else "执行异常：请检查模型服务或查看服务端诊断日志"
        db.append_event(run_id, "failed", {"reason": message})
        db.update_run(run_id, "FAILED", error=message)
