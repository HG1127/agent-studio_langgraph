"""AgentSpec draft normalization regression tests; all model calls mocked."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.application.draft_normalizer import DraftFormatError, normalize_draft
from app.llm import generate_draft
from app.main import app
from fastapi.testclient import TestClient


class DraftNormalizerTests(unittest.TestCase):
    @staticmethod
    def message(body):
        return {"role": "assistant", "content": body}

    def test_flat_json(self):
        reply = self.message(
            json.dumps(
                {
                    "name": "研究助手",
                    "description": "收集并核查研究资料",
                    "task": "研究全球人物的公开商业联系",
                    "success_criteria": ["有可核查来源", "无证据关系标注待核实"],
                },
                ensure_ascii=False,
            )
        )
        parsed = normalize_draft(reply)
        self.assertEqual(parsed.name, "研究助手")
        self.assertEqual(parsed.goal.success_criteria[0], "有可核查来源")
        self.assertFalse(parsed.permissions.external_write_allowed)

    def test_markdown_fenced_nested_full_agent_spec(self):
        data = {
            "spec": {
                "name": "人物关系助手",
                "description": "查证联系",
                "goal": {
                    "task": "核验目标人物的组织与资本关系",
                    "success_criteria": ["逐边引用证据"],
                },
                "permissions": {"external_write_allowed": True},
                "tools": {"enabled": ["fake_network_tool"]},
            }
        }
        parsed = normalize_draft(
            self.message("下面是配置：\n```json\n" + json.dumps(data, ensure_ascii=False) + "\n```")
        )
        self.assertEqual(parsed.name, "人物关系助手")
        self.assertEqual(parsed.goal.task, "核验目标人物的组织与资本关系")
        self.assertFalse(parsed.permissions.external_write_allowed)
        self.assertNotIn("fake_network_tool", parsed.tools.enabled)

    def test_success_criterion_text_and_missing_default(self):
        raw = {"title": "数据分析", "goal": "自动分析授权的交易数据并生成摘要"}
        spec = normalize_draft(self.message(raw))
        self.assertEqual(spec.name, "数据分析")
        self.assertTrue(spec.goal.success_criteria)
        raw["success_criteria"] = "统计值可以复核"
        self.assertEqual(
            normalize_draft(self.message(raw)).goal.success_criteria,
            ["统计值可以复核"],
        )

    def test_missing_fields_and_non_json_rejected(self):
        invalid = [
            "I cannot output JSON",
            json.dumps({"name": "Agent", "task": "short", "success_criteria": []}),
            json.dumps(
                {
                    "name": "Agent",
                    "task": "完成一个具体的数据分析工作",
                    "success_criteria": {"a": 1},
                }
            ),
            "{}",
            "",
        ]
        for source in invalid:
            with self.subTest(source=source):
                with self.assertRaises(DraftFormatError):
                    normalize_draft(self.message(source))

    def test_provider_output_size_limited(self):
        with self.assertRaises(DraftFormatError):
            normalize_draft(self.message("X" * 30_001))

    def test_one_model_retry_corrects_bad_format(self):
        with (
            patch("app.llm.model_for_workspace", return_value={"mock": True}),
            patch("app.llm.ModelGateway") as gateway_cls,
        ):
            gateway = gateway_cls.return_value
            gateway.chat.side_effect = [
                {"content": "This is not JSON"},
                self.message(
                    json.dumps(
                        {
                            "name": "商业研究助手",
                            "task": "分析企业经营数据并标注核查依据",
                            "success_criteria": ["提供可核查的经营依据"],
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
            spec = generate_draft("帮我生成商业研究助手", "test-workspace")
            self.assertEqual(spec.name, "商业研究助手")
            self.assertEqual(gateway.chat.call_count, 2)
            self.assertTrue(all(call.kwargs["json_mode"] for call in gateway.chat.call_args_list))

    def test_invalid_twice_reports_model_format_not_user_error(self):
        with (
            patch("app.llm.model_for_workspace", return_value={"mock": True}),
            patch("app.llm.ModelGateway") as gateway_cls,
        ):
            gateway_cls.return_value.chat.return_value = {"content": "not JSON"}
            with self.assertRaisesRegex(DraftFormatError, "模型已响应"):
                generate_draft("编写一个分析企业商业计划的Agent", "test-workspace")
            self.assertEqual(gateway_cls.return_value.chat.call_count, 2)


class AgentDraftRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("AGENT_STUDIO_DB_PATH")
        os.environ["AGENT_STUDIO_DB_PATH"] = os.path.join(self.temp.name, "draft.sqlite3")
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        registered = self.client.post(
            "/v1/auth/register",
            json={"email": "draft-tests@example.test", "password": "local-test-password-123"},
        )
        self.assertEqual(registered.status_code, 201, registered.text)
        self.client.headers["X-Workspace-ID"] = registered.json()["workspaces"][0]["id"]

    def tearDown(self):
        self.context.__exit__(None, None, None)
        if self.previous is None:
            os.environ.pop("AGENT_STUDIO_DB_PATH", None)
        else:
            os.environ["AGENT_STUDIO_DB_PATH"] = self.previous
        self.temp.cleanup()

    def test_schema_failure_returns_upstream_502_with_actionable_message(self):
        with (
            patch("app.llm.model_for_workspace", return_value={"mock": True}),
            patch("app.llm.ModelGateway") as gateway_cls,
        ):
            gateway_cls.return_value.chat.return_value = {"content": "Oops, not JSON"}
            response = self.client.post(
                "/v1/agents/drafts/generate", json={"description": "研究全球人物背景关系"}
            )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("手动配置草稿", response.json()["detail"])
        self.assertNotIn("修改输入", response.json()["detail"])

    def test_success_creates_safe_server_agent_spec(self):
        with (
            patch("app.llm.model_for_workspace", return_value={"mock": True}),
            patch("app.llm.ModelGateway") as gateway_cls,
        ):
            gateway_cls.return_value.chat.return_value = {
                "content": json.dumps(
                    {
                        "name": "资产研究助手",
                        "goal": {
                            "task": "对公开市场的企业数据进行调查并核查",
                            "success_criteria": ["给出核验后的来源"],
                        },
                        "permissions": {"external_write_allowed": True},
                    },
                    ensure_ascii=False,
                )
            }
            response = self.client.post(
                "/v1/agents/drafts/generate", json={"description": "研究公开上市企业的经营数据"}
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["spec"]["permissions"]["external_write_allowed"])
        self.assertEqual(
            response.json()["spec"]["goal"]["task"], "对公开市场的企业数据进行调查并核查"
        )


if __name__ == "__main__":
    unittest.main()
