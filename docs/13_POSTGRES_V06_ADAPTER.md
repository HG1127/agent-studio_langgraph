# Agent Studio V0.6｜PostgreSQL 权限与 Agent 仓储适配实施报告

**更新日期：2026-10-10**

## 0. 项目结论

延续此前决定：以 **PostgreSQL 17 + pgvector** 作为 Agent Studio 的未来主数据库，保留模块化单体结构，不为单 Agent Studio 首期引入独立图数据库或专用向量数据库。Redis 后续仅负责队列与缓存，文件放对象存储。

本轮重点是**纠正初始 PG 迁移中的关键语法缺陷，并新增可执行的事务化 PostgreSQL Agent/Version/Run 仓储代码**。不是重新设计数据模型，也没有替换已经可用的 SQLite Web/API/Worker。

### 当前真实状态

- SQLite 本地工作台仍然正常运行；
- PostgreSQL/Alembic 的全部升级 SQL 已通过 PostgreSQL 语法解析器；
- Scoped PostgreSQL Repository 已有方法和独立测试，具备单事务创建 Agent、更新版本、任务入队及事件记录等基础功能；
- PostgreSQL 数据库在 WebCodex Windows Runner 尚未启动：Docker Engine 无法连接，指定的本机 PostgreSQL 端口也不可用，因此**没有进行真实数据库写入/权限执行验收**；
- CI 具备 pgvector/pg17 容器集成测试定义，等待推送至授权 Git 仓库后实际执行；当前本地 Git 不存在远程仓库；
- `STUDIO_DATABASE_BACKEND=postgres` 保持禁止启动 API/Worker，不自动切换、不暗中落回 SQLite。

## 1. 本轮重要修复

V0.5 Alembic 初始迁移 `services/api/alembic/versions/0001_pg17_workspace.py` 存在 PostgreSQL 匿名代码块分隔符错误：

```sql
-- 修复前（错误）
END $;

-- 修复后
END $$;
```

仅检查 SQL 是否包含某些关键词不足以发现这类问题。现在开发依赖加入 `pglast`，自动测试会对 Alembic 生成的**整份 SQL**执行 PostgreSQL 语法解析；这相当于离线 DB 语法门禁，不等于真正执行迁移。

## 2. 仓储模块位置

```text
services/api/app/infrastructure/postgres/
├── models.py
├── transaction.py           # 每事务安全角色与身份上下文
└── scoped_store.py          # PgScopedStore：业务对象的事务内 CRUD

services/api/alembic/
└── versions/0001_pg17_workspace.py

services/api/tests/
├── test_postgres_contracts.py
└── test_postgres_scoped_store.py

scripts/
├── smoke_postgres_rls.py    # 未来真实 PG 集成：含 PgScopedStore + viewer 场景
└── plan_sqlite_migration.py # 不接入 PG，不读取具体隐私内容的迁移评估
```

这些代码保持 `AGENTS.md` 的依赖规则：领域定义在 `domain`；SQLAlchemy 对象与事务只在 `infrastructure`；HTTP API 路由不直接执行任意 SQL。

## 3. PgScopedStore 已实现的业务操作

| 操作 | 实施行为 | 保证 |
| --- | --- | --- |
| create_agent | 同事务 INSERT Agent + AgentVersion v1 | 当前版有不可变历史版本 |
| get_agent/list_agents | 明确 WHERE workspace_id + DB RLS | 无跨工作空间默认查询 |
| list_versions | 通过 Agent 连接并验证 workspace | 不暴露其他空间版本 |
| create_version | SELECT FOR UPDATE + expected_version + vN+1 | 并发更新冲突；不会覆盖旧版 |
| queue_run | 读取当前已确认版本，同事务 INSERT Run + RunEvent | Run 绑定原始版本与初始队列事件 |
| get_run/list_runs | 明确工作空间过滤 + RLS | 只能读取当前空间的任务 |
| get_events | 使用 Run 和 workspace 双重限制 + cursor | 防止通过 event_id 越权查询 |

`PgScopedStore` 不提供 `commit()`；事务必须由调用方 UnitOfWork 管理，因此失败时 Agent 与版本、Run 与事件不会出现半完成。它只接受 PostgreSQL 连接，初始化时验证当前执行角色必须是受 RLS 约束的 `agentstudio_runtime`，并要求事务已设置有效的 actor/workspace UUID。

## 4. 数据库身份作用域与权限模型

`workspace_transaction(engine,user_id,workspace_id)` 的职责：

1. 验证参数是 Python UUID，数据库确实为 PostgreSQL；
2. 检查当前 PostgreSQL 登录角色**不是**超级用户、拥有 BYPASSRLS 的账户或 `studio_migrator`；
3. `SET LOCAL ROLE agentstudio_runtime`（数据库管理员角色禁止用于业务执行）；
4. 在同一事务内 `set_config('agent_studio.user_id', ..., true)` 和 `set_config('agent_studio.workspace_id', ..., true)`；
5. 事务结束后自动清理这些临时范围，避免连接池中不同用户共享状态。

数据库 RLS 自身使用 `workspace_members` 校验 actor 对该空间是否有成员资格，查看角色 viewer 能读不能写。即使数据库范围正确，前端/后端仍必须从真实鉴权会话获得 actor，**用户不得提交任意 SQL 或自行声明 actor UUID**。

`agentstudio_runtime` 为数据库 **NOLOGIN** 角色。正式运行还需要由 DBA 单独建立非特权、可登录且获授权执行 `SET ROLE` 的应用账号；本轮**没有创建正式账号或泄露数据库密码**。

## 5. 测试与证据分级

**DB1（可在目前 Runner 验证）：**

- `pglast` 解析完整 PostgreSQL/Alembic 升级 SQL；
- SQLAlchemy 模型、JSONB/Vector、复合外键、RLS 策略、不可变历史及只追加事件的静态契约测试；
- 测试 PostgreSQL Adapter 拒绝 SQLite、迁移管理员角色、空的/mismatched Scope 和非法 UUID；
- 编译并验证 Agent、Version、Run 和事件 SQL 的范围条件、事务分组和冲突语义；
- 原 SQLite/Next.js/LangGraph 回归仍需保持通过。

**DB2（必须连接真正 PG17 才能验证）：**

`scripts/smoke_postgres_rls.py` 将在临时事务中建立三名合成用户及两个工作空间，并针对真实 PG 验证：
- 未设置 Scope 时查询不到 Agent；
- owner 可以创建 Agent、从 v1 升级到 v2、创建 Run + 初始事件；
- 无权用户不能通过伪造工作空间 ID 跨空间读取；
- viewer 可在所属工作空间读取，但不能 INSERT Agent；
- 普通数据库角色无法读取 sessions / 隔离历史数据，不能 UPDATE AgentVersion；
- 整个测试事务最后回滚，不保留合成用户或记录。

**尚无 DB2 成功日志。** 测试脚本已经加进 CI，但 CI 未在本地启动。请勿将已有离线及 Mock 测试描述为“真正 PostgreSQL 运行成功”。

**本轮实际自动化验收：** 全部 Python unittest 共 **60/60 项通过**（包括新增 PostgreSQL 仓储契约测试）；PostgreSQL 语法解析器成功解析完整 Alembic 迁移中的 **92 条 SQL 语句**；Ruff lint/format、架构边界规则、前端 ESLint/TypeScript/生产构建均通过。已有 FastAPI→LangGraph Worker 及 Next.js→FastAPI Cookie 登录两条 HTTP 模拟服务链路通过。以上均不等于真实 PostgreSQL DB2 在线验收。

## 6. 如何继续完成真正上线的数据库闭环

1. 启动 Docker Desktop 或选择受控的 PostgreSQL 17 测试实例（建议优先本地 Docker；不应擅自在云端创建付费资源）。
2. 使用 V0.5 文档中的 `infra/postgres/compose.yaml` 与独立测试密码启动 `pgvector/pgvector:pg17`。
3. 在隔离测试库执行 `alembic upgrade head`，再运行 `scripts/smoke_postgres_rls.py`。
4. 如真实数据库测试失败，先修复 **迁移、RLS、角色和事务问题**；不绕过权限来求“绿色测试”。
5. DB2 通过后实现**独立的 PostgreSQL 身份服务账号、DB Repository 接口、任务 Worker 领取/锁/心跳、LangGraph PostgresSaver**；这些组件只有同时通过端到端与崩溃恢复测试后才允许切换 `STUDIO_DATABASE_BACKEND`。
6. 最后再进行 SQLite 旧数据的可信归属核对、备份、分批导入、数据对账与回滚预案。缺少所有权证明的旧记录持续隔离，不能自动归入首位登录用户。

## 7. 正式启动阻断条件

- 未接入正式 Auth/OIDC/MFA、HTTPS、登录限速与审计；
- 普通服务连接使用超级用户/迁移管理员，或者可绕过 RLS；
- PostgreSQL Worker、持久化队列与检查点尚未连通；
- 真实 DB2 跨租户权限和数据隔离测试未通过；
- 数据迁移未经备份、所有权核验、数量及关联对账。

V0.6 的本质交付是**可以进入 PG17 实测的数据库访问实现**，不是已经对公网开放或替代 SQLite 的运行版本。
