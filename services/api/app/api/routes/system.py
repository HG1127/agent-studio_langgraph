"""Local development readiness and read-only tool catalogue."""

import os

from fastapi import APIRouter

from ... import db
from ...tools import DESCRIPTIONS
from ..dependencies import CurrentUser

router = APIRouter(prefix="/v1", tags=["system"])


@router.get("/health")
def health():
    return {
        "status": "ok",
        "mode": "local_development_only",
        "database_backend": "sqlite_development",
        "postgres_schema_status": "prepared_not_activated",
        "model_key_configured": bool(os.getenv("MODEL_API_KEY")),
        "worker_online": db.worker_online(),
        "checkpointing": "langgraph_sqlite_development",
        "durable_resume_available": True,
        "authentication_available": True,
        "production_safe": False,
    }


@router.get("/tools")
def list_tools(actor: CurrentUser):
    return [
        {"id": name, "description": description, "risk": "read_only"}
        for name, description in DESCRIPTIONS.items()
    ]
