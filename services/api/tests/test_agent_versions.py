"""Agent immutable versions, optimistic concurrency and legacy migration tests."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
import uuid

from app import db
from app.domain.specs import AgentSpec, Goal
from app.main import app
from fastapi.testclient import TestClient


class AgentVersionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.previous_db = os.environ.get("AGENT_STUDIO_DB_PATH")
        self.previous_checkpoint = os.environ.get("AGENT_STUDIO_CHECKPOINT_PATH")
        os.environ["AGENT_STUDIO_DB_PATH"] = os.path.join(self.folder.name, "versions.sqlite3")
        os.environ.pop("AGENT_STUDIO_CHECKPOINT_PATH", None)
        self.client_handle = TestClient(app)
        self.client = self.client_handle.__enter__()
        auth = self.client.post(
            "/v1/auth/register",
            json={"email": "version-tester@example.test", "password": "correct-horse-battery-123"},
        )
        self.assertEqual(auth.status_code, 201, auth.text)
        self.workspace_id = auth.json()["workspaces"][0]["id"]
        self.client.headers["X-Workspace-ID"] = self.workspace_id

    def tearDown(self):
        self.client_handle.__exit__(None, None, None)
        for name, value in (
            ("AGENT_STUDIO_DB_PATH", self.previous_db),
            ("AGENT_STUDIO_CHECKPOINT_PATH", self.previous_checkpoint),
        ):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.folder.cleanup()

    @staticmethod
    def spec(goal: str) -> dict:
        return AgentSpec(
            name="版本管理测试助手",
            goal=Goal(task=goal),
        ).model_dump()

    def create_agent(self) -> dict:
        response = self.client.post(
            "/v1/agents",
            json=self.spec("初始目标是研究一个企业的财务表现"),
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def create_version(self, agent_id: str, expected: int, goal: str):
        return self.client.post(
            f"/v1/agents/{agent_id}/versions",
            json={"expected_version": expected, "spec": self.spec(goal)},
        )

    def test_new_agent_has_v1_and_versions_are_ordered(self):
        agent = self.create_agent()
        self.assertEqual(agent["current_version"], 1)
        url = f"/v1/agents/{agent['id']}/versions"
        versions = self.client.get(url)
        self.assertEqual(versions.status_code, 200)
        self.assertEqual([v["version"] for v in versions.json()], [1])
        self.assertEqual(versions.json()[0]["spec"]["goal"]["task"], agent["spec"]["goal"]["task"])
        v2 = self.create_version(agent["id"], 1, "升级后的目标是比较五家公司财务表现")
        self.assertEqual(v2.status_code, 201, v2.text)
        self.assertEqual(v2.json()["version"], 2)
        self.assertEqual(
            [v["version"] for v in self.client.get(url).json()],
            [2, 1],
        )
        self.assertEqual(self.client.get(f"/v1/agents/{agent['id']}").json()["current_version"], 2)

    def test_stale_writes_conflict_with_no_partial_commit(self):
        agent = self.create_agent()
        self.assertEqual(
            self.create_version(agent["id"], 1, "升级后的任务范围是进行完整行业比较").status_code,
            201,
        )
        stale = self.create_version(agent["id"], 1, "旧客户端在不知道新版本时提交更改")
        self.assertEqual(stale.status_code, 409)
        self.assertIn("版本冲突", stale.json()["detail"])
        self.assertEqual(
            len(self.client.get(f"/v1/agents/{agent['id']}/versions").json()),
            2,
        )
        self.assertEqual(self.client.get(f"/v1/agents/{agent['id']}").json()["current_version"], 2)

    def test_version_rows_cannot_be_updated_or_deleted(self):
        agent = self.create_agent()
        versions = self.client.get(f"/v1/agents/{agent['id']}/versions").json()
        version_id = versions[0]["id"]
        with self.assertRaises(sqlite3.IntegrityError):
            with db.connect() as connection:
                connection.execute("UPDATE agent_versions SET spec='{}' WHERE id=?", (version_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            with db.connect() as connection:
                connection.execute("DELETE FROM agent_versions WHERE id=?", (version_id,))

    def test_run_binds_exact_snapshot_and_version_id(self):
        agent = self.create_agent()
        v1_id = self.client.get(f"/v1/agents/{agent['id']}/versions").json()[0]["id"]
        old_response = self.client.post(
            f"/v1/agents/{agent['id']}/runs", json={"input": "分析旧目标"}
        )
        self.assertEqual(old_response.status_code, 202)
        old_id = old_response.json()["id"]
        self.assertEqual(
            self.create_version(agent["id"], 1, "最新研究目标调整为新能源企业竞争分析").status_code,
            201,
        )
        latest_response = self.client.post(
            f"/v1/agents/{agent['id']}/runs", json={"input": "研究新目标"}
        )
        self.assertEqual(latest_response.status_code, 202)
        first = db.get_run(old_id)
        second = db.get_run(latest_response.json()["id"])
        self.assertEqual(first["agent_version_id"], v1_id)
        self.assertNotEqual(second["agent_version_id"], v1_id)
        self.assertIn("财务表现", json.loads(first["spec_snapshot"])["goal"]["task"])
        self.assertIn("新能源", json.loads(second["spec_snapshot"])["goal"]["task"])
        self.assertNotIn("spec_snapshot", old_response.json())
        self.assertEqual(old_response.json()["agent_version_id"], v1_id)

    def test_input_validation_and_missing_agent(self):
        agent = self.create_agent()
        self.assertEqual(
            self.create_version("nonexistent", 1, "不存在的助手更新方案测试").status_code,
            404,
        )
        self.assertEqual(
            self.create_version(agent["id"], 0, "非法预期版本测试").status_code,
            422,
        )
        self.assertEqual(self.client.get("/v1/agents/nonexistent/versions").status_code, 404)

    def test_migrate_pre_version_legacy_db_and_preserve_old_run(self):
        old_path = os.path.join(self.folder.name, "legacy.sqlite3")
        old_agent_id = str(uuid.uuid4())
        old_run_id = str(uuid.uuid4())
        old_spec = json.dumps(self.spec("历史单版本 Agent 的研究任务验证"))
        with sqlite3.connect(old_path) as connection:
            connection.executescript(
                """
                CREATE TABLE agents(
                    id TEXT PRIMARY KEY, spec TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE runs(
                    id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
                    input TEXT NOT NULL, status TEXT NOT NULL,
                    output TEXT, error TEXT, created_at TEXT NOT NULL,
                    started_at TEXT, completed_at TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO agents VALUES (?,?,?)",
                (old_agent_id, old_spec, "2025-01-01T00:00:00+00:00"),
            )
            connection.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    old_run_id,
                    old_agent_id,
                    "老任务",
                    "SUCCEEDED",
                    "历史结果",
                    None,
                    "2025-01-02T00:00:00+00:00",
                    None,
                    None,
                ),
            )
        connection.close()  # sqlite3 connection context commits but does not close on Windows
        os.environ["AGENT_STUDIO_DB_PATH"] = old_path
        db.initialize()
        db.initialize()  # idempotent
        old_agent = db.get_agent(old_agent_id)
        versions = db.list_agent_versions(old_agent_id)
        self.assertEqual(old_agent["current_version"], 1)
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["spec"]["goal"]["task"], "历史单版本 Agent 的研究任务验证")
        legacy = db.get_run(old_run_id)
        self.assertEqual(legacy["output"], "历史结果")
        self.assertIsNone(legacy["agent_version_id"])
        self.assertIsNone(legacy["spec_snapshot"])


if __name__ == "__main__":
    unittest.main()
