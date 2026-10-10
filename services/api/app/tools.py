"""Server-owned, read-only tool registry (no model-generated arbitrary code)."""
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field


class UtcNowArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddNumbersArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: float = Field(ge=-1e12, le=1e12)
    b: float = Field(ge=-1e12, le=1e12)


ARG_MODELS = {"utc_now": UtcNowArgs, "add_numbers": AddNumbersArgs}
DESCRIPTIONS = {
    "utc_now": "获取服务器当前 UTC 时间。用于需要真实当前时间的任务。",
    "add_numbers": "计算两个数的精确加法（浮点范围内）。",
}


def function_definitions(allowed: list[str]) -> list[dict]:
    return [
        {"type": "function",
         "function": {
             "name": key,
             "description": DESCRIPTIONS[key],
             "parameters": ARG_MODELS[key].model_json_schema(),
         }}
        for key in allowed
    ]


def execute_tool(name: str, arguments: dict, allowed: set[str]) -> dict:
    if name not in allowed or name not in ARG_MODELS:
        raise PermissionError("Tool is not registered and permitted")
    parsed = ARG_MODELS[name].model_validate(arguments)
    if name == "utc_now":
        return {"utc_iso": datetime.now(timezone.utc).isoformat()}
    if name == "add_numbers":
        return {"result": parsed.a + parsed.b}
    raise PermissionError("Tool is unavailable")
