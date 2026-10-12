# S2 C08 — 应用层评测读权限与权威快照契约

新增 `services/api/app/application/evaluation_read_service.py` 和 `services/api/tests/test_evaluation_read_service.py`。

此应用层只读入口在任何 Run 查询前要求服务端传入**已认证** user_id，并从可信仓储逐次查询用户的工作空间成员角色（owner/admin/member/viewer）。非成员或未知角色拒绝；已授权用户请求的 workspace/Agent/version/run 必须与可信仓储返回的权威 Run scope 完全一致。任何缺失/不匹配都拒绝，不退化到客户端数据。作用域通过后才调用原有 `reconcile_eval_execution`，重新计算报告而不是信任客户端汇总，报告不得作为发布凭证。

**重要边界：** 这是基于依赖注入 `EvalReadStore` 的**应用层契约及离线负向测试**。项目目前没有已实现的、受认证保护的 Eval 存储仓储适配器；本次只用内存 FakeStore 测试，不能保证 FakeStore 外的真实数据来源、事务一致性、PG17/RLS 或端到端认证。上层 HTTP 只能通过已认证 Scope/CurrentUser，且绝不可允许用户选择 store 或自填 role；真实仓储在读取快照时还必须接收 authenticated_user_id，并于同一个只读事务内再次验证工作空间成员资格，随后读取 Run、不可变评测套件和执行记录，防止成员角色查询与快照读取之间撤销授权造成 TOCTOU 越权。发布审批、版本回滚、审计与真实 LLM 均未实现，S2 C08/G2 不算验收通过。

2026-10-11 验证：新增授权契约 unittest 16/16、后端全量 192/192（27.514 秒，exit=0）；Ruff lint 和 72 个 Python 文件的 format --check、架构检查、本地 LangGraph Stub Smoke、Web ESLint/typecheck/Next.js build 全部通过。以上只是离线/模拟测试，未请求真实 LLM；未验证 PG17/RLS 或真实仓储/发布流程。
