"""Authentication, workspace RBAC and cross-tenant isolation regression suite.

Uses isolated SQLite per test. No real accounts or external credentials.
"""

import os
import tempfile
import unittest

from app import db
from app.domain.specs import AgentSpec, Goal
from app.main import app
from fastapi.testclient import TestClient

PASSWORD = "local-testing-password-123"


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.previous = {
            name: os.environ.get(name)
            for name in ("AGENT_STUDIO_DB_PATH", "AGENT_STUDIO_CHECKPOINT_PATH", "APP_ENV")
        }
        os.environ["AGENT_STUDIO_DB_PATH"] = os.path.join(self.folder.name, "auth.sqlite3")
        os.environ.pop("AGENT_STUDIO_CHECKPOINT_PATH", None)
        os.environ.pop("APP_ENV", None)
        self.first_context = TestClient(app)
        self.a = self.first_context.__enter__()
        self.second_context = TestClient(app)
        self.b = self.second_context.__enter__()
        self.user_a = self.register(self.a, "alice@example.test")
        self.user_b = self.register(self.b, "bob@example.test")
        self.space_a = self.user_a["workspaces"][0]["id"]
        self.space_b = self.user_b["workspaces"][0]["id"]
        self.a.headers["X-Workspace-ID"] = self.space_a
        self.b.headers["X-Workspace-ID"] = self.space_b

    def tearDown(self):
        self.first_context.__exit__(None, None, None)
        self.second_context.__exit__(None, None, None)
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.folder.cleanup()

    def register(self, client: TestClient, email: str):
        result = client.post(
            "/v1/auth/register",
            json={"email": email, "password": PASSWORD},
        )
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    @staticmethod
    def agent_spec(name="团队 Agent"):
        return AgentSpec(name=name, goal=Goal(task="为团队研究企业竞争格局并生成报告")).model_dump()

    def new_agent(self, client: TestClient, name="团队 Agent"):
        response = client.post("/v1/agents", json=self.agent_spec(name))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_credentials_are_hashed_cookie_is_httponly_strict(self):
        res = self.a.post(
            "/v1/auth/login",
            json={"email": "ALICE@EXAMPLE.TEST", "password": PASSWORD},
        )
        self.assertEqual(res.status_code, 200)
        header = res.headers["set-cookie"]
        self.assertIn("httponly", header.lower())
        self.assertIn("samesite=strict", header.lower())
        self.assertNotIn(PASSWORD, header)
        with db.connect() as conn:
            saved = conn.execute(
                "SELECT password_hash FROM users WHERE email=?", ("alice@example.test",)
            ).fetchone()[0]
            session_token_hash = conn.execute("SELECT token_hash FROM sessions LIMIT 1").fetchone()[
                0
            ]
        self.assertTrue(saved.startswith("scrypt$"))
        self.assertNotIn(PASSWORD, saved)
        self.assertNotIn(self.a.cookies.get("studio_session"), session_token_hash)

    def test_duplicate_and_invalid_credentials(self):
        duplicate = self.a.post(
            "/v1/auth/register", json={"email": "ALICE@example.test", "password": PASSWORD}
        )
        self.assertEqual(duplicate.status_code, 409)
        invalid = self.a.post(
            "/v1/auth/login",
            json={"email": "alice@example.test", "password": "wrong-password-000"},
        )
        self.assertEqual(invalid.status_code, 401)
        invalid_schema = self.a.post(
            "/v1/auth/register", json={"email": "bad", "password": "small"}
        )
        self.assertEqual(invalid_schema.status_code, 422)

    def test_logout_revokes_session_and_routes_fail_closed(self):
        self.assertEqual(self.a.get("/v1/auth/me").status_code, 200)
        out = self.a.post("/v1/auth/logout")
        self.assertEqual(out.status_code, 200)
        for path in ("/v1/auth/me", "/v1/agents", "/v1/runs", "/v1/tools"):
            self.assertEqual(self.a.get(path).status_code, 401, path)
        self.assertEqual(self.a.post("/v1/agents", json=self.agent_spec()).status_code, 401)

    def test_workspace_required_and_cross_workspace_read_denied(self):
        agent = self.new_agent(self.a)
        run = self.a.post(f"/v1/agents/{agent['id']}/runs", json={"input": "分析"}).json()
        self.assertEqual(run["status"], "QUEUED")
        # B cannot enumerate A's workspace, even if they guess the ID.
        self.assertEqual(self.b.get("/v1/agents").json(), [])
        self.assertEqual(self.b.get("/v1/runs").json(), [])
        self.assertEqual(self.b.get(f"/v1/agents/{agent['id']}").status_code, 404)
        self.assertEqual(self.b.get(f"/v1/agents/{agent['id']}/versions").status_code, 404)
        self.assertEqual(self.b.get(f"/v1/runs/{run['id']}").status_code, 404)
        self.assertEqual(self.b.get(f"/v1/runs/{run['id']}/events").status_code, 404)
        self.assertEqual(self.b.get(f"/v1/runs/{run['id']}/checkpoint").status_code, 404)
        self.assertEqual(self.b.post(f"/v1/runs/{run['id']}/cancel").status_code, 404)
        self.assertEqual(
            self.b.post(f"/v1/agents/{agent['id']}/runs", json={"input": "窃取数据"}).status_code,
            404,
        )
        self.assertEqual(
            self.b.post(
                f"/v1/agents/{agent['id']}/versions",
                json={"expected_version": 1, "spec": self.agent_spec("越权修改")},
            ).status_code,
            404,
        )
        # Guessed or missing workspace header is never used to fall back to all data.
        self.b.headers["X-Workspace-ID"] = self.space_a
        self.assertEqual(self.b.get("/v1/agents").status_code, 403)
        self.b.headers.pop("X-Workspace-ID")
        self.assertEqual(self.b.get("/v1/agents").status_code, 400)

    def test_single_user_second_workspace_is_data_isolated(self):
        a_agent = self.new_agent(self.a)
        second = self.a.post("/v1/workspaces", json={"name": "第二研发团队"})
        self.assertEqual(second.status_code, 201)
        self.a.headers["X-Workspace-ID"] = second.json()["id"]
        self.assertEqual(self.a.get("/v1/agents").json(), [])
        self.assertEqual(self.a.get(f"/v1/agents/{a_agent['id']}").status_code, 404)
        other = self.new_agent(self.a, "第二团队助手")
        self.assertEqual(len(self.a.get("/v1/agents").json()), 1)
        self.a.headers["X-Workspace-ID"] = self.space_a
        self.assertEqual(self.a.get(f"/v1/agents/{other['id']}").status_code, 404)

    def test_viewer_can_read_but_cannot_modify(self):
        agent = self.new_agent(self.a)
        shared = self.a.post(
            f"/v1/workspaces/{self.space_a}/members",
            json={"user_email": "bob@example.test", "role": "viewer"},
        )
        self.assertEqual(shared.status_code, 201)
        self.b.headers["X-Workspace-ID"] = self.space_a
        self.assertEqual(len(self.b.get("/v1/agents").json()), 1)
        self.assertEqual(self.b.get(f"/v1/agents/{agent['id']}").status_code, 200)
        self.assertEqual(self.b.post("/v1/agents", json=self.agent_spec()).status_code, 403)
        self.assertEqual(
            self.b.post(
                f"/v1/agents/{agent['id']}/runs", json={"input": "not allowed"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.b.post(
                f"/v1/agents/{agent['id']}/versions",
                json={"expected_version": 1, "spec": self.agent_spec()},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.b.post(
                f"/v1/workspaces/{self.space_a}/members",
                json={"user_email": "alice@example.test", "role": "admin"},
            ).status_code,
            403,
        )

    def test_member_can_create_and_revoke_is_immediate(self):
        granted = self.a.post(
            f"/v1/workspaces/{self.space_a}/members",
            json={"user_email": "bob@example.test", "role": "member"},
        )
        self.assertEqual(granted.status_code, 201)
        self.b.headers["X-Workspace-ID"] = self.space_a
        agent = self.new_agent(self.b, "Bob 的 Agent")
        self.assertEqual(self.a.get(f"/v1/agents/{agent['id']}").status_code, 200)
        # Change role to viewer; next request must reflect it, not a cached claim.
        downgraded = self.a.post(
            f"/v1/workspaces/{self.space_a}/members",
            json={"user_email": "bob@example.test", "role": "viewer"},
        )
        self.assertEqual(downgraded.status_code, 201)
        self.assertEqual(self.b.post("/v1/agents", json=self.agent_spec()).status_code, 403)

    def test_owner_cannot_be_downgraded_by_admin(self):
        self.a.post(
            f"/v1/workspaces/{self.space_a}/members",
            json={"user_email": "bob@example.test", "role": "admin"},
        )
        self.b.headers["X-Workspace-ID"] = self.space_a
        changed = self.b.post(
            f"/v1/workspaces/{self.space_a}/members",
            json={"user_email": "alice@example.test", "role": "viewer"},
        )
        self.assertEqual(changed.status_code, 409)
        members = self.a.get(f"/v1/workspaces/{self.space_a}/members").json()
        owner = next(m for m in members if m["email"] == "alice@example.test")
        self.assertEqual(owner["role"], "owner")

    def test_authenticated_responses_are_not_cached(self):
        response = self.a.get("/v1/agents")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_origin_guard_and_member_scope_mismatch(self):
        self.assertEqual(
            self.a.post(
                "/v1/agents", json=self.agent_spec(), headers={"Origin": "https://other.example"}
            ).status_code,
            403,
        )
        self.assertEqual(self.a.get(f"/v1/workspaces/{self.space_b}/members").status_code, 403)

    def test_expired_session_rejected(self):
        with db.connect() as conn:
            conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.a.get("/v1/auth/me").status_code, 401)

    def test_legacy_unowned_records_are_quarantined_from_api_and_worker(self):
        agent = self.new_agent(self.a)
        run = self.a.post(f"/v1/agents/{agent['id']}/runs", json={"input": "未授权旧任务"}).json()
        with db.connect() as conn:
            # Simulate data created before any ownership could be proved.
            conn.execute("UPDATE agents SET workspace_id=NULL WHERE id=?", (agent["id"],))
            conn.execute("UPDATE runs SET workspace_id=NULL WHERE id=?", (run["id"],))
        db.initialize()  # Migration must not reassign unverified data.
        self.assertEqual(self.a.get("/v1/agents").json(), [])
        self.assertEqual(self.a.get("/v1/runs").json(), [])
        self.assertEqual(self.a.get(f"/v1/agents/{agent['id']}").status_code, 404)
        self.assertEqual(self.a.get(f"/v1/runs/{run['id']}").status_code, 404)
        self.assertIsNone(db.claim_next("no-legacy-worker"))
        self.assertEqual(db.get_run(run["id"])["status"], "QUEUED")


if __name__ == "__main__":
    unittest.main()
