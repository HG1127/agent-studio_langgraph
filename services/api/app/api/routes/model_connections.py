"""Workspace model connection management. Secrets never returned to the browser."""

from fastapi import APIRouter, HTTPException

from ...application import model_connection_service as service
from ...domain.model_connections import (
    PROVIDER_CATALOG,
    ConnectionCreate,
    ConnectionUpdate,
    ModelTestRequest,
)
from ...infrastructure.model_transport import ProviderCallError
from ...infrastructure.sqlite import model_connection_repository as store
from ..dependencies import CurrentUser, Manager, Scope

router = APIRouter(prefix="/v1/models", tags=["model-connections"])


@router.get("/providers")
def providers(user: CurrentUser):
    return PROVIDER_CATALOG


@router.get("/connections")
def connections(scope: Scope):
    return store.list_connections(scope.workspace_id)


@router.post("/connections", status_code=201)
def add_connection(data: ConnectionCreate, scope: Manager):
    try:
        return service.create(scope.workspace_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/connections/{connection_id}")
def update_connection(connection_id: str, data: ConnectionUpdate, scope: Manager):
    try:
        result = service.update(scope.workspace_id, connection_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="模型连接不存在")
    return result


@router.delete("/connections/{connection_id}")
def remove_connection(connection_id: str, scope: Manager):
    if not store.delete_connection(connection_id, scope.workspace_id):
        raise HTTPException(status_code=404, detail="模型连接不存在")
    return {"deleted": True}


@router.post("/connections/{connection_id}/discover")
def discover(connection_id: str, scope: Manager):
    try:
        result = service.models(scope.workspace_id, connection_id)
    except ProviderCallError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="模型连接不存在")
    return result


@router.post("/connections/{connection_id}/test")
def test_connection(connection_id: str, data: ModelTestRequest, scope: Manager):
    try:
        result = service.test(scope.workspace_id, connection_id, data.model_id, data.samples)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderCallError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="模型连接不存在")
    return result
