"""Deterministic API/StateGraph tests. Models are mocked ONLY inside tests.

No tests pretend to be evidence of live model execution.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from app import db
from app.main import app
from app.runner import process_claimed
from app.schema import AgentSpec, Goal
from fastapi.testclient import TestClient


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.env_before = {
            k: os.environ.get(k)
            for k in (
                "AGENT_STUDIO_DB_PATH",
                "AGENT_STUDIO_CHECKPOINT_PATH",
                "MODEL_API_KEY",
                "APP_ENV",
            )
        }
        os.environ["AGENT_STUDIO_DB_PATH"] = os.path.join(self.folder.name, "test.sqlite3")
        for key in ("AGENT_STUDIO_CHECKPOINT_PATH", "MODEL_API_KEY", "APP_ENV"):
            os.environ.pop(key, None)
        self.client_ctx = TestClient(app)
        self.client = self.client_ctx.__enter__()

    def tearDown(self):
        self.client_ctx.__exit__(None, None, None)
        for key, before in self.env_before.items():
            if before is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = before
        self.folder.cleanup()

    def example_spec(self, **stops):
        spec = AgentSpec(
            name="本地测试助手", goal=Goal(task="回答问题，必要时调用只读计算工具")
        ).model_dump()
        spec["stop_conditions"].update(stops)
        return spec

    def agent_id(self, spec=None):
        response = self.client.post("/v1/agents", json=spec or self.example_spec())
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def queue(self, text="计算 3 + 5", spec=None):
        agent_id = self.agent_id(spec)
        result = self.client.post(f"/v1/agents/{agent_id}/runs", json={"input": text})
        self.assertEqual(result.status_code, 202)
        self.assertEqual(result.json()["status"], "QUEUED")
        return result.json()["id"]

    def claim(self, run_id):
        self.assertEqual(db.claim_next("test-worker"), run_id)

    def test_health_and_tools_catalog(self):
        health = self.client.get("/v1/health")
        self.assertEqual(health.status_code, 200)
        self.assertFalse(health.json()["authentication_available"])
        self.assertEqual(health.json()["checkpointing"], "langgraph_sqlite_development")
        self.assertFalse(health.json()["worker_online"])
        self.assertEqual(
            {r["id"] for r in self.client.get("/v1/tools").json()},
            {"utc_now", "add_numbers"},
        )

    def test_save_list_agent_and_strict_tool_validation(self):
        self.agent_id()
        self.assertEqual(len(self.client.get("/v1/agents").json()), 1)
        spec = self.example_spec()
        spec["permissions"]["allowed_tools"] = []
        self.assertEqual(self.client.post("/v1/agents", json=spec).status_code, 422)

    def test_generated_draft_missing_model_key_is_explicit(self):
        response = self.client.post(
            "/v1/agents/drafts/generate", json={"description": "建立市场研究助手"}
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("MODEL_API_KEY", response.json()["detail"])

    def test_model_generated_draft_validated(self):
        with patch("app.llm.ModelGateway") as gateway:
            gateway.return_value.chat.return_value = {
                "content": '{"name":"市场研究 Agent","description":"辅助研究",'
                '"task":"对指定主题开展资料研究并输出结论",'
                '"success_criteria":["有结论","说明不确定部分"]}'
            }
            response = self.client.post(
                "/v1/agents/drafts/generate", json={"description": "创建一个市场研究 Agent"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["spec"]["name"], "市场研究 Agent")

    def test_run_waits_for_worker_and_no_key_fails_not_faked(self):
        run_id = self.queue("当前时间是多少")
        # API does not execute inside the HTTP request.
        self.assertEqual(self.client.get(f"/v1/runs/{run_id}").json()["status"], "QUEUED")
        self.assertEqual(db.claim_next("test"), run_id)
        run = process_claimed(run_id)
        self.assertEqual(run["status"], "FAILED")
        self.assertIn("MODEL_API_KEY", run["error"])
        events = self.client.get(f"/v1/runs/{run_id}/events").json()
        self.assertTrue(any(e["type"] == "failed" for e in events))
        self.assertTrue(db.checkpoint_path().exists())

    def test_stategraph_tools_and_checkpoint_persist(self):
        run_id = self.queue()
        self.claim(run_id)
        answers = [
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "calc1",
                        "type": "function",
                        "function": {"name": "add_numbers", "arguments": '{"a":3,"b":5}'},
                    }
                ],
            },
            {"content": "真实工具返回 8"},
        ]
        with patch("app.harness.graph.ModelGateway") as gateway:
            gateway.return_value.chat.side_effect = answers
            run = process_claimed(run_id)
        self.assertEqual(run["status"], "SUCCEEDED")
        self.assertEqual(run["output"], "真实工具返回 8")
        events = db.get_events(run_id)
        self.assertEqual(sum(e["type"] == "tool_completed" for e in events), 1)
        self.assertEqual(sum(e["type"] == "plan" for e in events), 1)
        # Reopen checkpointer in a second context: persisted across process boundaries.
        from langgraph.checkpoint.sqlite import SqliteSaver

        with SqliteSaver.from_conn_string(str(db.checkpoint_path())) as saver:
            saved = saver.get({"configurable": {"thread_id": run_id}})
        self.assertIsNotNone(saved)
        self.assertEqual(saved["channel_values"]["final_status"], "SUCCEEDED")
        summary = self.client.get(f"/v1/runs/{run_id}/checkpoint")
        self.assertEqual(summary.status_code, 200)
        self.assertTrue(summary.json()["available"])
        self.assertEqual(summary.json()["model_rounds"], 2)
        self.assertEqual(summary.json()["tool_calls"], 1)
        self.assertEqual(summary.json()["next_nodes"], [])

    def test_tool_count_limit_keeps_partial_result(self):
        run_id = self.queue(spec=self.example_spec(max_tool_calls=1))
        self.claim(run_id)
        tool_requests = {
            "content": None,
            "tool_calls": [
                {
                    "id": "one",
                    "type": "function",
                    "function": {"name": "add_numbers", "arguments": '{"a":1,"b":2}'},
                },
                {
                    "id": "two",
                    "type": "function",
                    "function": {"name": "add_numbers", "arguments": '{"a":4,"b":5}'},
                },
            ],
        }
        with patch("app.harness.graph.ModelGateway") as gateway:
            gateway.return_value.chat.return_value = tool_requests
            run = process_claimed(run_id)
        self.assertEqual(run["status"], "PARTIAL")
        self.assertEqual(run["error"], "max_tool_calls")
        self.assertEqual(sum(e["type"] == "tool_completed" for e in db.get_events(run_id)), 1)

    def test_limit_model_rounds(self):
        run_id = self.queue(spec=self.example_spec(max_model_rounds=1))
        self.claim(run_id)
        tool_request = {
            "content": None,
            "tool_calls": [
                {
                    "id": "one",
                    "type": "function",
                    "function": {"name": "utc_now", "arguments": "{}"},
                }
            ],
        }
        with patch("app.harness.graph.ModelGateway") as gateway:
            gateway.return_value.chat.return_value = tool_request
            run = process_claimed(run_id)
        self.assertEqual(run["status"], "PARTIAL")
        self.assertEqual(run["error"], "max_model_rounds")

    def test_unknown_and_unauthorized_tools_fail_closed(self):
        from app.tools import execute_tool

        with self.assertRaises(PermissionError):
            execute_tool("unknown", {}, {"utc_now"})
        with self.assertRaises(PermissionError):
            execute_tool("add_numbers", {"a": 1, "b": 2}, {"utc_now"})

    def test_queued_pause_resume_and_cancel(self):
        run_id = self.queue()
        paused = self.client.post(f"/v1/runs/{run_id}/pause")
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()["status"], "PAUSED")
        self.assertIsNone(db.claim_next("unit"))
        resumed = self.client.post(f"/v1/runs/{run_id}/resume")
        self.assertEqual(resumed.json()["status"], "QUEUED")
        canceled = self.client.post(f"/v1/runs/{run_id}/cancel")
        self.assertEqual(canceled.json()["status"], "CANCELED")
        self.assertIsNone(db.claim_next("unit"))
        self.assertEqual(self.client.post(f"/v1/runs/{run_id}/resume").status_code, 409)

    def test_running_pause_request_acknowledged(self):
        run_id = self.queue()
        self.claim(run_id)
        response = self.client.post(f"/v1/runs/{run_id}/pause")
        self.assertEqual(response.json()["status"], "PAUSE_REQUESTED")
        result = process_claimed(run_id)
        self.assertEqual(result["status"], "PAUSED")

    def test_running_cancel_request_acknowledged(self):
        run_id = self.queue()
        self.claim(run_id)
        response = self.client.post(f"/v1/runs/{run_id}/cancel")
        self.assertEqual(response.json()["status"], "CANCEL_REQUESTED")
        self.assertEqual(process_claimed(run_id)["status"], "CANCELED")

    def test_explicit_retry_after_no_key(self):
        run_id = self.queue()
        self.claim(run_id)
        self.assertEqual(process_claimed(run_id)["status"], "FAILED")
        self.assertEqual(self.client.post(f"/v1/runs/{run_id}/retry").json()["status"], "QUEUED")
        self.claim(run_id)
        with patch("app.harness.graph.ModelGateway") as gateway:
            gateway.return_value.chat.return_value = {"content": "重试后恢复完成"}
            result = process_claimed(run_id)
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertIn("重试后恢复完成", result["output"])
        self.assertTrue(any(e["type"] == "checkpoint_resumed" for e in db.get_events(run_id)))

    def test_worker_crash_restarts_from_checkpoint_not_first_node(self):
        run_id = self.queue()
        self.claim(run_id)
        tool = {
            "content": None,
            "tool_calls": [
                {
                    "id": "call-crash",
                    "type": "function",
                    "function": {"name": "add_numbers", "arguments": '{"a":20,"b":22}'},
                }
            ],
        }
        with patch("app.harness.graph.ModelGateway") as gateway:
            gateway.return_value.chat.side_effect = [
                tool,
                KeyboardInterrupt("simulated_worker_death"),
            ]
            with self.assertRaises(KeyboardInterrupt):
                process_claimed(run_id)
        self.assertEqual(db.get_run(run_id)["status"], "RUNNING")
        self.assertEqual(sum(e["type"] == "tool_completed" for e in db.get_events(run_id)), 1)
        # Simulate lease expiry and a new worker process.
        with db.connect() as conn:
            conn.execute(
                "UPDATE runs SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",
                (run_id,),
            )
        self.assertEqual(db.claim_next("recovery-worker"), run_id)
        with patch("app.harness.graph.ModelGateway") as gateway:
            gateway.return_value.chat.return_value = {"content": "20 + 22 = 42"}
            result = process_claimed(run_id)
            self.assertEqual(gateway.return_value.chat.call_count, 1)
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(sum(e["type"] == "tool_completed" for e in db.get_events(run_id)), 1)
        self.assertTrue(any(e["type"] == "worker_recovered" for e in db.get_events(run_id)))

    def test_two_claims_are_not_allowed(self):
        run_id = self.queue()
        self.claim(run_id)
        self.assertIsNone(db.claim_next("second-worker"))

    def test_incremental_event_cursor(self):
        run_id = self.queue()
        db.append_event(run_id, "custom", {"test": True}, key="custom")
        db.append_event(run_id, "custom", {"test": True}, key="custom")
        events = self.client.get(f"/v1/runs/{run_id}/events").json()
        self.assertEqual(len(events), 2)
        tail = self.client.get(f"/v1/runs/{run_id}/events?after_seq=1").json()
        self.assertEqual(len(tail), 1)
        self.assertEqual(self.client.get(f"/v1/runs/{run_id}/events?after_seq=-1").status_code, 422)

    def test_production_mode_refuses_startup(self):
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            with self.assertRaises(RuntimeError):
                with TestClient(app):
                    pass

    def test_time_budget_before_model_blocks_execution(self):
        run_id = self.queue(spec=self.example_spec(max_duration_seconds=5))
        self.claim(run_id)
        with db.connect() as conn:
            conn.execute(
                "UPDATE runs SET started_at='2000-01-01T00:00:00+00:00' WHERE id=?", (run_id,)
            )
        with patch("app.harness.graph.ModelGateway") as gateway:
            result = process_claimed(run_id)
            gateway.assert_not_called()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["error"], "max_duration")

    def test_duplicate_tool_no_progress_limit(self):
        run_id = self.queue(spec=self.example_spec(max_no_progress_steps=1))
        self.claim(run_id)
        repeated = {
            "content": None,
            "tool_calls": [
                {
                    "id": "repeated",
                    "type": "function",
                    "function": {"name": "add_numbers", "arguments": '{"a":1,"b":2}'},
                }
            ],
        }
        with patch("app.harness.graph.ModelGateway") as gateway:
            gateway.return_value.chat.return_value = repeated
            result = process_claimed(run_id)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["error"], "no_progress")
        self.assertEqual(sum(e["type"] == "tool_completed" for e in db.get_events(run_id)), 1)

    def test_invalid_tool_id_must_be_rejected_before_execution(self):
        run_id = self.queue()
        self.claim(run_id)
        invalid = {
            "content": None,
            "tool_calls": [
                {
                    "type": "function",
                    "function": {"name": "add_numbers", "arguments": '{"a":7,"b":8}'},
                }
            ],
        }
        with (
            patch("app.harness.graph.ModelGateway") as gateway,
            patch("app.harness.graph.execute_tool") as tool,
        ):
            gateway.return_value.chat.return_value = invalid
            result = process_claimed(run_id)
            tool.assert_not_called()
        self.assertEqual(result["status"], "FAILED")

    def test_worker_heartbeat_online_flag(self):
        db.register_worker("test-worker")
        health = self.client.get("/v1/health").json()
        self.assertTrue(health["worker_online"])
        self.assertFalse(health["production_safe"])

    def test_remote_client_rejected_in_development(self):
        # A remote client's peer address cannot access an unauthenticated API.
        from app.main import local_clients_only
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/v1/health",
            "headers": [],
            "client": ("198.51.100.9", 54321),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
            "http_version": "1.1",
        }
        request = Request(scope)

        async def call_next(_):
            raise AssertionError("Remote request bypassed the loopback filter")

        import asyncio

        response = asyncio.run(local_clients_only(request, call_next))
        self.assertEqual(response.status_code, 403)

    def test_pause_after_tool_step_and_resume_from_checkpoint(self):
        run_id = self.queue()
        self.claim(run_id)
        first = {
            "content": None,
            "tool_calls": [
                {
                    "id": "pause-tool",
                    "type": "function",
                    "function": {"name": "add_numbers", "arguments": '{"a":5,"b":6}'},
                }
            ],
        }

        def pause_while_model_is_running(*args, **kwargs):
            db.request_pause(run_id)
            return {"content": "此回复应被暂停并重新请求"}

        call_number = 0

        def model_answers(*args, **kwargs):
            nonlocal call_number
            call_number += 1
            return first if call_number == 1 else pause_while_model_is_running()

        with patch("app.harness.graph.ModelGateway") as gateway:
            gateway.return_value.chat.side_effect = model_answers
            result = process_claimed(run_id)
        self.assertEqual(result["status"], "PAUSED")
        self.assertEqual(sum(e["type"] == "tool_completed" for e in db.get_events(run_id)), 1)
        self.assertEqual(self.client.post(f"/v1/runs/{run_id}/resume").json()["status"], "QUEUED")
        self.claim(run_id)
        with patch("app.harness.graph.ModelGateway") as gateway:
            gateway.return_value.chat.return_value = {"content": "恢复之后的最终回答"}
            result = process_claimed(run_id)
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(sum(e["type"] == "tool_completed" for e in db.get_events(run_id)), 1)


if __name__ == "__main__":
    unittest.main()
