# Agent Studio

目标：让非技术人员通过自然语言，创建、调试、部署和运营真正能完成任务的 AI 助手。

## 项目状态（2026-10-10）

这是从空项目启动的绿地工程。当前先建设开发基线，不把原型或未配置的模型调用认定为生产级 Agent。

- 需求基线：Agent Studio V3.0（对话中确认的产品方向）
- 交付节奏：阶段 0 工程基础 → 阶段 1 首条真实执行闭环 → 阶段 2 P0 内测 → 阶段 3 商业化公测
- 当前运行：Next.js、FastAPI、LangGraph 与 SQLite 本地 Worker。宝塔独立 PostgreSQL 17 + pgvector 已通过真实 Alembic 初始迁移（12 张业务表）及合成数据回滚的 RLS/Agent/Run/任务租约集成测试；当前仍**未**接管 API、Worker 或 LangGraph Checkpoint。专用低权限数据库登录账号处于待用户确认创建的 staging 阶段；Redis、MCP 后续迭代。
- 原则：只有真实模型与真实工具调用才标记为“已运行”；没有凭据时必须明确提示“待配置”。

## 核心资料

- docs/00_IMPLEMENTATION_PLAN.md：落地里程碑、分工、依赖、进度关口
- docs/01_PRODUCT_SCOPE.md：用户旅程、页面、MVP 范围及产品指标
- docs/02_ARCHITECTURE.md：技术选型、业务实体、安全与执行状态
- docs/03_ACCEPTANCE_MATRIX.md：12 个 Agent 要素和端到端验收
- docs/04_BACKLOG.csv：可导入项目管理工具的任务清单
- docs/08_LANGGRAPH_IMPLEMENTATION.md：本轮 LangGraph 改造与验证/限制明细
- AGENTS.md：代码贡献与编码 Agent 操作规则
- docs/09_CODE_STANDARDS.md：模块架构、代码组织、质量门禁与安全规范
- docs/10_IMPLEMENTATION_CHANGELOG.md：本轮重构与验收记录
- docs/11_WORKSPACE_AUTH.md：本地认证、工作空间隔离与安全剩余项
- docs/12_POSTGRES_DATABASE_V05.md：PostgreSQL 17 + pgvector 选型、迁移、RLS 与本地安装测试
- docs/13_POSTGRES_V06_ADAPTER.md：PostgreSQL 仓储适配、迁移修复、可重复测试和剩余切换条件
- docs/14_BAOTA_PG17_TUNNEL_MIGRATION.md：宝塔 PostgreSQL 17 的 Windows SSH 隧道、只读预检、确认迁移和真实 RLS 测试
- docs/19_PG_RUNTIME_ROLE_SETUP.md：真实数据库 RLS 通过后的独立非管理员运行账号、权限验证与后续安全切换条件

## 约定

- apps/web 为浏览器工作台，services/api 为后端。
- 生产环境禁止演示数据伪装成真实执行。
- 开发阶段的本地 SQLite/内存测试不代表 PostgreSQL 多租户或分布式调度已经完成。
- 测试及审核通过后才能标记里程碑完成。
- 未经明确授权不得部署线上或推送到远程 Git 仓库。

## 本地启动（Windows PowerShell）
以下命令均从本项目的 **Git 仓库根目录** 开始；每打开一个新终端，先切换到仓库根目录。这样不依赖任何个人电脑的绝对路径。
### 1. 后端环境（首次安装）

```powershell
cd services/api
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

建议 Python 3.12/3.13；本轮在 Windows Python 3.14 环境中通过安装和测试。真实运行需在 **API 和 Worker 两个进程**的环境里都设置 MODEL_API_KEY；如果使用兼容供应商，另设置 MODEL_BASE_URL / MODEL_NAME。不要把密钥提交到 Git。

### 2. API 服务（终端 1）

```powershell
cd services/api
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

API 文档：http://127.0.0.1:8000/docs；健康检查：http://127.0.0.1:8000/v1/health

### 3. LangGraph Worker（终端 2，必须独立运行）

```powershell
cd services/api
.\.venv\Scripts\python.exe -m app.worker
```

Worker 从数据库领取任务并执行 StateGraph；与浏览器页面和 API 进程相互独立。关闭浏览器不影响正在运行的 Worker。退出 Worker 后，RUNNING 任务的租约到期会重新排队，由下一个 Worker 从 LangGraph 检查点恢复。当前仅支持一个本地 Worker，检查点采用 SQLite，尚不是生产级多实例方案。

### 4. Web 工作台（终端 3）

```powershell
cd apps/web
npm ci
npm run dev
# 已强制仅监听 127.0.0.1；严禁开放本地代理至公网
```

打开 http://localhost:3000。首次使用通过邮箱与至少 12 位密码注册个人工作空间，后续登录进入 Studio。前端以同源 /backend 代理访问本地 API，HttpOnly Cookie 不暴露给浏览器 JavaScript。若后端改用其他端口，例如 8019，请在启动 Next.js 前设置 `$env:STUDIO_LOCAL_API_PORT='8019'`。切换工作空间时后台按真实成员权限隔离各自的 Agent、版本、Run。

### 5. 真实模型连接

在启动 API **和** Worker 之前，分别在两个终端设置模型凭据，例如（仅为环境变量名称示例，不包含真实密钥）：

```powershell
$env:MODEL_API_KEY="your-own-provider-key"
$env:MODEL_BASE_URL="https://api.openai.com/v1"
$env:MODEL_NAME="your-authorized-model"
```

配置后需重启相应进程。本项目调用实际的 OpenAI 兼容 Chat Completions API；没有凭据时，AI 草稿返回 503，任务返回 FAILED 并保留事件记录，不会生成模拟成功。当前只包含 UTC 时间与浮点加法两种已注册只读工具。**不要使用当前开发版处理真实敏感数据或将服务开放到公网。**

### 6. 自动化验证

- Next.js 同源 Cookie HTTP 集成（services/api）：`\.\\.venv\\Scripts\\python.exe ..\\..\\scripts\\smoke_web_proxy.py`（隔离临时数据库；要求 3000 端口空闲）

- 后端（services/api）：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
- 跨进程集成（services/api）：`.\.venv\Scripts\python.exe ..\..\scripts\smoke_process.py`
- 前端（apps/web）：`npm run typecheck`、`npm run build`
- 完整质量门禁（services/api）：先安装 `requirements-dev.txt`，运行 `ruff check .`、`ruff format --check .` 以及 `python ../../scripts/check_architecture.py`
- 前端规范（apps/web）：`npm run lint`、`npm run format:check`

跨进程集成测试会启动一个独立 Worker 和临时的**模型协议模拟服务器**，用于验证真实 HTTP 通信、队列、LangGraph 和工具调用链路；它不是与真实大模型供应商的联机验收。

## PostgreSQL 数据库准备（V0.5）

主数据库已选定 **PostgreSQL 17 + pgvector**。目前仅完成数据模型、Alembic 初始迁移、RLS 定义、SQLite 旧数据只读检查和开发容器配置。Web/API/Worker 仍使用原 SQLite，真实 PostgreSQL 服务与权限尚未在本地验收。

如需进行 PostgreSQL 在线验证，先打开 Docker Desktop、为 `infra/postgres/.env` 配置本地随机密码，再按 `docs/12_POSTGRES_DATABASE_V05.md` 启动容器、执行 Alembic 并运行真实 RLS 测试。数据库连接信息不得写入 Git。

`STUDIO_DATABASE_BACKEND=postgres` 在运行时适配完成之前将明确拒绝启动，不会无提示回退到 SQLite。

### V0.6 适配进展

PostgreSQL 初始迁移中 `DO $$` 结束标记错误已修复，并且完整 Alembic SQL 纳入 `pglast` 解析门禁。新增 `app/infrastructure/postgres/scoped_store.py`，实现事务作用域内的 Agent 创建/查询、不可变版本、Run 创建与初始事件及列表查询；`transaction.py` 严格拒绝管理员、BYPASSRLS 角色和未授权 UUID Scope。以上**尚未切换到 API 和 Worker**，因为本机没有可连接的真实 PG17 实例；详见 `docs/13_POSTGRES_V06_ADAPTER.md`。


## 当前已实现 / 未实现

已实现（本地开发版）：12 要素 AgentSpec 结构与校验、模块化前后端、不可变 Agent 版本、LangGraph+SQLite 检查点与本地 Worker；新增加 localhost 邮箱/密码登录、哈希 Cookie 会话、个人及多个工作空间、owner/admin/member/viewer 角色，按空间过滤 Agent/Run/执行事件/Checkpoint，并隔离历史无主数据。真实外部模型仍需授权配置。

未实现（不能上线）：生产级托管身份认证与 HTTPS、MFA、密码找回、登录限速、审计、PostgreSQL/RLS、Redis 多 Worker、发布/回滚、MCP/OAuth、联网搜索、知识库、长期记忆、审批、隔离沙盒、Eval 与计费。本地 SQLite 空间级权限检查不等于生产级多租户安全验收。

## 下一步

下一步：PostgreSQL + Alembic/RLS、强化正式身份与安全审计、真实模型授权及费用计量、MCP/搜索只读工具和生产级任务调度；未经验收不得公开部署。