# S2 C08 — 离线 Eval 批次回放（增量）

新增 `app/domain/evaluation_batch.py` 与独立离线单测 `tests/test_evaluation_batch.py`。通过 `EvalSubmission` 记录每个用例的原始答案和精确证据标识，并用 `replay_eval_batch(manifest, submissions)` 调用既有确定性 `evaluate()` 生成 `EvalSummary`。

## Fail-closed 合同

- 输入必须是已有不可变 `EvalSuiteManifest` 和 `tuple[EvalSubmission, ...]`。
- 每个套件用例必须恰好有一条提交，重复、缺失、额外 case_id 均拒绝处理；非法提交结构、空 case_id、非法证据均拒绝处理。
- 用例和输入提交的排列次序不改变结果，结果按 case_id 排序；空答案是可评分失败，而不是静默跳过；oracle 缺失仍为不可比较。
- 该函数只有**离线重放/输入完整性**价值，不证明提交来自可信 LLM 执行器，不能证明工作空间/版本授权，也不执行发布、审批或回滚。正式 C08 必须在可信后端验证执行来源、版本与租户绑定后重新生成评测结果并接入发布门禁。

## 验收记录

本模块与旧状态为 `outcome_unknown` 的 Eval 任务范围隔离，不修改原有评测领域文件、数据库及 Web。新增契约测试涵盖稳定重放、正确评分、失败理由、不可比较、缺失/重复/越界、非法类型与不可变性。实际测试与质量检查结果以本轮追加到 `docs/10_IMPLEMENTATION_CHANGELOG.md` 的退出状态为准；无真实 LLM 验收。

2026-10-11 已完成本地验证：本文件对应新增离线契约 **14/14**、后端全量 unittest **152/152**（24.603 秒，退出码 0）、Ruff lint/format（68 文件）、架构检查、模拟 LangGraph Smoke，以及 Web lint/typecheck/build 均通过。WebCodex 的 Codex ACP 编码委托本轮被平台安全检查阻断，改用项目本地结构化编辑；未重试既有 outcome_unknown Eval 持久任务。上述验证仅覆盖离线规则，不证明可信执行来源或正式 C08 发布链路。

