"""FastAPI development service.

SECURITY: local-only proof-of-implementation. Authentication, tenant isolation,
sandbox and durable worker MUST be implemented before public deployment.
"""
import os
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .llm import ModelNotConfigured, generate_draft
from .runner import execute_run
from .schema import AgentSpec, DraftRequest, RunRequest
from .tools import DESCRIPTIONS


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise RuntimeError("拒绝启动：当前版本没有生产所需的认证与多租户隔离。")
    db.initialize()
    yield


app = FastAPI(title="Agent Studio Development API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/v1/health")
def health():
    return {
        "status": "ok",
        "mode": "local_development_only",
        "model_key_configured": bool(os.getenv("MODEL_API_KEY")),
        "durable_resume_available": False,
        "authentication_available": False,
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
def create_run(agent_id: str, request: RunRequest, background: BackgroundTasks):
    if not db.get_agent(agent_id):
        raise HTTPException(404, detail="Agent 不存在")
    run = db.add_run(agent_id, request.input)
    background.add_task(execute_run, run["id"])
    return run


@app.get("/v1/runs")
def get_runs():
    return db.list_runs()


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, detail="任务不存在")
    return run


@app.get("/v1/runs/{run_id}/events")
def get_run_events(run_id: str):
    if not db.get_run(run_id):
        raise HTTPException(404, detail="任务不存在")
    return db.get_events(run_id)
