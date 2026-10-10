"""FastAPI composition root: no SQL, business execution, or domain mutation here.

Security warning: this version is only for local development and is not a
public, authenticated or multi-tenant Agent Studio.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.routes import agents, runs, system
from .infrastructure.sqlite.migrations import initialize


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise RuntimeError("拒绝启动：尚未完成生产认证和多租户安全隔离")
    initialize()
    yield


app = FastAPI(
    title="Agent Studio Development API",
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def local_clients_only(request: Request, call_next):
    """Fail closed for non-loopback clients while this service has no user auth."""
    peer = request.client.host if request.client else ""
    if peer not in ("127.0.0.1", "::1", "testclient"):
        return JSONResponse(status_code=403, content={"detail": "开发版本仅允许本机访问"})
    return await call_next(request)


app.include_router(system.router)
app.include_router(agents.router)
app.include_router(runs.router)
