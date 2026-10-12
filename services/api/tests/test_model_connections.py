"""Workspace model connections: auth, secret isolation, discovery and protocol checks.

Every external model call is mocked; these tests do not imply a live vendor key.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from app import db
from app.infrastructure.model_transport import (
    ProviderCallError,
    chat_completion,
    discover_models,
    validate_base_url,
)
from app.infrastructure.sqlite import model_connection_repository as storage
from app.llm import model_for_run
from app.main import app
from app.schema import AgentSpec, Goal
from fastapi.testclient import TestClient

PASSWORD = "model-testing-secret-pass-123"


class ModelConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {
            key: os.environ.get(key)
            for key in (
                "AGENT_STUDIO_DB_PATH",
                "STUDIO_MODEL_ENCRYPTION_KEY",
                "MODEL_API_KEY",
                "APP_ENV",
            )
        }
        os.environ["AGENT_STUDIO_DB_PATH"] = os.path.join(self.temp.name, "models.sqlite3")
        for key in ("STUDIO_MODEL_ENCRYPTION_KEY", "MODEL_API_KEY", "APP_ENV"):
            os.environ.pop(key, None)
        self.context_a = TestClient(app)
        self.context_b = TestClient(app)
        self.a = self.context_a.__enter__()
        self.b = self.context_b.__enter__()
        self.space_a = self._register(self.a, "models-alice@example.test")
        self.space_b = self._register(self.b, "models-bob@example.test")
        self.a.headers["X-Workspace-ID"] = self.space_a
        self.b.headers["X-Workspace-ID"] = self.space_b

    def tearDown(self):
        self.context_a.__exit__(None, None, None)
        self.context_b.__exit__(None, None, None)
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def _register(self, client, email):
        response = client.post(
            "/v1/auth/register",
            json={"email": email, "password": PASSWORD},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["workspaces"][0]["id"]

    def _create(self, client=None, **overrides):
        data = {
            "provider": "openai",
            "name": "Primary OpenAI",
            "protocol": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-development-secret-never-display",
            "model_id": "gpt-test",
            "is_default": False,
        }
        data.update(overrides)
        response = (client or self.a).post("/v1/models/connections", json=data)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_presets_auth_and_key_stored_encrypted(self):
        self.assertEqual(len(self.a.get("/v1/models/providers").json()), 22)
        unauthorized = TestClient(app)
        self.assertEqual(unauthorized.get("/v1/models/providers").status_code, 401)
        saved = self._create()
        self.assertTrue(saved["has_api_key"])
        self.assertTrue(saved["is_default"])
        self.assertNotIn("api_key", saved)
        self.assertNotIn("api_key_encrypted", saved)
        raw = self.a.get("/v1/models/connections")
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(len(raw.json()), 1)
        self.assertNotIn("sk-development-secret-never-display", raw.text)
        with db.connect() as connection:
            value = connection.execute(
                "SELECT api_key_encrypted FROM model_connections WHERE id=?", (saved["id"],)
            ).fetchone()[0]
        self.assertNotIn("sk-development-secret-never-display", value)
        loaded = storage.get_runtime_connection(saved["id"], self.space_a)
        self.assertEqual(loaded["api_key"], "sk-development-secret-never-display")
        db.initialize()
        self.assertEqual(self.a.get("/v1/models/connections").json()[0]["id"], saved["id"])

    def test_scope_and_manager_only(self):
        saved = self._create()
        self.assertEqual(self.b.get("/v1/models/connections").json(), [])
        self.assertEqual(
            self.b.patch(
                f"/v1/models/connections/{saved['id']}", json={"name": "stolen"}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.b.post(
                f"/v1/models/connections/{saved['id']}/test", json={"samples": 1}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.b.post(f"/v1/models/connections/{saved['id']}/discover").status_code, 404
        )
        self.assertEqual(self.b.delete(f"/v1/models/connections/{saved['id']}").status_code, 404)
        self.a.post(
            f"/v1/workspaces/{self.space_a}/members",
            json={"user_email": "models-bob@example.test", "role": "viewer"},
        )
        self.b.headers["X-Workspace-ID"] = self.space_a
        self.assertEqual(len(self.b.get("/v1/models/connections").json()), 1)
        self.assertEqual(self.b.post("/v1/models/connections", json={}).status_code, 403)
        self.assertEqual(
            self.b.patch(
                f"/v1/models/connections/{saved['id']}", json={"name": "wrong"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.b.post(f"/v1/models/connections/{saved['id']}/discover").status_code, 403
        )
        self.assertEqual(
            self.b.post(
                f"/v1/models/connections/{saved['id']}/test", json={"samples": 1}
            ).status_code,
            403,
        )
        self.a.post(
            f"/v1/workspaces/{self.space_a}/members",
            json={"user_email": "models-bob@example.test", "role": "member"},
        )
        self.assertEqual(self.b.post("/v1/models/connections", json={}).status_code, 403)
        self.a.post(
            f"/v1/workspaces/{self.space_a}/members",
            json={"user_email": "models-bob@example.test", "role": "admin"},
        )
        self.assertEqual(
            self.b.patch(
                f"/v1/models/connections/{saved['id']}", json={"name": "Team model"}
            ).status_code,
            200,
        )

    def test_default_selection_run_snapshot_and_delete(self):
        first = self._create()
        second = self._create(name="Secondary", model_id="other-model")
        self.assertFalse(second["is_default"])
        self.assertTrue(first["is_default"])
        changed = self.a.patch(
            f"/v1/models/connections/{second['id']}",
            json={"is_default": True},
        )
        self.assertEqual(changed.status_code, 200)
        self.assertTrue(changed.json()["is_default"])
        self.assertFalse(
            next(x for x in self.a.get("/v1/models/connections").json() if x["id"] == first["id"])[
                "is_default"
            ]
        )
        agent = self.a.post(
            "/v1/agents",
            json=AgentSpec(
                name="模型路由测试", goal=Goal(task="生成有关企业的事实核查报告")
            ).model_dump(),
        )
        self.assertEqual(agent.status_code, 201)
        queued = self.a.post(
            f"/v1/agents/{agent.json()['id']}/runs",
            json={"input": "测试保存时的模型绑定"},
        )
        self.assertEqual(queued.status_code, 202)
        run = queued.json()
        self.assertEqual(run["model_connection_id"], second["id"])
        self.assertEqual(run["model_name"], "other-model")
        self.a.patch(f"/v1/models/connections/{first['id']}", json={"is_default": True})
        runtime = model_for_run(run["id"])
        self.assertEqual(runtime["model_id"], "other-model")
        self.assertEqual(runtime["id"], second["id"])
        self.assertEqual(self.a.delete(f"/v1/models/connections/{second['id']}").status_code, 200)
        self.assertEqual(self.a.get("/v1/models/connections").json()[0]["id"], first["id"])

    def test_update_rotation_invalid_url_and_protocol_rules(self):
        saved = self._create()
        denied = self.a.patch(f"/v1/models/connections/{saved['id']}", json={"is_default": False})
        self.assertEqual(denied.status_code, 422)
        rotated = self.a.patch(
            f"/v1/models/connections/{saved['id']}",
            json={"api_key": "rotated-key-no-leak", "model_id": "gpt-new"},
        )
        self.assertEqual(rotated.status_code, 200)
        self.assertNotIn("rotated-key-no-leak", rotated.text)
        self.assertEqual(
            storage.get_runtime_connection(saved["id"], self.space_a)["api_key"],
            "rotated-key-no-leak",
        )
        forbidden_protocol = self.a.patch(
            f"/v1/models/connections/{saved['id']}",
            json={"protocol": "gemini"},
        )
        self.assertEqual(forbidden_protocol.status_code, 422)
        blocked = self.a.patch(
            f"/v1/models/connections/{saved['id']}",
            json={"base_url": "http://169.254.169.254/latest/meta-data"},
        )
        self.assertEqual(blocked.status_code, 422)
        no_key = self.a.post(
            "/v1/models/connections",
            json={
                "provider": "openai",
                "name": "no key",
                "protocol": "openai",
                "base_url": "https://api.openai.com/v1",
            },
        )
        self.assertEqual(no_key.status_code, 422)
        local = self.a.post(
            "/v1/models/connections",
            json={
                "provider": "custom",
                "name": "my localhost",
                "protocol": "openai",
                "base_url": "http://127.0.0.1:11434/v1",
                "model_id": "llama3.2",
            },
        )
        self.assertEqual(local.status_code, 201)
        self.assertFalse(local.json()["has_api_key"])

    def test_live_discover_and_benchmark_use_model_endpoint_with_mock(self):
        saved = self._create()
        with patch("app.infrastructure.model_transport._request") as mocked:
            mocked.return_value = {"data": [{"id": "gpt-test"}, {"id": "gpt-test-large"}]}
            response = self.a.post(f"/v1/models/connections/{saved['id']}/discover")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(response.json()["models"]), 2)
            mocked.assert_called_once()
            self.assertEqual(mocked.call_args.args[1:3], ("GET", "/models"))
            self.assertNotIn("api_key", response.text)
            payload = mocked.call_args.args[0]
            self.assertEqual(payload["api_key"], "sk-development-secret-never-display")
            mocked.reset_mock()
            mocked.return_value = {
                "choices": [{"message": {"role": "assistant", "content": "首先核实出处"}}],
                "usage": {"prompt_tokens": 15, "completion_tokens": 30},
            }
            success = self.a.post(
                f"/v1/models/connections/{saved['id']}/test",
                json={"samples": 3, "model_id": "gpt-test"},
            )
            self.assertEqual(success.status_code, 200, success.text)
            self.assertTrue(success.json()["success"])
            self.assertEqual(success.json()["samples"], 3)
            self.assertEqual(success.json()["output_tokens"], 90)
            self.assertIsNotNone(success.json()["effective_tokens_per_second"])
            self.assertEqual(mocked.call_count, 3)
            self.assertTrue(
                all(
                    call.args[1:3] == ("POST", "/chat/completions")
                    for call in mocked.call_args_list
                )
            )
            persisted = self.a.get("/v1/models/connections").json()[0]
            self.assertEqual(persisted["last_status"], "ok")
            self.assertEqual(persisted["model_id"], "gpt-test")
        mismatched = self.a.post(
            f"/v1/models/connections/{saved['id']}/test",
            json={"model_id": "different-unsaved-model"},
        )
        self.assertEqual(mismatched.status_code, 422)

    def test_failure_must_not_mark_healthy_or_expose_provider_body(self):
        saved = self._create()
        with patch(
            "app.infrastructure.model_transport._request",
            side_effect=ProviderCallError("rate_limited", 429),
        ):
            result = self.a.post(
                f"/v1/models/connections/{saved['id']}/test",
                json={"samples": 1},
            )
        self.assertEqual(result.status_code, 502)
        self.assertIn("429", result.json()["detail"])
        self.assertEqual(
            self.a.get("/v1/models/connections").json()[0]["last_status"],
            "rate_limited",
        )

    def test_protocol_conversion_discovery_and_url_gate(self):
        with patch("app.infrastructure.model_transport._request") as mocked:
            anthropic = {
                "protocol": "anthropic",
                "provider": "anthropic",
                "model_id": "claude-sample",
                "base_url": "https://api.anthropic.com/v1",
                "api_key": "mock-key",
            }
            mocked.return_value = {
                "content": [{"type": "text", "text": "Completed"}],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
            message, _, tokens = chat_completion(anthropic, [{"role": "user", "content": "你好"}])
            self.assertEqual(message["content"], "Completed")
            self.assertEqual(tokens, 2)
            self.assertEqual(mocked.call_args.args[2], "/messages")
            gemini = {
                "protocol": "gemini",
                "provider": "google",
                "model_id": "gemini-sample",
                "base_url": "https://generativelanguage.googleapis.com/v1beta",
                "api_key": "mock-key",
            }
            mocked.return_value = {
                "candidates": [{"content": {"parts": [{"text": "OK"}]}}],
                "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 5},
            }
            msg, _, output = chat_completion(gemini, [{"role": "user", "content": "你好"}])
            self.assertEqual(msg["content"], "OK")
            self.assertEqual(output, 5)
            self.assertEqual(mocked.call_args.args[2], "/models/gemini-sample:generateContent")
            mocked.return_value = {
                "models": [
                    {
                        "name": "models/gemini-text",
                        "displayName": "Gemini test",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {"name": "models/embed-only", "supportedGenerationMethods": ["embedContent"]},
                ]
            }
            listed = discover_models(gemini)
            self.assertEqual([m["id"] for m in listed["models"]], ["gemini-text"])
        for malicious in (
            "http://169.254.169.254/latest",
            "https://10.0.0.1/v1",
            "https://127.0.0.1/v1?key=oops",
            "http://remote.example/v1",
            "https://user:pass@api.openai.com/v1",
            "file:///etc/passwd",
            "http://127.0.0.1:80/v1",
        ):
            with self.subTest(url=malicious):
                with self.assertRaises(ValueError):
                    validate_base_url(malicious)
        self.assertEqual(
            validate_base_url("http://127.0.0.1:11434/v1"),
            "http://127.0.0.1:11434/v1",
        )
        self.assertEqual(
            validate_base_url("http://127.0.0.1:8000/v1"),
            "http://127.0.0.1:8000/v1",
        )
        self.assertEqual(
            discover_models(
                {
                    "protocol": "azure_openai",
                    "base_url": "https://example.openai.azure.com/openai/v1",
                }
            )["source"],
            "manual",
        )
