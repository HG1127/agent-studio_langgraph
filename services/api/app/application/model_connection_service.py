"""Use cases: validated model catalog, credentials, live discovery and checks."""

from __future__ import annotations

from urllib.parse import urlsplit

from ..domain.model_connections import ConnectionCreate, ConnectionUpdate
from ..infrastructure.model_transport import (
    benchmark_connection,
    discover_models,
    validate_base_url,
)
from ..infrastructure.sqlite import model_connection_repository as store


def _validated(data: ConnectionCreate) -> dict:
    value = data.model_dump()
    value["base_url"] = validate_base_url(value["base_url"])
    if not data.api_key and urlsplit(value["base_url"]).hostname not in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        raise ValueError("非本地供应商需要 API Key")
    return value


def create(workspace_id: str, data: ConnectionCreate) -> dict:
    return store.add_connection(workspace_id, _validated(data))


def update(workspace_id: str, connection_id: str, changes: ConnectionUpdate) -> dict | None:
    value = changes.model_dump(exclude_unset=True)
    if "base_url" in value and value["base_url"] is not None:
        value["base_url"] = validate_base_url(value["base_url"])
    current = store.get_connection(connection_id, workspace_id)
    if current is None:
        return None
    if value.get("protocol") and value["protocol"] != current["protocol"]:
        if current["provider"] != "custom":
            raise ValueError("预设供应商的 API 协议不可修改，请创建自定义连接")
    if value.get("is_default") is False and current["is_default"]:
        raise ValueError("请先将另一条连接设为默认，不能直接取消当前默认连接")
    destination = value.get("base_url") or current["base_url"]
    if (
        not current["api_key_encrypted"]
        and not value.get("api_key")
        and urlsplit(destination).hostname not in ("localhost", "127.0.0.1", "::1")
    ):
        raise ValueError("非本地供应商需要 API Key")
    return store.update_connection(connection_id, workspace_id, value)


def models(workspace_id: str, connection_id: str) -> dict | None:
    connection = store.get_runtime_connection(connection_id, workspace_id)
    return discover_models(connection) if connection else None


def test(workspace_id: str, connection_id: str, model_id: str | None, samples: int) -> dict | None:
    from ..infrastructure.model_transport import ProviderCallError

    connection = store.get_runtime_connection(connection_id, workspace_id)
    if connection is None:
        return None
    if model_id and model_id != connection["model_id"]:
        raise ValueError("请先保存要测试的模型 ID，避免将其他模型的测试误记为当前连接")
    if not connection["model_id"]:
        raise ValueError("请先填写模型 ID，或从供应商模型列表中选择")
    try:
        result = benchmark_connection(connection, samples)
        store.record_test(
            connection_id,
            workspace_id,
            status="ok",
            latency_ms=result["latency_ms"],
            tokens_per_second=result["effective_tokens_per_second"],
        )
        return {"model_id": connection["model_id"], **result}
    except ProviderCallError as exc:
        store.record_test(connection_id, workspace_id, status=exc.code)
        raise
