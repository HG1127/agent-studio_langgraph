# Agent Studio 仓库开发规范（适用于所有代码贡献者及编码代理）

原则：以模块化单体持续演进；采用小步安全重构、明确数据边界和真实测试，不为追求设计模式而增加无必要抽象。

## 变更前先做

1. 阅读 `docs/09_CODE_STANDARDS.md` 和相关模块的 README。
2. 检查当前 Git 工作区及已有测试。一次迭代只处理一个清晰的业务用例。
3. 修改现有公开 API 时保留兼容性，必须补充契约与旧数据迁移测试。
4. 任何第三方写操作默认不可执行；模型或网页输入不能提升权限。

## 必须遵守的模块依赖

- Python `domain`：只含业务实体、规则与类型；禁止导入 API、数据库、LLM、LangGraph。
- Python `application`：用例与业务编排；不直接管理 HTTP 或框架事件。
- Python `infrastructure`：数据库、模型、工具实现；不能依赖 API。
- Python `harness`：LangGraph 执行流程与状态，依赖受控工具与存储接口；不得处理 HTTP 路由。
- Python `api`：薄路由、输入校验、错误映射；不得包含 SQL 或模型执行循环。
- Web `app`：只保留路由入口；`features` 负责业务 UI；`lib` 负责基础 HTTP；`types` 共享接口契约。

## 工作完成前

- Python: `ruff check`、`ruff format --check`、`unittest`、架构边界检查。
- Web: `npm run lint`、`npm run typecheck`、`npm run build`。
- 至少跑一次 `scripts/smoke_process.py`（本地模型协议模拟，不宣称是真实 LLM）。
- 在 `docs/10_IMPLEMENTATION_CHANGELOG.md` 记录实现内容、验证事实、风险与后续事项。
- 无用户指示不得推送远程仓库、部署公网或声称已完成生产级安全验收。

## Auth/Workspace 新增硬规则

- 访问 Agent/Run/版本/事件/Checkpoint 的任何新 HTTP API 都必须使用 Scope/Editor/Manager 依赖，不能只凭资源 ID 或客户端声明的角色访问。
- 业务对象必须带 workspace_id；旧 NULL 数据仅限可信维护操作，不得经用户 API 暴露，也不能进入 Worker 队列。
- 身份 cookie 只能经本地同源 BFF 代理传递，前端不读取 Cookie 原文；禁止将本地密码登录直接当作生产级身份安全实现。

## PostgreSQL / RLS 工程规则（V0.5）

- 主数据库为 PostgreSQL 17 + pgvector；SQLAlchemy 模型与版本化迁移分别存放在 `app/infrastructure/postgres` 和 `services/api/alembic`。
- RLS 运行角色必须 `NOLOGIN`、`NOBYPASSRLS`；普通 API/Worker 不得使用数据库迁移管理员账号。
- PostgreSQL 数据访问需在可信用户和工作空间的同一事务中通过 `SET LOCAL` 赋予上下文；不得只靠前端过滤。
- 旧 SQLite 数据不能自动归属任何用户；迁移前需审计所有权、执行备份和对账。
- 真实 PG 服务、RLS、Worker/checkpoint 与数据迁移未经验证时，`STUDIO_DATABASE_BACKEND=postgres` 必须禁止启动，不能静默使用 SQLite。
- Alembic 离线生成 SQL、ORM 结构测试与未来 CI 配置不等于真实数据库上线；状态披露须准确。

## V0.6 PostgreSQL 适配增量规则

- `app/infrastructure/postgres/scoped_store.py` 只能处理已通过 RLS 的事务内连接，构造时必须验证 `current_user=agentstudio_runtime` 和有认证的 actor/workspace UUID；不得接入迁移管理员 Engine。
- `workspace_transaction()` 使用非超级用户的数据库账号、`SET LOCAL ROLE` 和事务内 `set_config(..., true)`；禁止跨请求共享不受控的 `Connection`。
- Agent + v1、Run + Event1 需要同事务提交或整体回滚；Agent 新版本采用 `SELECT ... FOR UPDATE` 和乐观版本冲突条件。
- Alembic 完整生成 SQL 必须通过 `pglast` PostgreSQL 语法解析；这不能替代 DB2 的真实 PostgreSQL 执行与权限测试。
- Postgres Adapter 仍是独立 staging 模块，未经真实 PG17 身份/队列/Checkpoint 验收不能向普通用户开放。
