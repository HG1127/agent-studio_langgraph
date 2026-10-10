"""Run lifecycle orchestration around the reusable LangGraph StateGraph."""

from __future__ import annotations

import json
import logging
import os

from langgraph.checkpoint.sqlite import SqliteSaver

from .. import db
from ..domain.specs import AgentSpec
from ..llm import ModelNotConfigured
from .graph import RunControlled, build_graph, initial_state

logger = logging.getLogger(__name__)


def process_claimed(run_id: str) -> dict | None:
    """Execute one previously claimed task from its latest persisted checkpoint."""
    run = db.get_run(run_id)
    if run and run["status"] in ("PAUSE_REQUESTED", "CANCEL_REQUESTED"):
        db.settle_control(run_id)
        return db.get_run(run_id)
    if not run or run["status"] != "RUNNING":
        return run

    agent = db.get_agent(run["agent_id"])
    if not agent:
        db.finalize_run(run_id, "FAILED", error="Agent 不存在")
        return db.get_run(run_id)

    frozen = json.loads(run["spec_snapshot"]) if run.get("spec_snapshot") else agent["spec"]
    spec = AgentSpec.model_validate(frozen)
    config = {"configurable": {"thread_id": run_id}}
    os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"

    try:
        with SqliteSaver.from_conn_string(str(db.checkpoint_path())) as saver:
            graph = build_graph(saver, run_id, spec)
            snapshot = graph.get_state(config)
            if snapshot.values and not snapshot.next:
                result = snapshot.values
            else:
                seed = None if snapshot.values else initial_state(run, spec)
                db.append_event(
                    run_id,
                    "started" if seed else "checkpoint_resumed",
                    {"checkpoint_resume": seed is None},
                    key="started" if seed else f"resumed:{run['attempts']}",
                )
                for _ in graph.stream(seed, config=config, stream_mode="updates"):
                    db.heartbeat(run_id)
                result = graph.get_state(config).values

        controlled = db.settle_control(run_id)
        if controlled:
            return db.get_run(run_id)
        status = result.get("final_status")
        if status == "SUCCEEDED":
            db.finalize_run(run_id, "SUCCEEDED", output=result.get("output") or "")
        elif status == "PARTIAL":
            db.finalize_run(
                run_id,
                "PARTIAL",
                output=result.get("output") or None,
                error=result.get("stop_reason") or "任务未完整完成",
            )
        else:
            db.finalize_run(run_id, "FAILED", error="图执行结束，但没有合法的终态")
    except RunControlled:
        db.settle_control(run_id)
    except ModelNotConfigured as exc:
        db.append_event(run_id, "failed", {"reason": str(exc)})
        db.finalize_run(run_id, "FAILED", error=str(exc))
    except Exception:
        logger.exception("Agent Run failed: %s", run_id)
        reason = "执行异常：请检查模型配置及后台诊断日志"
        db.append_event(run_id, "failed", {"reason": reason})
        db.finalize_run(run_id, "FAILED", error=reason)

    # Process termination may escape via BaseException, leaving the lease to
    # expire and the next Worker to resume from a graph checkpoint.
    return db.get_run(run_id)


def run_once(worker_id: str = "local-worker") -> dict | None:
    db.register_worker(worker_id)
    run_id = db.claim_next(worker_id)
    if not run_id:
        return None
    return process_claimed(run_id)
