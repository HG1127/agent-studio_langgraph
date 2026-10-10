# Agent Studio V0.5｜PostgreSQL 17 + pgvector 数据库方案与工程实施记录

更新：2026-10-10

## 0. 结论与交付状态

**主数据库明确选 PostgreSQL 17；语义检索使用同一实例的 pgvector。** Redis 不是主数据库，仅用于后续队列/缓存；PDF 等文件通过 S3 兼容对象存储而不是数据库大字段；可核验的人物/组织/证据关系首版可使用 PostgreSQL 关系边表，待性能证据显示多跳图查询瓶颈时再评估 Neo4j。

**V0.5 已落地的范围：** 完整 SQLAlchemy PostgreSQL 模型、Alembic 初始版本迁移、RLS SQL 策略、不可变 Agent 版本和事件的权限约束、pgvector 表字段、开发容器配置、事务内身份作用域帮助函数、SQLite 旧数据只读评估脚本、离线 SQL 自动测试及未来真实 PostgreSQL RLS 集成测试脚本。

**未完成：** 目前 Web、FastAPI 和独立 Worker 仍运行在原 SQLite 本地版本。V0.5 的 PostgreSQL 模型与迁移 **不是** 实际接管运行的数据库，不可把“迁移 SQL 已生成”称作“数据库已迁移成功”。在 WebCodex Windows Runner 检查时 Docker Desktop 引擎未启动、本地 PostgreSQL 端口无实例，因此无法在该机器上完成真实 PostgreSQL 建表和 RLS 的在线验证。CI 已配置真实 pgvector 容器测试任务，但尚未推送远程仓库触发。

## 1. 为什么主数据库选 PostgreSQL

| 能力 | PostgreSQL 方案 | 选型价值 |
| --- | --- | --- |
| 用户/团队/Agent 业务实体 | PostgreSQL 17 | 事务和复杂关联具备一致性边界 |
| AgentSpec 与版本 | JSONB + 关系表 + 不可变触发器 | 配置灵活，历史版本可追溯 |
| Run/Events | 主表、快照、事件表与唯一幂等键 | 可查询、可复盘，防止数据错位 |
| Workspace 隔离 | API/RBAC + PostgreSQL RLS | 用户校验与数据库过滤形成双层限制 |
| 知识片段与长期记忆 | pgvector 类型 | 首期无需专用向量服务 |
| 关系网络 | People/Organizations/Edges/Evidence（后续迁移） | 初期避免多数据库双写；未来有规模瓶颈再评估图 DB |
| 文件对象 | S3 兼容存储 | PostgreSQL 仅保存引用、校验信息、状态 |
| 实时任务/限流 | Redis（后续） | 与权威持久化状态清楚分工 |

PostgreSQL 17 是当前项目固定开发基线，不意味着其他受支持版本无法使用。真正生产托管方式（Neon / RDS / Cloud SQL / 自托管）取决于所在地区、合规要求和运维职责，后续单独评估和决定；目前**没有创建云数据库或付费资源**。

## 2. 代码结构

```text
infra/postgres/
├── compose.yaml                   # pgvector/pgvector:pg17；只映射到 127.0.0.1:54329
└── .env.example                   # 无真实密码，复制成本地 .env 使用
services/api/
├── alembic.ini                    # 版本化迁移入口
├── alembic/
│   ├── env.py                     # 仅以显式管理员 URL 运行在线 DDL
│   ├── script.py.mako
│   └── versions/0001_pg17_workspace.py
├── requirements-postgres.txt
└── app/infrastructure/postgres/
    ├── models.py                  # 12 张 PostgreSQL 数据模型表
    └── transaction.py             # SET LOCAL actor/workspace 限定在事务内
scripts/
├── plan_sqlite_migration.py       # 只读统计，不迁移也不输出用户内容
└── smoke_postgres_rls.py          # 仅允许本机 54329/agent_studio 的事务回滚式测试
```

生产框架仍遵守原 `AGENTS.md` 和 `docs/09_CODE_STANDARDS.md` 的分层，领域模型不直接依赖 DB 连接，数据库/迁移逻辑不写在 API 路由中。

## 3. PostgreSQL 结构说明

初始版本包括 **12 张表**：

`users`、`sessions`、`workspaces`、`workspace_members`、`agents`、`agent_versions`、`runs`、`run_events`、`knowledge_sources`、`knowledge_chunks`、`memory_records`、`quarantined_legacy_rows`。

设计硬约束：

- `agents.workspace_id`、`runs.workspace_id` 以及未来知识与记忆记录的 workspace_id 全部非空。
- Run 外键强制 `agent_id + workspace_id` 同属一个空间；Run 绑定的版本 ID 必须来自同一个 Agent。
- RunEvent 外键同时校验 `run_id + workspace_id`，避免事件被错误重挂到其他工作空间。
- AgentVersion 的 UPDATE/DELETE 由数据库触发器禁止；普通运行角色只有 SELECT/INSERT 权限。
- RunEvent 仅授予运行角色 SELECT/INSERT，拒绝 UPDATE/DELETE，便于审计追溯。
- `quarantined_legacy_rows` 不授予普通运行角色任何访问权。原来没有明确归属的 SQLite 记录不会被赋给新注册账号。
- knowledge_chunks.embedding 与 memory_records.embedding 是 VECTOR 类型，目前不预设统一维度或向量索引；必须先确定具体 Embedding 模型的维度、数据量与距离函数，再建 HNSW/IVFFlat 索引，防止错误配置。

## 4. RLS 执行协议（必须严格遵守）

数据库迁移建立 `agentstudio_runtime` **NOLOGIN、NOBYPASSRLS** 角色。管理员、会话存储表及隔离历史表不向该角色开放。普通业务数据库账号未来应在严格授权后以该角色执行受限 SQL，不得拿数据库迁移管理员账号处理用户数据。

可信后端从已校验的用户 Session 和当前成员资格获取 user_id/workspace_id，在 SQLAlchemy 的**同一事务**内调用：

```sql
SELECT set_config('agent_studio.user_id', :user_id, true);
SELECT set_config('agent_studio.workspace_id', :workspace_id, true);
```

最后一个 `true` 为 `SET LOCAL` 语义，连接池复用时作用域在事务结束后消失。缺少 scope 时 RLS 返回零行、写入被拒绝；会员角色 viewer 只读，owner/admin/member 才可修改；跨空间写入同时受 RLS 和复合外键约束。

这套机制**不意味着数据库可信地自行验证 HTTP 身份**。身份核验仍必须由独立的安全认证层完成，并且要禁止向普通用户暴露任意 SQL 执行入口。

## 5. 本地启动与真实 PostgreSQL 验收步骤

**前提：先手动打开 Docker Desktop 并确认 Docker Engine 已启动。** 本轮 WebCodex 检查到 Docker CLI 可用，但 Docker Linux Engine 未运行；未自动启动或安装其他本地服务。

从仓库根目录执行（PowerShell）：

```powershell
# 首先将 infra/postgres/.env.example 复制为 infra/postgres/.env
# 在该 .env 中设置随机且保密的 STUDIO_PG_ADMIN_PASSWORD
docker compose --env-file infra/postgres/.env -f infra/postgres/compose.yaml up -d
docker compose --env-file infra/postgres/.env -f infra/postgres/compose.yaml ps

cd services/api
.\.venv\Scripts\python.exe -m pip install -r requirements-postgres.txt

# 在当前 PowerShell 会话里设置真实本地管理员 URL；
# 密码包含特殊字符时，需要进行标准 URL 百分号编码。
# 例如 STUDIO_POSTGRES_ADMIN_URL=postgresql+psycopg://studio_migrator:...@127.0.0.1:54329/agent_studio
.\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head

# 测试脚本只接受本机 127.0.0.1:54329/agent_studio
# 需要显式设置 STUDIO_POSTGRES_TEST_ADMIN_URL 后运行。
.\.venv\Scripts\python.exe ..\..\scripts\smoke_postgres_rls.py
```

不希望启动数据库也能执行**离线验收**：

```powershell
cd services/api
.\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head --sql
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_postgres_contracts.py -v
```

原 SQLite 数据排查（只读，不会迁移或输出任何用户提示词）：

```powershell
cd services/api
.\.venv\Scripts\python.exe ..\..\scripts\plan_sqlite_migration.py --sqlite-path .local/agent_studio.sqlite3
```

严禁在未经来源所有权审查时，将旧 SQLite Agent/Run 自动分配给新 Workspace。正式迁移需要来源验证、备份、映射审核、行数/关联完整性比对与回滚演练。

## 6. 数据库上线验收分级

| 门槛 | 内容 | 状态 |
| --- | --- | --- |
| DB0 | PostgreSQL ORM 表、Alembic 初始迁移、Compose 配置 | 已编写 |
| DB1 | Alembic 离线 SQL 生成、模型结构/安全契约检查 | 已做自动化测试 |
| DB2 | 真实 PostgreSQL+pgvector 启动和迁移、RLS 跨空间/角色负向测试 | 未在本地 Runner 验收；CI Job 已配置 |
| DB3 | SQLite 现有数据可信映射、验证后的实际导入与对账 | 未实现 |
| DB4 | FastAPI/Worker 正式 PG Repository 与 LangGraph Postgres Checkpoint 切换 | 未实现 |
| DB5 | 备份恢复、审计、分布式租约、安全、性能/负载与正式部署 | 未实现 |

**当前 DB1 范围已推进；请勿将其说成“PostgreSQL 已接管应用”。** PostgreSQL 实际上线应至少通过 DB2–DB5，并由测试记录证明。

**本轮本地自动化证据（2026-10-10）：** 原有功能及新增 PG 数据契约共 50 项 Python unittest 通过；Alembic 升级 SQL 生成成功，离线生成的 SQL 约 15.7 KB；Docker Compose 语法验证、Ruff/模块边界、Next.js ESLint/TypeScript/生产构建，以及两条已存在的 HTTP Stub 执行链路均验证通过。PG17 的真实服务启动、建表、RLS 执行与 DB2 集成仍未完成。


## 7. 特别风险与决策

1. Alembic 初始版本刻意拒绝可能删除用户数据的破坏性 `downgrade`；灾难恢复应使用验证过的数据库备份，而不是不受控地 DROP 全部表。
2. `agentstudio_runtime` 为数据库全局角色；迁移会检查现有同名角色不能 LOGIN、不能 SUPERUSER 或 BYPASSRLS。
3. 数据库迁移管理员需要创建扩展、创建角色权限；正式环境必须经 DBA 授权，不应让普通 API 连接拥有这些权限。
4. 当前本地身份系统没有生产级 OIDC/MFA、限流与 HTTPS 相关安全验收；即使 PostgreSQL DB2 完成，应用仍不得直接公开部署。
5. PostgreSQL 事务角色上下文与 LangGraph 持久化检查点是两个不同问题：未来应单独使用受控 Worker/Checkpoint 连接，不允许用户通过检查点接口直接越权访问线程。
6. 本阶段不创建 Neo4j 或专用向量数据库；人物关系图如果演化为千万级节点与复杂多跳查询，可按真实查询基准再评估。
