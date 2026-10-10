"""Cookie session authentication and workspace authorization."""

from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request

from ..domain.identity import Actor, WorkspaceScope
from ..infrastructure.sqlite import identity_repository as identity


def current_user(studio_session: str | None = Cookie(default=None)) -> Actor:
    user = identity.resolve_session(studio_session or "")
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return Actor(id=user["id"], email=user["email"])


CurrentUser = Annotated[Actor, Depends(current_user)]


def current_workspace(
    actor: CurrentUser,
    x_workspace_id: str | None = Header(default=None),
) -> WorkspaceScope:
    if not x_workspace_id or len(x_workspace_id) > 128:
        raise HTTPException(status_code=400, detail="需要指定有效工作空间")
    membership = identity.get_membership(actor.id, x_workspace_id)
    if not membership:
        raise HTTPException(status_code=403, detail="无权访问此工作空间")
    return WorkspaceScope(
        user_id=actor.id,
        workspace_id=x_workspace_id,
        role=membership["role"],
    )


Scope = Annotated[WorkspaceScope, Depends(current_workspace)]


def editor(scope: Scope) -> WorkspaceScope:
    if not scope.can_edit:
        raise HTTPException(status_code=403, detail="此工作空间为只读权限")
    return scope


Editor = Annotated[WorkspaceScope, Depends(editor)]


def manager(scope: Scope) -> WorkspaceScope:
    if not scope.can_manage_members:
        raise HTTPException(status_code=403, detail="只有管理员能够管理成员")
    return scope


Manager = Annotated[WorkspaceScope, Depends(manager)]


def origin_guard(request: Request) -> None:
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and origin not in (
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ):
            raise HTTPException(status_code=403, detail="跨站请求被拒绝")
