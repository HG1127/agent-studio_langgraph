"""Bounded outbound model transport for OpenAI-compatible, Claude and Gemini APIs.

Local development ONLY. Prevent loopback proxying to arbitrary LAN services,
reject redirects, and never expose provider response bodies or secrets in errors.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
import uuid
from urllib.parse import quote, urlsplit

import httpx

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ProviderCallError(RuntimeError):
    def __init__(self, code: str, status_code: int | None = None):
        messages = {
            "unauthorized": "API Key 无效或没有相应模型权限（401/403）。",
            "not_found": "模型、部署名称或接口路径不存在（404）；请核对模型 ID 与 Base URL。",
            "rate_limited": "供应商返回 429：额度或速率限制；请查看供应商控制台。",
            "upstream_rejected": "供应商拒绝请求；可能是不支持的模型或调用参数。",
            "upstream_unavailable": "供应商服务暂不可用，请稍后再试。",
            "network": "连接超时或网络无法访问供应商接口。",
            "invalid_response": "供应商响应格式不符合当前协议。",
            "unsupported": "此供应商无法自动列出模型，请在界面手动填写模型/部署 ID。",
            "unsafe_endpoint": "Base URL 不安全：只允许公网 HTTPS 或本机回环 HTTP，禁止内网地址和跳转。",
        }
        super().__init__(messages.get(code, "模型接口请求失败"))
        self.code = code
        self.status_code = status_code


def validate_base_url(url: str, *, legacy_env: bool = False) -> str:
    try:
        parsed = urlsplit(url.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Base URL 格式错误") from exc
    if (
        not host
        or parsed.scheme not in ("http", "https")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or " " in url
        or "\\" in url
        or (parsed.path and "//" in parsed.path)
    ):
        raise ValueError("Base URL 必须是无查询参数、无账号密码的 http(s) API 根地址")
    is_loopback = host.lower() in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme == "http" and not is_loopback:
        raise ValueError("非本机地址必须使用 HTTPS")
    if parsed.scheme == "http" and not legacy_env and (port is None or port < 1024):
        raise ValueError("本地 HTTP 必须使用显式指定的非特权端口（1024–65535）")
    if parsed.scheme == "https" and port and port not in (443, 8443):
        raise ValueError("公网 HTTPS 接口仅允许 443 或 8443 端口")
    if not is_loopback:
        try:
            literal = ipaddress.ip_address(host)
            if not literal.is_global:
                raise ValueError("禁止连接内网、保留或回环 IP")
        except ValueError as exc:
            if "禁止连接" in str(exc):
                raise
    return url.strip().rstrip("/")


def _check_destination(url: str) -> None:
    hostname = urlsplit(url).hostname or ""
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return
    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror) as exc:
        raise ProviderCallError("network") from exc
    if not addresses or any(not ipaddress.ip_address(info[4][0]).is_global for info in addresses):
        raise ProviderCallError("unsafe_endpoint")
    # This DNS check reduces accidental SSRF; public deployment requires an
    # egress proxy that pins the approved IP across connection establishment.


def _headers(connection: dict) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = connection.get("api_key") or ""
    match connection["protocol"]:
        case "anthropic":
            headers["anthropic-version"] = "2023-06-01"
            if key:
                headers["x-api-key"] = key
        case "gemini":
            if key:
                headers["x-goog-api-key"] = key
        case "azure_openai":
            if key:
                headers["api-key"] = key
        case _:
            if key:
                headers["Authorization"] = f"Bearer {key}"
    return headers


def _request(
    connection: dict, method: str, suffix: str, payload: dict | None = None, timeout: float = 20
) -> dict:
    base = validate_base_url(connection["base_url"], legacy_env=connection.get("legacy_env", False))
    _check_destination(base)
    try:
        with httpx.Client(
            timeout=httpx.Timeout(max(2, min(timeout, 45)), connect=6),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            with client.stream(
                method,
                base + suffix,
                headers=_headers(connection),
                json=payload,
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    raise ProviderCallError("unsafe_endpoint")
                if response.status_code >= 400:
                    code = (
                        "unauthorized"
                        if response.status_code in (401, 403)
                        else "not_found"
                        if response.status_code == 404
                        else "rate_limited"
                        if response.status_code == 429
                        else "upstream_rejected"
                        if response.status_code < 500
                        else "upstream_unavailable"
                    )
                    raise ProviderCallError(code, response.status_code)
                chunks = bytearray()
                for part in response.iter_bytes():
                    chunks.extend(part)
                    if len(chunks) > MAX_RESPONSE_BYTES:
                        raise ProviderCallError("invalid_response")
        data = json.loads(chunks)
        if not isinstance(data, dict):
            raise ValueError("Not a JSON object")
        return data
    except (httpx.HTTPError, TimeoutError) as exc:
        raise ProviderCallError("network") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProviderCallError("invalid_response") from exc


def discover_models(connection: dict) -> dict:
    """Always query the provider. No fabricated/stale catalog is presented as live."""
    if connection["protocol"] == "azure_openai":
        return {
            "models": [],
            "source": "manual",
            "notice": "Azure 使用 Deployment Name。此连接方式不能枚举资源部署，请在模型 ID 中手动填写。",
        }
    entries: list[dict] = []
    token = ""
    for _ in range(5):
        if connection["protocol"] == "anthropic":
            suffix = "/models?limit=100" + (f"&after_id={quote(token)}" if token else "")
        elif connection["protocol"] == "gemini":
            suffix = "/models?pageSize=100" + (f"&pageToken={quote(token)}" if token else "")
        else:
            suffix = "/models"
        try:
            payload = _request(connection, "GET", suffix, timeout=15)
        except ProviderCallError as exc:
            if exc.code in ("not_found", "upstream_rejected"):
                return {
                    "models": [],
                    "source": "manual",
                    "notice": "供应商未提供兼容的模型列表接口；可手动填写模型 ID 并进行推理测试。",
                }
            raise
        if connection["protocol"] == "gemini":
            data = payload.get("models", [])
            for model in data if isinstance(data, list) else []:
                if not isinstance(model, dict):
                    continue
                supported = model.get("supportedGenerationMethods", [])
                if "generateContent" in supported:
                    name = str(model.get("name", ""))
                    if name.startswith("models/"):
                        name = name[7:]
                    if name:
                        entries.append({"id": name, "name": model.get("displayName") or name})
            token = payload.get("nextPageToken") or ""
        else:
            data = payload.get("data", [])
            for model in data if isinstance(data, list) else []:
                if isinstance(model, dict) and isinstance(model.get("id"), str):
                    name = model["id"]
                    entries.append({"id": name, "name": model.get("display_name") or name})
            token = payload.get("last_id") if payload.get("has_more") else ""
        if (
            not token
            or len(entries) >= 400
            or connection["protocol"] not in ("anthropic", "gemini")
        ):
            break
    unique = list({model["id"]: model for model in entries}.values())[:400]
    return {
        "models": unique,
        "source": "provider_api",
        "notice": "模型列表由供应商接口返回；列出不等于拥有该模型的推理权限，请使用连接测试确认。",
    }


def _anthropic_messages(messages: list[dict], tools: list[dict] | None) -> tuple[str, list, list]:
    system_parts = []
    turns: list[dict] = []
    tool_names: dict[str, str] = {}
    converted_tools = []
    for tool in tools or []:
        func = tool["function"]
        converted_tools.append(
            {
                "name": func["name"],
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    for message in messages:
        role = message.get("role")
        if role == "system":
            system_parts.append(str(message.get("content") or ""))
        elif role == "tool":
            turns.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.get("tool_call_id"),
                            "content": str(message.get("content") or ""),
                        }
                    ],
                }
            )
        elif role == "assistant":
            blocks = []
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            for call in message.get("tool_calls") or []:
                func = call["function"]
                tool_names[call["id"]] = func["name"]
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": func["name"],
                        "input": json.loads(func.get("arguments") or "{}"),
                    }
                )
            if blocks:
                turns.append({"role": "assistant", "content": blocks})
        elif role == "user":
            turns.append({"role": "user", "content": str(message.get("content") or "")})
    return "\n".join(system_parts), turns, converted_tools


def _gemini_messages(messages: list[dict], tools: list[dict] | None) -> tuple[str, list, list]:
    system = []
    turns = []
    tool_names: dict[str, str] = {}
    converted_tools = []
    for tool in tools or []:
        func = tool["function"]
        converted_tools.append(
            {
                "name": func["name"],
                "description": func.get("description", ""),
                "parameters": func.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    for message in messages:
        role = message.get("role")
        if role == "system":
            system.append(str(message.get("content") or ""))
        elif role == "user":
            turns.append({"role": "user", "parts": [{"text": str(message.get("content") or "")}]})
        elif role == "assistant":
            parts = []
            if message.get("content"):
                parts.append({"text": str(message["content"])})
            for call in message.get("tool_calls") or []:
                func = call["function"]
                tool_names[call["id"]] = func["name"]
                parts.append(
                    {
                        "functionCall": {
                            "name": func["name"],
                            "args": json.loads(func.get("arguments") or "{}"),
                        }
                    }
                )
            if parts:
                turns.append({"role": "model", "parts": parts})
        elif role == "tool":
            tool_id = message.get("tool_call_id")
            name = tool_names.get(tool_id, "")
            if name:
                turns.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": name,
                                    "response": {"result": str(message.get("content") or "")},
                                }
                            }
                        ],
                    }
                )
    return "\n".join(system), turns, converted_tools


def chat_completion(
    connection: dict,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    json_mode: bool = False,
    timeout: float = 40,
    benchmark: bool = False,
) -> tuple[dict, int | None, int | None]:
    model = connection["model_id"]
    if not model:
        raise ProviderCallError("upstream_rejected")
    if connection["protocol"] == "anthropic":
        system, turns, registered_tools = _anthropic_messages(messages, tools)
        payload = {"model": model, "max_tokens": 160 if benchmark else 2048, "messages": turns}
        if system:
            payload["system"] = system
        if registered_tools:
            payload["tools"] = registered_tools
        result = _request(connection, "POST", "/messages", payload, timeout=timeout)
        parts, calls = [], []
        for block in result.get("content") or []:
            if block.get("type") == "text":
                parts.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                calls.append(
                    {
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    }
                )
        message = {"role": "assistant", "content": "\n".join(parts), "tool_calls": calls}
        usage = result.get("usage") or {}
        return message, usage.get("input_tokens"), usage.get("output_tokens")
    if connection["protocol"] == "gemini":
        system, turns, registered_tools = _gemini_messages(messages, tools)
        payload = {"contents": turns, "generationConfig": {}}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if registered_tools:
            payload["tools"] = [{"functionDeclarations": registered_tools}]
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        if benchmark:
            payload["generationConfig"]["maxOutputTokens"] = 160
        result = _request(
            connection,
            "POST",
            "/models/" + quote(model.removeprefix("models/"), safe="-._") + ":generateContent",
            payload,
            timeout=timeout,
        )
        parts, calls = [], []
        candidates = result.get("candidates") or []
        if candidates:
            for part in candidates[0].get("content", {}).get("parts", []):
                if "text" in part:
                    parts.append(part["text"])
                if "functionCall" in part:
                    function = part["functionCall"]
                    calls.append(
                        {
                            "id": "gemini-" + uuid.uuid4().hex[:20],
                            "type": "function",
                            "function": {
                                "name": function["name"],
                                "arguments": json.dumps(
                                    function.get("args", {}), ensure_ascii=False
                                ),
                            },
                        }
                    )
        usage = result.get("usageMetadata") or {}
        return (
            {"role": "assistant", "content": "\n".join(parts), "tool_calls": calls},
            usage.get("promptTokenCount"),
            usage.get("candidatesTokenCount"),
        )

    payload = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if benchmark:
        payload[
            "max_completion_tokens"
            if connection["provider"] in ("openai", "azure")
            else "max_tokens"
        ] = 160
    response = _request(connection, "POST", "/chat/completions", payload, timeout=timeout)
    try:
        message = response["choices"][0]["message"]
        usage = response.get("usage") or {}
        if not isinstance(message, dict):
            raise ValueError("Invalid assistant")
        return message, usage.get("prompt_tokens"), usage.get("completion_tokens")
    except (IndexError, KeyError, ValueError, TypeError) as exc:
        raise ProviderCallError("invalid_response") from exc


def benchmark_connection(connection: dict, samples: int) -> dict:
    """Rate is effective completed tokens / full wall clock, NOT decode-only TPS/TTFT."""
    measurements = []
    for _ in range(samples):
        started = time.perf_counter()
        message, prompt_tokens, completion_tokens = chat_completion(
            connection,
            [{"role": "user", "content": "请用中文给出三条简短的提高资料核查质量的方法。"}],
            timeout=40,
            benchmark=True,
        )
        elapsed = max(time.perf_counter() - started, 0.001)
        if not (message.get("content") or message.get("tool_calls")):
            raise ProviderCallError("invalid_response")
        measurements.append(
            {
                "duration_ms": round(elapsed * 1000),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }
        )
    total_s = sum(m["duration_ms"] for m in measurements) / 1000
    token_counts = [m["completion_tokens"] for m in measurements]
    has_usage = all(isinstance(v, int) and v >= 0 for v in token_counts)
    return {
        "success": True,
        "samples": samples,
        "latency_ms": round(sum(m["duration_ms"] for m in measurements) / samples),
        "min_latency_ms": min(m["duration_ms"] for m in measurements),
        "max_latency_ms": max(m["duration_ms"] for m in measurements),
        "output_tokens": sum(token_counts) if has_usage else None,
        "effective_tokens_per_second": (
            round(sum(token_counts) / total_s, 2) if has_usage and total_s else None
        ),
        "usage_reported": has_usage,
        "metric_note": "有效输出速率 = API 返回的输出 Token 数 / 完整请求耗时；非首字延迟或纯解码 TPS。",
    }
