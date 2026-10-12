# S2 C08 — 从终态 Run 输出与执行事件生成单用例 Eval（staging）

新增 `services/api/app/infrastructure/sqlite/evaluation_run_projection.py` 与 `services/api/tests/test_evaluation_run_projection.py`，复用上一轮 `evaluation_snapshot_writer.py`，不修改正式迁移、Worker、HTTP API、真实数据。

## 开发实现

- 仅接受**显式 SQLite 写事务**和一个合法 EvalCase；在同一事务中查询 `runs.output` 及绑定的 workspace、Agent、版本，只有 `SUCCEEDED` Run 允许投影。
- 必须具有连续递增的 Run Event 序列（至多 500 条）、至少一个 `model_step`、唯一且位于末尾的 `completed` 事件。完成事件的规范负载须明确 `reason=model_final_response` 以及严格布尔 `quality_evaluated=false`。
- 答案直接取持久化的 Run 输出；为 `tool_completed` 事件生成 `run-event:<seq>` 标识符作为评测可比较证据，拒绝将 `tool_rejected` 当成已调用成功的工具。
- 构建 `EvalExecutionRecord`，通过现有 **append-only** staging writer 保存；重复写入、无效输入及无可信形状的事件序列均按拒绝策略处理。
- 所有测试使用独立临时 SQLite 与虚构数据，涵盖范围绑定、假阳性保护、答案错误仍评分失败、恶意完成事件、错误状态和事务失败。

## 未验收及安全边界

当前的 `run_events` 与 `runs.output` 未提供数据库级不可变审计、来源签名或真实 LLM 证明；**从同一 SQLite 事务读取，不等于证明执行结果不可篡改**。事件标识只证明存在一行相应类型的记录，不等于答案引文有效或外部工具结果真实。该投影只支持单个 Run/单个 EvalCase，不具备真正的多用例评测调度和来源证据核验。

仍未接入受控 Worker、正式迁移、可撤权 HTTP 权限、PG17/RLS、发布审批或回滚。因此不可认定 C08 或 S2 已验收，更不能授权部署公网或生产数据库变更。
