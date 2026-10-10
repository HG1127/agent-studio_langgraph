"""Agent creation, immutable versions and natural-language draft routes."""

from fastapi import APIRouter, HTTPException

from ...application import agent_service
from ...domain.errors import AgentNotFound, VersionConflict
from ...domain.specs import AgentSpec, AgentVersionCreate, DraftRequest
from ...llm import ModelNotConfigured, generate_draft

router = APIRouter(prefix="/v1", tags=["agents"])


@router.post("/agents/drafts/generate")
def generate_agent_draft(request: DraftRequest):
    try:
        return {"spec": generate_draft(request.description).model_dump()}
    except ModelNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/agents", status_code=201)
def create_agent(spec: AgentSpec):
    return agent_service.create_agent(spec)


@router.get("/agents")
def list_agents():
    return agent_service.list_agents()


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    agent = agent_service.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return agent


@router.get("/agents/{agent_id}/versions")
def list_versions(agent_id: str):
    if agent_service.get_agent(agent_id) is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return agent_service.list_versions(agent_id)


@router.post("/agents/{agent_id}/versions", status_code=201)
def create_version(agent_id: str, request: AgentVersionCreate):
    try:
        return agent_service.create_version(agent_id, request.spec, request.expected_version)
    except AgentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
