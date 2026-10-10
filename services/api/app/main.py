"""FastAPI development API backed by a separate LangGraph Worker.

WARNING: no authentication or multi-tenancy. APP_ENV=production is refused.
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db
from .llm import ModelNotConfigured, generate_draft
from .schema import AgentSpec, DraftRequest, RunRequest
from .tools import DESCRIPTIONS


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise RuntimeError("拒绝启动：当前版本没有生产所需的认证与多租户隔离。")
    db.initialize()
    yield


app = FastAPI(title="Agent Studio Development API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

@app.middleware("http")
async def local_clients_only(request: Request, call_next):
    # No authentication exists yet. Refuse even accidental remote requests while
    # the local development version is bound to an external interface.
    peer = request.client.host if request.client else ""
    if peer not in ("127.0.0.1", "::1", "testclient"):
        return JSONResponse(status_code=403, content={"detail": "开发版本仅允许本机访问"})
    return await call_next(request)



def _expose_run(run: dict) -> dict:
    """Do not send frozen Agent configuration or internal leases to the browser."""
    return {key: value for key, value in run.items()
            if key not in ("spec_snapshot", "lease_until", "heartbeat_at")}


def _control(run_id: str, action: str):
    method = {
        "pause": db.request_pause,
        "cancel": db.request_cancel,
        "resume": db.request_resume,
        "retry": lambda key: db.request_resume(key, retry_failed=True),
    }[action]
    try:
        result = method(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _expose_run(result)


@app.get("/v1/health")
def health():
    return {
        "status": "ok",
        "mode": "local_development_only",
        "model_key_configured": bool(os.getenv("MODEL_API_KEY")),
        "worker_online": db.worker_online(),
        "checkpointing": "langgraph_sqlite_development",
        "durable_resume_available": True,
        "authentication_available": False,
        "production_safe": False,
    }


@app.get("/v1/tools")
def list_tools():
    return [{"id": key, "description": value, "risk": "read_only"} for key, value in DESCRIPTIONS.items()]


@app.post("/v1/agents/drafts/generate")
def create_draft(request: DraftRequest):
    try:
        return {"spec": generate_draft(request.description).model_dump()}
    except ModelNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/v1/agents", status_code=201)
def create_agent(spec: AgentSpec):
    return db.add_agent(spec)


@app.get("/v1/agents")
def get_agents():
    return db.list_agents()


@app.get("/v1/agents/{agent_id}")
def get_agent(agent_id: str):
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, detail="Agent 不存在")
    return agent


@app.post("/v1/agents/{agent_id}/runs", status_code=202)
def create_run(agent_id: str, request: RunRequest):
    if not db.get_agent(agent_id):
        raise HTTPException(404, detail="Agent 不存在")
    # Persistent queue; an independent worker claims and executes the run.
    return _expose_run(db.add_run(agent_id, request.input))


@app.get("/v1/runs")
def get_runs():
    return [_expose_run(run) for run in db.list_runs()]


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, detail="任务不存在")
    return _expose_run(run)


@app.get("/v1/runs/{run_id}/events")
def get_run_events(run_id: str, after_seq: int = Query(default=0, ge=0)):
    if not db.get_run(run_id):
        raise HTTPException(404, detail="任务不存在")
    return db.get_events(run_id, after_seq=after_seq)


@app.get("/v1/runs/{run_id}/checkpoint")
def get_run_checkpoint(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="任务不存在")
    from langgraph.checkpoint.sqlite import SqliteSaver
    from .runner import build_graph
    import json

    agent = db.get_agent(run["agent_id"])
    if not agent:
        raise HTTPException(status_code=404, detail="所属 Agent 不存在")
    data = json.loads(run["spec_snapshot"]) if run.get("spec_snapshot") else agent["spec"]
    spec = AgentSpec.model_validate(data)
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


@app.post("/v1/runs/{run_id}/pause")
def pause_run(run_id: str):
    return _control(run_id, "pause")


@app.post("/v1/runs/{run_id}/resume")
def resume_run(run_id: str):
    return _control(run_id, "resume")


@app.post("/v1/runs/{run_id}/retry")
def retry_run(run_id: str):
    return _control(run_id, "retry")


@app.post("/v1/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    return _control(run_id, "cancel")
