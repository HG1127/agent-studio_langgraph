# Agent Studio V0.7｜PostgreSQL 作用域队列与 Worker 租约

日期：2026-10-10

## 1. 交付范围

本轮新增 PostgreSQL 工作空间作用域内的任务领取、心跳、租约过期恢复和完成操作；代码位于 `services/api/app/infrastructure/postgres/scoped_run_queue.py`。同时新增离线测试 `services/api/tests/test_postgres_scoped_queue.py`，并在 `scripts/smoke_postgres_rls.py` 中加入未来真实 PostgreSQL 的队列生命周期测试。

**此模块尚未接入 FastAPI 或 LangGraph Worker**；目前应用仍使用 SQLite。即使宝塔 PostgreSQL 17.11 + pgvector 0.8.7 已部署且通过 SSH 隧道登录，仍未获得 Alembic Check/Migrate/RlsTest 的实际通过记录，不能宣称 PostgreSQL 已接管运行。

## 2. 业务规则

- 由经过 actor/workspace 和 RLS 验证的 `PgScopedStore` 创建队列对象；不允许使用数据库迁移管理员账户执行业务。
- 只领取本工作空间内状态 QUEUED 的 Run，使用 `SELECT FOR UPDATE SKIP LOCKED` 防止并发重复领取。
- `attempts` 是执行世代号；心跳和完成操作必须匹配 RunId、workspace_id、attempt、RUNNING 和未过期租约，旧 Worker 不能提交过期结果。
- 过期任务按本空间锁定与恢复；超过最大重试次数标为 FAILED，否则回到 QUEUED。
- Run 状态变化和 RunEvent 追加处于调用方管理的同一事务。事件追加按世代生成幂等 key，不允许业务层单独 commit。
- 该模型是单 workspace 安全队列的开发模块，不包含全局 Worker 的安全服务身份、跨空间调度、PostgresSaver 或模型副作用幂等。

## 3. 测试证据与限制

新增队列模块在独立的本地 Python + SQLAlchemy 隔离测试环境中通过 8 项模拟/SQL 编译测试：锁定领取、空队列、租约心跳、防旧世代完成、结果事件、租约恢复、错误输入和事务边界。该测试使用代表性 Run/RunEvent ORM stub，而不是宝塔真实数据库实例。

真实 PostgreSQL 测试脚本已增加：Agent v1→v2 / queue / claim / heartbeat / finalize / lease-expired recovery / stale finish rejection / viewer read-only / cross-workspace denial。目前未执行。正式 DB2 验收必须实际运行，不能以离线通过代替。

## 4. 并发开发说明

本轮发现 WebCodex 还有未提交的模型连接相关改动，且四个长时间运行的后台开发服务进程占用 Runner 执行槽。只新增独立队列文件、专项测试及此文档，不覆盖其他会话的模型功能，也不停止后台服务。若 Runner 全部测试暂不可用，应在不影响已有服务时重新运行完整项目测试并单独提交本轮文件。

## 5. 后续落地顺序

1. Windows SSH 隧道保持运行，执行 `& .\scripts\pg_staging.ps1 -Action Check`，确认独立测试库。
2. 预检显示 `initial_empty / safe_for_baseline: True` 后，由用户确认执行 Alembic `-Action Migrate`。
3. 迁移成功后执行 `-Action RlsTest`，确认真实 DB RLS 与本轮队列测试结果。
4. 创建独立非管理员 DB 运行账户、专用服务主体，并做 RLS 权限撤销测试。
5. 集成 `langgraph-checkpoint-postgres`，实测崩溃恢复和多 Worker 并发；必须处理外部写操作幂等。
6. 数据迁移需要所有权审查、备份和对账。通过全链路验收后再显式切换 SQLite → PostgreSQL。
