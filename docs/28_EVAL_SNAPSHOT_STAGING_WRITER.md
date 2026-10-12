# S2 C08 — SQLite 本地 Eval 快照追加写入（staging）

新增 `services/api/app/infrastructure/sqlite/evaluation_snapshot_writer.py` 与 9 项临时 SQLite 契约测试，作为现有只读适配器的内部写入前置构件。**没有接入用户 API / Worker，也没有修改正式迁移或生产数据。**

## 已实现

- 调用方必须先开启写事务，明确控制提交与回滚；检查 Run/Agent/版本/工作空间关联与终态（SUCCEEDED、PARTIAL、FAILED）。
- 重算评测套件 SHA-256 指纹，并检查每个 case 都有准确的一条提交；按现有只读适配器兼容的 JSON 形状保存，每份 UTF-8 JSON 最多 1 MiB。
- 采用纯 `INSERT` 而非覆盖；重复写入被拒绝，既有快照不可替换。测试使用虚构用户、Run 和独立临时数据库，覆盖跨租户/版本、未完成 Run、非法指纹、漏项、超大数据及事务回滚。

## 明确阻断

此组件**不能证明评测数据来自真实 Agent 执行**：任何内部 Python 调用方都能构造记录。必须在正式接入前由可信 Worker 从不可变 Run Events 提取结果、限定数据库写权限、建立正式迁移与审计日志。当前项目正式 SQLite/PostgreSQL 不包含 `evaluation_snapshots`，不能由此认定已可在线评测。

没有 HTTP Auth、正式 PostgreSQL 17/RLS、发布审批/回滚、真实 LLM；它仅是本地 staging 模块，不授予发布资格。完整验收仍依据 docs/03_ACCEPTANCE_MATRIX.md C08 进行。
