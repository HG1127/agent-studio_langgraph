"""Localhost development auth and workspace membership endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ...application import identity_service
from ...domain.errors import AgentNotFound, VersionConflict
from ...domain.identity import Credentials, MembershipCreate, WorkspaceCreate
from ...infrastructure.sqlite import identity_repository as identity
from ..dependencies import CurrentUser, Manager, Scope, origin_guard

router = APIRouter(prefix="/v1", tags=["identity"], dependencies=[Depends(origin_guard)])
COOKIE_NAME = "studio_session"


def set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=False,
        samesite="strict",
        max_age=identity.SESSION_HOURS * 3600,
        path="/",
    )


@router.post("/auth/register", status_code=201)
def register(payload: Credentials, response: Response):
    try:
        result, token = identity_service.register(payload.email, payload.password)
    except VersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    set_cookie(response, token)
    return result


@router.post("/auth/login")
def login(payload: Credentials, response: Response):
    try:
        result, token = identity_service.login(payload.email, payload.password)
    except AgentNotFound as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    set_cookie(response, token)
    return result


@router.post("/auth/logout")
def logout(response: Response, request: Request):
    token = request.cookies.get(COOKIE_NAME, "")
    identity.revoke_session(token)
    response.delete_cookie(COOKIE_NAME, path="/", samesite="strict")
    return {"ok": True}


@router.get("/auth/me")
def me(actor: CurrentUser):
    return {"user": actor.model_dump(), "workspaces": identity.list_workspaces(actor.id)}


@router.get("/workspaces")
def workspaces(actor: CurrentUser):
    return identity.list_workspaces(actor.id)


@router.post("/workspaces", status_code=201)
def create_workspace(payload: WorkspaceCreate, actor: CurrentUser):
    return identity.create_workspace(actor.id, payload.name)


@router.get("/workspaces/{workspace_id}/members")
def get_members(workspace_id: str, scope: Scope):
    if scope.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail="工作空间不匹配")
    return identity.list_members(scope.workspace_id)


@router.post("/workspaces/{workspace_id}/members", status_code=201)
def add_member(
    workspace_id: str,
    payload: MembershipCreate,
    scope: Manager,
):
    if scope.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail="工作空间不匹配")
    try:
        member = identity.add_member(scope.workspace_id, payload.user_email, payload.role)
    except VersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if member is None:
        raise HTTPException(status_code=404, detail="用户未注册")
    return member
