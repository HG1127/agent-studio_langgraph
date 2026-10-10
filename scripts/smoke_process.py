"""Full subprocess smoke: API -> SQLite queue -> Worker -> LangGraph -> read-only tool.

The OpenAI-compatible LLM server below is a TEST STUB, not a real model.
This only verifies adapter wire protocol, process separation and persistence.
Run: cd services/api; .venv/Scripts/python.exe ../../scripts/smoke_process.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import httpx

API_DIR = Path(__file__).resolve().parents[1] / "services" / "api"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class StubProvider(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        request = json.loads(raw)
        has_tool_result = any(m.get("role") == "tool" for m in request.get("messages", []))
        if has_tool_result:
            message = {"role": "assistant", "content": "测试模型模拟响应：3 + 5 = 8"}
        else:
            message = {
                "role": "assistant", "content": None, "tool_calls": [
                    {"id": "smoke-call", "type": "function",
                     "function": {"name": "add_numbers", "arguments": '{"a":3,"b":5}'}}
                ],
            }
        content = json.dumps({"choices": [{"index": 0, "message": message}]}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        return


def wait_api(url: str, timeout: float = 25) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url + "/v1/health", timeout=2)
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError:
            pass
        time.sleep(0.35)
    raise RuntimeError("FastAPI process did not start")


def main():
    local_api_port = free_port()
    local_llm_port = free_port()
    with tempfile.TemporaryDirectory() as directory:
        env = os.environ.copy()
        env.update({
            "AGENT_STUDIO_DB_PATH": str(Path(directory) / "smoke.sqlite3"),
            "MODEL_BASE_URL": f"http://127.0.0.1:{local_llm_port}/v1",
            "MODEL_NAME": "stub-integration-only",
            "MODEL_API_KEY": "nonsecret-test-token",
            "APP_ENV": "development",
            "LANGGRAPH_STRICT_MSGPACK": "true",
        })
        server = ThreadingHTTPServer(("127.0.0.1", local_llm_port), StubProvider)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        api_process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(local_api_port)],
            cwd=API_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        try:
            base = f"http://127.0.0.1:{local_api_port}"
            status = wait_api(base)
            assert status["checkpointing"] == "langgraph_sqlite_development"
            assert status["model_key_configured"]
            agent = httpx.post(
                base + "/v1/agents",
                json={
                    "name": "HTTP 单 Agent 测试",
                    "goal": {"task": "调用真实本地只读计算工具并给出结果"},
                },
                timeout=5,
            )
            assert agent.status_code == 201, agent.text
            run = httpx.post(
                base + "/v1/agents/" + agent.json()["id"] + "/runs",
                json={"input": "请计算 3 + 5"}, timeout=5,
            )
            assert run.status_code == 202, run.text
            run_id = run.json()["id"]
            assert run.json()["status"] == "QUEUED"
            # Worker runs in its own subprocess, opening and closing checkpoint storage.
            worker = subprocess.run(
                [sys.executable, "-m", "app.worker", "--once"],
                cwd=API_DIR, env=env, timeout=45, capture_output=True, text=True,
            )
            assert worker.returncode == 0, worker.stderr[-1600:]
            finished = httpx.get(base + "/v1/runs/" + run_id, timeout=5).json()
            events = httpx.get(base + "/v1/runs/" + run_id + "/events", timeout=5).json()
            assert finished["status"] == "SUCCEEDED", finished
            assert "3 + 5 = 8" in finished["output"]
            assert any(e["type"] == "tool_completed" and e["payload"]["result"]["result"] == 8
                       for e in events), events
            assert (Path(directory) / "smoke.checkpoints.sqlite3").exists()
            checkpoint = httpx.get(base + "/v1/runs/" + run_id + "/checkpoint", timeout=5).json()
            assert checkpoint["available"] and checkpoint["tool_calls"] == 1, checkpoint
            assert httpx.get(base + "/v1/health").json()["worker_online"]
            print("PASS API 200 | Agent 201 | queued Run 202 | Worker subprocess -> SUCCEEDED")
            print("PASS server-authorized read-only tool | LangGraph SQLite checkpoint | Run events")
            print("NOTE provider is a local HTTP stub; this is not a real LLM or production test.")
        finally:
            api_process.terminate()
            try:
                api_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                api_process.kill()
                api_process.wait(timeout=5)
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
