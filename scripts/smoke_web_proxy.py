"""End-to-end HTTP proxy check of Next.js -> FastAPI local cookie auth.

This test launches localhost-only API and Next DEV servers, with a temporary
database. It does not use real models, shared production data, or a browser.
Run: services/api/.venv/Scripts/python.exe scripts/smoke_web_proxy.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "services" / "api"
WEB = ROOT / "apps" / "web"


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_http(url: str, seconds: float = 55) -> httpx.Response:
    deadline = time.monotonic() + seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=4)
            if response.status_code == 200:
                return response
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(0.45)
    raise RuntimeError(f"Service not ready at {url}: {last_error}")


def stop(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=7)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> None:
    port = unused_port()
    web_port = 3000
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", web_port)) == 0:
            raise RuntimeError("Port 3000 is in use. Do not disturb an existing local server.")
    with tempfile.TemporaryDirectory() as temporary:
        env = os.environ.copy()
        env.update({
            "AGENT_STUDIO_DB_PATH": str(Path(temporary) / "proxy.sqlite3"),
            "APP_ENV": "development",
        })
        env.pop("MODEL_API_KEY", None)
        web_env = env.copy()
        web_env["STUDIO_LOCAL_API_PORT"] = str(port)
        web_env["NEXT_TELEMETRY_DISABLED"] = "1"
        api_log = open(Path(temporary) / "api.log", "wb")
        web_log = open(Path(temporary) / "next.log", "wb")
        api = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "app.main:app",
                "--host", "127.0.0.1", "--port", str(port),
            ],
            cwd=str(API), env=env, stdout=api_log, stderr=subprocess.STDOUT,
        )
        next_server = None
        try:
            wait_http(f"http://127.0.0.1:{port}/v1/health")
            next_server = subprocess.Popen(
                [
                    "node", "node_modules/next/dist/bin/next", "dev",
                    "--hostname", "127.0.0.1", "--port", str(web_port),
                ],
                cwd=str(WEB), env=web_env, stdout=web_log, stderr=subprocess.STDOUT,
            )
            home = wait_http("http://127.0.0.1:3000/")
            assert "Agent Studio" in home.text
            with httpx.Client(
                base_url="http://127.0.0.1:3000", timeout=15,
                headers={"Origin": "http://127.0.0.1:3000"},
            ) as client:
                public = client.get("/backend/v1/agents")
                assert public.status_code == 401, public.text
                registered = client.post(
                    "/backend/v1/auth/register",
                    json={
                        "email": "webproxy@example.test",
                        "password": "proxy-test-password-123",
                    },
                )
                assert registered.status_code == 201, registered.text
                assert "httponly" in registered.headers.get("set-cookie", "").lower()
                assert client.cookies.get("studio_session")
                assert client.get("/backend/v1/auth/me").status_code == 200

                workspace = registered.json()["workspaces"][0]["id"]
                scope = {"X-Workspace-ID": workspace}
                created = client.post(
                    "/backend/v1/agents",
                    json={
                        "name": "代理测试 Agent",
                        "goal": {"task": "验证登录后配置保存不跨空间泄露"},
                    },
                    headers=scope,
                )
                assert created.status_code == 201, created.text
                agents = client.get("/backend/v1/agents", headers=scope)
                assert len(agents.json()) == 1, agents.text
                logout = client.post("/backend/v1/auth/logout")
                assert logout.status_code == 200
                assert client.get("/backend/v1/agents", headers=scope).status_code == 401
            print("PASS Next.js proxy -> local FastAPI | HttpOnly cookie | Workspace header")
            print("PASS Auth register/me/logout | Unauthenticated 401 | Agent create/read 201/200")
            print("NOTE HTTP integration, not browser click E2E or production security testing.")
        finally:
            if next_server:
                stop(next_server)
            stop(api)
            api_log.close()
            web_log.close()


if __name__ == "__main__":
    main()
