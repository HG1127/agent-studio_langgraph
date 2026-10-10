"""Task management and read-only execution checkpoint routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from langgraph.checkpoint.sqlite import SqliteSaver

from ... import db
from ...domain.specs import AgentSpec, RunRequest
from ...harness.graph import build_graph

router = APIRouter(prefix="/v1", tags=["runs"])


def _public_run(run: dict) -> dict:
    """Internal leases and frozen configuration are never returned over HTTP."""
    return {
        key: value
        for key, value in run.items()
        if key not in ("spec_snapshot", "lease_until", "heartbeat_at")
    }


def _control(run_id: str, action: str):
    commands = {
        "pause": db.request_pause,
        "cancel": db.request_cancel,
        "resume": db.request_resume,
        "retry": lambda key: db.request_resume(key, retry_failed=True),
    }
    try:
        updated = commands[action](run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _public_run(updated)


@router.post("/agents/{agent_id}/runs", status_code=202)
def create_run(agent_id: str, request: RunRequest):
    if db.get_agent(agent_id) is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return _public_run(db.add_run(agent_id, request.input))


@router.get("/runs")
def list_runs():
    return [_public_run(row) for row in db.list_runs()]


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _public_run(run)


@router.get("/runs/{run_id}/events")
def get_events(run_id: str, after_seq: int = Query(default=0, ge=0)):
    if db.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return db.get_events(run_id, after_seq=after_seq)


@router.get("/runs/{run_id}/checkpoint")
def get_checkpoint(run_id: str):
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    agent = db.get_agent(run["agent_id"])
    if agent is None:
        raise HTTPException(status_code=404, detail="所属 Agent 不存在")
    spec_data = json.loads(run["spec_snapshot"]) if run.get("spec_snapshot") else agent["spec"]
    spec = AgentSpec.model_validate(spec_data)
    with SqliteSaver.from_conn_string(str(db.checkpoint_path())) as saver:
        snapshot = build_graph(saver, run_id, spec).get_state(
            {"configurable": {"thread_id": run_id}}
        )
    values = snapshot.values or {}
    return {
        "available": bool(values),
        "model_rounds": int(values.get("model_rounds", 0)),
        "tool_calls": int(values.get("tool_calls", 0)),
        "next_nodes": list(snapshot.next or ()),
        "final_status": values.get("final_status") or None,
    }


@router.post("/runs/{run_id}/pause")
def pause_run(run_id: str):
    return _control(run_id, "pause")


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: str):
    return _control(run_id, "resume")


@router.post("/runs/{run_id}/retry")
def retry_run(run_id: str):
    return _control(run_id, "retry")


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    return _control(run_id, "cancel")
