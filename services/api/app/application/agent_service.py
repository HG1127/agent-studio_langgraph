"""Agent creation, versioning and retrieval use cases.

Immutable versions are stored transactionally by the repository. The service
does not expose any persistence-specific SQL or HTTP concerns to the caller.
"""

from ..domain.specs import AgentSpec
from ..infrastructure.sqlite import agent_repository


def create_agent(spec: AgentSpec, workspace_id: str) -> dict:
    return agent_repository.add_agent(spec, workspace_id)


def get_agent(agent_id: str, workspace_id: str) -> dict | None:
    return agent_repository.get_agent(agent_id, workspace_id)


def list_agents(workspace_id: str) -> list[dict]:
    return agent_repository.list_agents(workspace_id)


def list_versions(agent_id: str, workspace_id: str) -> list[dict]:
    return agent_repository.list_agent_versions(agent_id, workspace_id)


def create_version(
    agent_id: str, spec: AgentSpec, expected_version: int, workspace_id: str
) -> dict:
    return agent_repository.create_agent_version(agent_id, spec, expected_version, workspace_id)
