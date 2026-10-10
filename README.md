# Agent Studio

目标：让非技术人员通过自然语言，创建、调试、部署和运营真正能完成任务的 AI 助手。

## 项目状态（2026-10-10）

这是从空项目启动的绿地工程。当前先建设开发基线，不把原型或未配置的模型调用认定为生产级 Agent。

- 需求基线：Agent Studio V3.0（对话中确认的产品方向）
- 交付节奏：阶段 0 工程基础 → 阶段 1 首条真实执行闭环 → 阶段 2 P0 内测 → 阶段 3 商业化公测
- 主技术路线：Next.js / TypeScript + FastAPI / Python + LangGraph（正式运行时）+ PostgreSQL + Redis + MCP
- 原则：只有真实模型与真实工具调用才标记为“已运行”；没有凭据时必须明确提示“待配置”。

## 核心资料

- docs/00_IMPLEMENTATION_PLAN.md：落地里程碑、分工、依赖、进度关口
- docs/01_PRODUCT_SCOPE.md：用户旅程、页面、MVP 范围及产品指标
- docs/02_ARCHITECTURE.md：技术选型、业务实体、安全与执行状态
- docs/03_ACCEPTANCE_MATRIX.md：12 个 Agent 要素和端到端验收
- docs/04_BACKLOG.csv：可导入项目管理工具的任务清单

## 约定

- apps/web 为浏览器工作台，services/api 为后端。
- 生产环境禁止演示数据伪装成真实执行。
- 开发阶段的本地 SQLite/内存测试不代表 PostgreSQL 多租户或分布式调度已经完成。
- 测试及审核通过后才能标记里程碑完成。
- 未经明确授权不得部署线上或推送到远程 Git 仓库。

## 本地启动（Windows PowerShell）

### API（终端 1）

1. cd services/api
2. 推荐使用 Python 3.12 / 3.13 虚拟环境；开发机器的 Python 3.14 若依赖已满足也可运行。
3. python -m pip install -r requirements.txt
4. python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

打开 http://127.0.0.1:8000/docs 查看 API 文档，http://127.0.0.1:8000/v1/health 查看运行状况。

### Web（终端 2）

1. cd apps/web
2. npm install
3. npm run dev
4. 打开 http://localhost:3000

前端默认请求 http://127.0.0.1:8000；如需修改服务地址，在前端运行环境设置 NEXT_PUBLIC_API_BASE_URL。不要将网站公开部署。

### 真实模型连接

在启动 API 进程前设置 MODEL_API_KEY（使用系统环境或秘密管理器，而不是提交到 Git），必要时设置 MODEL_NAME、MODEL_BASE_URL（默认 OpenAI 兼容 chat completions 接口）。模型未连接时：AI 草稿返回 503；Run 记录 FAILED，绝不会伪造成功。工具目前仅 utc_now 与 add_numbers。

### 自动化验证

- 后端：在 services/api 运行 python -m unittest discover -s tests -v
- 前端：在 apps/web 运行 npm run typecheck 和 npm run build

## 当前已实现 / 未实现

已实现（本地样板）：AgentSpec 的十二字段结构化配置、基本校验、保存/查看 Agent、真正的模型 API 适配接口、只读 Tool Registry、基础工具调用事件和本地 SQLite 状态、Web 操作界面及后端自动测试。真实外部模型测试需要你自行配置有效密钥。

未实现（不能上线）：身份认证、多租户、PostgreSQL、LangGraph 持久化 Checkpoint、可靠队列、Agent 版本发布、MCP/OAuth、外部搜索/文档知识库、长期记忆、审批、沙盒、全面评测与计费。运行状态 SUCCEEDED 只代表模型执行完成，不代表结果通过业务质量验收。

## 下一步

按 docs/00_IMPLEMENTATION_PLAN.md 的 G0/G1 顺序继续，验收通过后再开始封闭内测功能；不要用当前样板承载用户真实隐私资料。
