"""Strict, versioned AgentSpec for the initial local proof of implementation."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ToolId = Literal["utc_now", "add_numbers"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Goal(StrictModel):
    task: str = Field(min_length=5, max_length=4000)
    success_criteria: list[str] = Field(default_factory=lambda: ["提供明确结果，并说明无法确认的部分"], min_length=1)


class Context(StrictModel):
    sources: list[str] = Field(default_factory=list)
    require_citations: bool = False


class Tools(StrictModel):
    enabled: list[ToolId] = Field(default_factory=lambda: ["utc_now", "add_numbers"])


class State(StrictModel):
    persistence: Literal["event_log_development", "langgraph_sqlite_development"] = "langgraph_sqlite_development"
    resume_enabled: bool = True


class Memory(StrictModel):
    enabled: bool = False
    scope: Literal["none"] = "none"


class Planning(StrictModel):
    enabled: bool = True
    max_replans: int = Field(default=1, ge=0, le=3)


class ToolCalling(StrictModel):
    structured_arguments: bool = True
    retry_limit: int = Field(default=1, ge=0, le=3)


class Permissions(StrictModel):
    allowed_tools: list[ToolId] = Field(default_factory=lambda: ["utc_now", "add_numbers"])
    external_write_allowed: Literal[False] = False


class Environment(StrictModel):
    profile: Literal["local_read_only"] = "local_read_only"


class Observability(StrictModel):
    trace_enabled: bool = True
    redact_secrets: bool = True


class Evaluation(StrictModel):
    suite_id: str | None = None
    enforced: bool = False


class StopConditions(StrictModel):
    max_model_rounds: int = Field(default=8, ge=1, le=20)
    max_tool_calls: int = Field(default=10, ge=0, le=30)
    max_duration_seconds: int = Field(default=120, ge=5, le=600)
    max_no_progress_steps: int = Field(default=3, ge=1, le=10)


class AgentSpec(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=1000)
    execution_mode: Literal["autonomous"] = "autonomous"
    goal: Goal
    context: Context = Field(default_factory=Context)
    tools: Tools = Field(default_factory=Tools)
    state: State = Field(default_factory=State)
    memory: Memory = Field(default_factory=Memory)
    planning: Planning = Field(default_factory=Planning)
    tool_calling: ToolCalling = Field(default_factory=ToolCalling)
    permissions: Permissions = Field(default_factory=Permissions)
    environment: Environment = Field(default_factory=Environment)
    observability: Observability = Field(default_factory=Observability)
    evaluation: Evaluation = Field(default_factory=Evaluation)
    stop_conditions: StopConditions = Field(default_factory=StopConditions)

    @model_validator(mode="after")
    def verify_tools(self):
        if len(self.tools.enabled) != len(set(self.tools.enabled)):
            raise ValueError("不能重复启用同一工具")
        if not set(self.tools.enabled).issubset(set(self.permissions.allowed_tools)):
            raise ValueError("启用的工具必须全部获得权限允许")
        return self


class DraftRequest(StrictModel):
    description: str = Field(min_length=5, max_length=4000)


class RunRequest(StrictModel):
    input: str = Field(min_length=1, max_length=4000)
