# S2 C08 — SQLite 本地只读评测快照仓储（staging）

新增 `services/api/app/infrastructure/sqlite/evaluation_read_repository.py` 与 `services/api/tests/test_evaluation_sqlite_read_repository.py`。

## 真实实现范围

- `SqliteEvalReadStore` 是原有 `EvalReadStore` 协议的 SQLite **只读**适配器；它只读取服务端连接的 `workspace_members`、`agents`、`agent_versions`、`runs`、`evaluation_snapshots`，没有写入/初始化/迁移 API。
- `member_role()` 每次从数据库查询成员角色；`load_snapshot()` 在独立 SQLite `BEGIN` 事务内重新校验成员，然后用工作空间、Run、Agent 和版本的 JOIN 约束读取快照。拒绝跨租户、已撤权、无主数据及未完成 Run。
- 对 JSON 采取严格字段/类型检查、重复键拒绝、单份 1 MiB 限制；重算 suite SHA-256 指纹，核查每个用例提交是否完整，最后交由现有 `read_reconciled_evaluation()` 对账。
- 所有契约测试在 Python 临时文件创建最小 SQLite 表与**虚构测试数据**；不连接项目真实数据库、不访问网络/真实模型。

## 未交付与风险

- **数据库尚未集成**：`evaluation_snapshots` 目前仅在测试用的临时 SQLite 模式中存在，未修改项目实际 migrations，也无可信执行器写入该表。调用此适配器不会使真实服务自动拥有快照。
- 没有 HTTP 路由、生产身份联动、PG17/RLS、执行签名、审批或发布/回滚；读取 SQLite 数据并不证明来源可信，不能作为发布授权令牌。
- `BEGIN` 保障本次读取的一致性快照。若并发撤权发生在事务开始之后，需基于事务隔离级别和上层撤权流程评估线性化语义；这里仅验证调用间已提交撤权必须在二次检查时失权。
- 后续需设计独立且可迁移的持久快照表、只允许可信 Runner 写入的审计链、应用级 HTTP Scope、真实 Postgres RLS 负向测试，再考虑发布流程。任何生产迁移或部署须额外授权。

## 测试记录

本轮测试执行结果见 `docs/10_IMPLEMENTATION_CHANGELOG.md`。离线 unittest/Ruff/架构/Web/Smoke 通过不能替代真实 LLM、数据库权限或发布验收。
