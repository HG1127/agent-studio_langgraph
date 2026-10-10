"""Local development readiness and read-only tool catalogue."""

import os

from fastapi import APIRouter

from ... import db
from ...tools import DESCRIPTIONS

router = APIRouter(prefix="/v1", tags=["system"])


@router.get("/health")
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


@router.get("/tools")
def list_tools():
    return [
        {"id": name, "description": description, "risk": "read_only"}
        for name, description in DESCRIPTIONS.items()
    ]
