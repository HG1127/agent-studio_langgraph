# S2 C08 — 离线评测执行数据对账

新增模块：services/api/app/domain/evaluation_execution_binding.py
新增测试：services/api/tests/test_evaluation_execution_binding.py

该领域模块将提交方报告与上层应用从服务端记录读取的评测批次进行逐项比较，核对工作空间、Agent、版本、Run、评测套件指纹、完整用例集合、答案和证据标识。任一不一致则拒绝；匹配时仅对服务端数据执行既有离线评分函数。提交/证据的排列顺序不影响核对。

重要限制：本模块不读取数据库，也不认证记录的来源；上层应用必须从已授权的可信持久化存储获得权威快照与身份作用域，不能采用客户端同时提供的两份数据。对账产物不是签名、授权凭据或发布许可。实际发布仍需服务端身份验证、审计、审批与回滚。

2026-10-11 实测：新增契约测试 24/24 通过；后端全量 unittest 176/176 通过，24.934 秒；Ruff lint 通过，Ruff format --check 70 文件通过，架构边界检查通过；LangGraph 本地模拟 Smoke 通过；Web lint、typecheck、Next.js build 均通过。上述测试没有调用真实 LLM。

未完成：可信执行器与服务端存储接入、真实租户授权接口、不可变评测记录、发布门禁与回滚、真实 LLM 和 PG17/RLS 验收。C08/G2 尚未达到发布验收条件。
