"""Deterministic local checks; fake model is used in tests only, never at runtime."""
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.schema import AgentSpec, Goal


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.prev_db = os.environ.get("AGENT_STUDIO_DB_PATH")
        self.prev_key = os.environ.pop("MODEL_API_KEY", None)
        os.environ["AGENT_STUDIO_DB_PATH"] = os.path.join(self.folder.name, "test.db")
        self.client_ctx = TestClient(app)
        self.client = self.client_ctx.__enter__()

    def tearDown(self):
        self.client_ctx.__exit__(None, None, None)
        if self.prev_db is None:
            os.environ.pop("AGENT_STUDIO_DB_PATH", None)
        else:
            os.environ["AGENT_STUDIO_DB_PATH"] = self.prev_db
        if self.prev_key is not None:
            os.environ["MODEL_API_KEY"] = self.prev_key
        self.folder.cleanup()

    def example_spec(self):
        return AgentSpec(name="示例研究助手", goal=Goal(task="回答用户问题并根据需要执行实际工具")).model_dump()

    def test_health_and_tool_catalog(self):
        result = self.client.get("/v1/health")
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["authentication_available"])
        tools = self.client.get("/v1/tools").json()
        self.assertEqual({x["id"] for x in tools}, {"utc_now", "add_numbers"})

    def test_agent_create_list_and_schema_rejects_tool_privilege(self):
        created = self.client.post("/v1/agents", json=self.example_spec())
        self.assertEqual(created.status_code, 201)
        self.assertEqual(len(self.client.get("/v1/agents").json()), 1)
        invalid = self.example_spec()
        invalid["permissions"]["allowed_tools"] = []
        self.assertEqual(self.client.post("/v1/agents", json=invalid).status_code, 422)

    def test_generate_draft_missing_real_model_key_is_explicit(self):
        result = self.client.post("/v1/agents/drafts/generate", json={"description": "建立市场调研助手"})
        self.assertEqual(result.status_code, 503)
        self.assertIn("MODEL_API_KEY", result.json()["detail"])

    def test_run_without_key_records_failure_not_faked_success(self):
        created = self.client.post("/v1/agents", json=self.example_spec()).json()
        run = self.client.post(f"/v1/agents/{created['id']}/runs", json={"input": "获取当前时间"})
        self.assertEqual(run.status_code, 202)
        finished = self.client.get(f"/v1/runs/{run.json()['id']}").json()
        self.assertEqual(finished["status"], "FAILED")
        self.assertIn("MODEL_API_KEY", finished["error"])
        events = self.client.get(f"/v1/runs/{finished['id']}/events").json()
        self.assertTrue(any(e["type"] == "failed" for e in events))

    def test_tool_called_and_traced_with_mocked_provider(self):
        created = self.client.post("/v1/agents", json=self.example_spec()).json()
        first = {"content": None, "tool_calls": [
            {"id": "call-test-1", "type": "function",
             "function": {"name": "add_numbers", "arguments": '{"a": 3, "b": 5}'}}
        ]}
        second = {"content": "计算结果是 8"}
        with patch("app.runner.ModelGateway") as gateway:
            gateway.return_value.chat.side_effect = [first, second]
            run = self.client.post(f"/v1/agents/{created['id']}/runs", json={"input": "计算3+5"})
        finished = self.client.get(f"/v1/runs/{run.json()['id']}").json()
        self.assertEqual(finished["status"], "SUCCEEDED")
        self.assertIn("8", finished["output"])
        events = self.client.get(f"/v1/runs/{finished['id']}/events").json()
        self.assertEqual([e["type"] for e in events].count("tool_completed"), 1)

    def test_model_tool_count_limit_is_enforced(self):
        spec = self.example_spec()
        spec["stop_conditions"]["max_tool_calls"] = 1
        created = self.client.post("/v1/agents", json=spec).json()
        request_calls = {"content": None, "tool_calls": [
            {"id": "id-1", "type": "function", "function": {"name": "add_numbers", "arguments": '{"a": 1, "b": 2}'}},
            {"id": "id-2", "type": "function", "function": {"name": "add_numbers", "arguments": '{"a": 3, "b": 4}'}},
        ]}
        with patch("app.runner.ModelGateway") as gateway:
            gateway.return_value.chat.return_value = request_calls
            response = self.client.post(f"/v1/agents/{created['id']}/runs", json={"input": "执行两个计算"})
        run = self.client.get(f"/v1/runs/{response.json()['id']}").json()
        self.assertEqual(run["status"], "PARTIAL")
        events = self.client.get(f"/v1/runs/{run['id']}/events").json()
        self.assertEqual([e["type"] for e in events].count("tool_completed"), 1)
        self.assertTrue(any(e["payload"].get("reason") == "max_tool_calls" for e in events))

    def test_unregistered_tool_blocked_in_server(self):
        from app.tools import execute_tool
        with self.assertRaises(PermissionError):
            execute_tool("unknown", {}, {"utc_now"})
        with self.assertRaises(PermissionError):
            execute_tool("add_numbers", {"a": 1, "b": 2}, {"utc_now"})

    def test_production_mode_refuses_startup(self):
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            with self.assertRaises(RuntimeError):
                with TestClient(app):
                    pass

    def test_model_generated_draft_is_validated(self):
        with patch("app.llm.ModelGateway") as gateway:
            gateway.return_value.chat.return_value = {
                "content": '{"name":"市场研究 Agent","description":"辅助研究","task":"对指定主题开展资料研究并输出结论","success_criteria":["有结论","说明不确定部分"]}'
            }
            response = self.client.post("/v1/agents/drafts/generate", json={"description": "创建一个市场研究 Agent"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["spec"]["name"], "市场研究 Agent")


if __name__ == "__main__":
    unittest.main()
