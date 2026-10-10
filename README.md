# Agent Studio

目标：让非技术人员通过自然语言，创建、调试、部署和运营真正能完成任务的 AI 助手。

## 项目状态（2026-10-10）

这是从空项目启动的绿地工程。当前先建设开发基线，不把原型或未配置的模型调用认定为生产级 Agent。

- 需求基线：Agent Studio V3.0（对话中确认的产品方向）
- 交付节奏：阶段 0 工程基础 → 阶段 1 首条真实执行闭环 → 阶段 2 P0 内测 → 阶段 3 商业化公测
- 当前技术：Next.js / TypeScript + FastAPI / Python + LangGraph StateGraph + SQLite Checkpoint + 独立单机 Worker；未来迁移 PostgreSQL / Redis / MCP。
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
```

打开 http://localhost:3000；前端默认调用 http://127.0.0.1:8000。工作台会提示模型和 Worker 是否在线，并提供任务暂停、取消、重试和查看执行轨迹的操作入口。

### 5. 真实模型连接

在启动 API **和** Worker 之前，分别在两个终端设置模型凭据，例如（仅为环境变量名称示例，不包含真实密钥）：

```powershell
$env:MODEL_API_KEY="your-own-provider-key"
$env:MODEL_BASE_URL="https://api.openai.com/v1"
$env:MODEL_NAME="your-authorized-model"
```

配置后需重启相应进程。本项目调用实际的 OpenAI 兼容 Chat Completions API；没有凭据时，AI 草稿返回 503，任务返回 FAILED 并保留事件记录，不会生成模拟成功。当前只包含 UTC 时间与浮点加法两种已注册只读工具。**不要使用当前开发版处理真实敏感数据或将服务开放到公网。**

### 6. 自动化验证

- 后端（services/api）：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
- 跨进程集成（services/api）：`.\.venv\Scripts\python.exe ..\..\scripts\smoke_process.py`
- 前端（apps/web）：`npm run typecheck`、`npm run build`
- 完整质量门禁（services/api）：先安装 `requirements-dev.txt`，运行 `ruff check .`、`ruff format --check .` 以及 `python ../../scripts/check_architecture.py`
- 前端规范（apps/web）：`npm run lint`、`npm run format:check`

跨进程集成测试会启动一个独立 Worker 和临时的**模型协议模拟服务器**，用于验证真实 HTTP 通信、队列、LangGraph 和工具调用链路；它不是与真实大模型供应商的联机验收。

## 当前已实现 / 未实现

已实现（本地开发版）：12 要素 AgentSpec 结构和校验；模块化领域/API/持久化/Harness/前端结构；不可变 Agent 版本、乐观锁和历史 Run 配置快照；OpenAI 兼容模型接入接口；只读 Tool Registry；LangGraph StateGraph；分离 Worker/SQLite 队列与检查点；运行状态恢复/暂停/取消/失败重试、事件追踪及 Web 工作台。真实外部模型仍需要用户自行授权配置凭据。

未实现（不能上线）：身份认证、多租户、PostgreSQL、生产级 Redis 队列、多 Worker 并发控制、Agent 版本的正式发布/回滚、MCP/OAuth、外部联网搜索、文件知识库、长期记忆、人工审批、沙盒、完整 Eval 与计费。SQLite 检查点仅支持本地单机开发；SUCCEEDED 代表执行流程完成，并不代表事实准确性通过业务评测。

## 下一步

下一步：使用获授权的真实模型验证、正式 Workspace/Auth、PostgreSQL/Alembic 迁移（本地 SQLite 不可变版本已完成）、MCP 工具网关和生产级任务调度。未经安全验收不得公开部署。
