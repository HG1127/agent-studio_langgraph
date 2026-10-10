# Agent Studio 本轮架构重构与版本管理实施记录

日期：2026-10-10 · 目标：在本地可运行 S1 版本上，执行工程结构规范、重构和不可变 Agent 版本。

## 1. 交付范围（已编写与验证）

- 新增 `AGENTS.md`、`.editorconfig`、`docs/09_CODE_STANDARDS.md`；明确架构边界、命名、测试和发布阻断条件。
- 新增 `scripts/check_architecture.py`：基于 AST 检查 Python domain/infrastructure/harness/application 的违规层依赖，并要求 Next.js `app/page.tsx` 为薄路由。
- 后端重构为 `domain`、`application`、`infrastructure/sqlite`、`api/routes` 和 `harness`，保持 `app.db` / `app.runner` 兼容 facade；没有更换原有 LangGraph StateGraph。
- `app/main.py` 只装配 FastAPI 和安全中间件；路由按 agents/runs/system 组织；前端拆分为 `src/features/agents`、`src/features/runs`、`src/features/tools`、`src/features/studio` 和统一 `src/lib/api.ts`、`src/types/studio.ts`。
- 新增 SQLite `agent_versions` 不可变记录，含 UPDATE/DELETE 阻断触发器、乐观锁 `expected_version`、冲突 409 和缺失 404；旧库幂等回填、历史 Run 保留。
- Run 创建与取 Agent 当前版本的过程在 SQLite 单事务中完成；每条新 Run 绑定具体 `agent_version_id` 和 `spec_snapshot`，更新 Agent 不影响正在排队或执行中的 Run。
- Web 的 Agent 详情可以查看不可变版本历史、更新目标并提交新的配置版本；不是“公开发布/灰度发布”功能。
- 新增后端 Ruff 静态检查与格式配置、前端 ESLint/Prettier 与 CI 规则，构建与测试在本地执行。

## 2. 自动验收事实

| 项目 | 实际执行或验证 | 结果 |
| --- | --- | --- |
| 后端回归 | Python unittest discover（包含新增 Agent 版本与历史迁移用例） | 31 项通过 |
| 历史 SQLite 迁移 | 创建旧表与旧 Run、重复 initialize、验证回填与原历史 Run | 通过 |
| 版本并发控制 | v1 → v2、过期 v1 更新返回 HTTP 409 | 通过 |
| 版本不可变性 | UPDATE/DELETE agent_versions 被 SQLite 触发器拒绝 | 通过 |
| Run 快照 | v1 Run 排队后创建 v2，Run 仍绑定 v1 配置及版本 ID | 通过 |
| 跨进程 Smoke | API → SQLite → 独立 Worker → LangGraph → 已注册工具 → Checkpoint | 通过 |
| 架构边界 | Python AST 依赖检查、Next 页面薄入口 | 通过 |
| Python 质量 | Ruff lint + format --check | 通过 |
| Web 质量 | ESLint、Prettier、TypeScript、Next.js production build | 通过 |

**注意：** Smoke 中的大模型接口是本地 HTTP 协议模拟器，并不是实际供应商调用。GitHub Actions 在未推送远程前未运行；未执行真实浏览器用户操作 E2E、负载测试或生产级安全评估。

## 3. 不包括的能力和上线阻断

- 正式用户登录、RBAC、工作空间及多租户隔离；当前本地 API 无认证，仍只允许 loopback 请求。
- PostgreSQL/Alembic 与生产级版本部署机制。当前 Agent 版本仅在 SQLite 单机开发数据库中落地。
- Redis Worker、正式分布式租约、幂等外部写工具、审批、沙盒。
- 实际 LLM 供应商联机、模型 token/费用计量、长期记忆、联网搜索/PDF/MCP、正式 Eval 与可视化画布。
- 真实浏览器 UI E2E 和无障碍验收；目前只有前端静态检查与 production build。

没有上述认证隔离与安全能力，**严禁公网部署**。

## 4. 推荐下一步

按照以下依赖推进：`正式 Workspace/Auth` → `PostgreSQL + Alembic 不可变版本迁移` → `真实模型授权接入` → `受控 Tool Gateway` → `生产级 Worker 与幂等审批` → `Eval/发布与计费`。每个阶段做真实数据迁移与回归，不要在未验收时宣称生产可用。

## 2026-10-10 v0.4 本地工作空间权限实施补充

新增 localhost 本地邮箱/密码认证、scrypt 密码哈希、不可逆会话令牌摘要、HttpOnly SameSite=Strict Cookie、个人与多个工作空间，以及 owner/admin/member/viewer 访问规则。服务端按 workspace 过滤 Agent、版本、Run、事件和检查点，未归属的旧资料保留但隔离。前端加入登录和空间切换，并使用 Next.js 同源 BFF 代理。

新增后端多用户/跨空间/权限/Session 生命周期负向测试，且保留 LangGraph 独立 Worker 集成；Next→FastAPI 同源代理的 HTTP Cookie 链路独立验证（非真实浏览器点击/非正式安全评估）。完整测试数量及最终代码提交以本轮交付结果为准。详情：docs/11_WORKSPACE_AUTH.md。

## 2026-10-10 v0.5 PostgreSQL 数据库底座实施补充

选择 PostgreSQL 17 + pgvector 为最终业务主数据库。新增 SQLAlchemy ORM 模型（12 张表）、第一版 PostgreSQL 专用 Alembic 迁移、JSONB AgentSpec、不可变版本触发器、工作空间复合外键、RunEvent 只追加约束、RLS 角色及权限策略。未来检索使用 pgvector，Redis 单独处理队列/缓存，文件交由对象存储。

工程新增 PG17 开发 Docker Compose、离线迁移测试、事务内 SET LOCAL 范围帮助函数、SQLite 旧数据只读统计工具及仅限本地数据库的 RLS 真实集成测试脚本。CI 新增 pgvector/pg17 容器真实 RLS 测试 Job，尚未推送远程执行。数据库角色受 NOLOGIN/NOBYPASSRLS 限制，不授权普通运行角色访问会话/用户/隔离旧数据。

目前 Docker Desktop Linux Engine 在目标 Windows 环境中未启动，未执行真正 PG 建表或 RLS 在线测试。原 FastAPI、Next.js 和 LangGraph Worker **仍使用 SQLite**；设置 STUDIO_DATABASE_BACKEND=postgres 会 fail-closed 而非假装已经完成迁移。需按照 docs/12_POSTGRES_DATABASE_V05.md 完成 DB2–DB5 实际验收后切换。

本地测试记录：Python 50/50 项通过（含新增 PG schema、Alembic offline、权限配置与安全迁移评估）；已生成可供 DBA 检查的离线 PG17 建库 SQL；Docker Compose config、Ruff、架构约束、Next ESLint 与 build 通过。GitHub Actions postgres-rls-integration Job 已编写但未经远程执行，因此不能声称在线 PG RLS 测试已通过。

## 2026-10-10 v0.6 PostgreSQL 适配器与 SQL 安全修复

修复 PostgreSQL Alembic 初始版本中 `END $;` 少一个分隔符的语法错误；新增 `pglast` 依赖，在 CI 用 PostgreSQL 解析器直接检查整份升级 SQL（此前仅字符串包含检查可漏过错误）。

新增 `app/infrastructure/postgres/scoped_store.py`，实现当前工作空间内 Agent、AgentVersion、Run、初始 RunEvent 的事务内 SQLAlchemy CRUD 和乐观版本控制，封装清晰的业务范围过滤；在 `transaction.py` 强制验证非管理员数据库登录，使用 `SET LOCAL ROLE agentstudio_runtime` 和可信 UUID 会话作用域。新增 Mock/SQL 编译层面的事务、版本、权限负向测试，并扩展未来真正 PostgreSQL CI smoke 的 owner / viewer / Cross-Workspace 场景。

现有 SQLite API/Worker **保持运行并拒绝未经验证的 PostgreSQL 后端标志**。由于此 Runner Docker Engine 未安装/不可连接，真实 PG17 RLS、实际迁移、身份服务切换、Worker/Checkpoint 与旧数据库导入仍未验收。详见 `docs/13_POSTGRES_V06_ADAPTER.md`。

本地最终测试：Python unittest 60/60 项通过；Alembic 整份迁移 SQL 共 92 条语句通过 PostgreSQL 语法解析；Ruff、架构边界、前端 ESLint + Next.js 生产构建及两条原有 HTTP 模拟服务 Smoke 验证通过。CI PostgreSQL 真机集成测试额外覆盖 PgScopedStore 的 Agent v1→v2、Run/Event、viewer 禁止写与跨工作空间隔离；但尚无真实 PG 在线日志，DB2 保持未验收。
