# 技术架构与核心决策 ADR

## 定稿主技术栈

- Web：Next.js (App Router) + React + TypeScript + Tailwind；高级画布使用 React Flow（P1）。
- API：Python 3.12/3.13 + FastAPI + Pydantic v2，后续 SQLAlchemy / Alembic。
- Runtime：LangGraph 作为正式 Agent 状态、检查点与 HITL 协作核心；统一 AgentSpec 定义，工具经 Tool Gateway。
- Data：PostgreSQL + pgvector；对象存储用于知识和交付文件；Redis 用于缓存、限流与队列。
- Queue：P0 生产使用独立 Worker + Redis 队列；短任务原型可临时使用同步/后台执行，但不能当作生产级重启恢复。
- Model：模型网关协议化，提供带密钥的实际 provider 接入与速率限制；失败不能静默切回虚构结果。
- Tools：原生结构化函数 + MCP + 受控 API；服务端策略授权 / 幂等检查，不运行任意不可信代码。
- Trace：先事件库 + OpenTelemetry；Langfuse 在有明确评价/运营需要且确认其组件运维成本后启用。
- Sandbox：受限容器和可控网络出口；不得让模型生成代码直接运行在 API 容器进程。
- Deploy：本地 Docker Compose 体验 → 受控测试环境 → 云生产；每环境独立数据库、密钥和日志政策。

## 不选的方案和原因

- 不将 Dify/Coze 整体作为第一版主运行时：为长期 SaaS 的产品控制权、许可、权限和维护复杂度保留独立边界；可以另行评估 fork 成本。
- 不同时实现多套 Workflow/Agent 引擎：正式 Run 状态权威仅 LangGraph + 数据库。
- 不一开始引入 Temporal：长时业务事务需求明确且 Redis Worker/持久化状态无法满足时再评估。
- 不直接使用 LLM 生成并执行任意 JavaScript/Python：以受控 AgentSpec、已注册 Tool 和沙盒替代。

## 运行路径

User → Web → Auth/API → Builder（自然语言到结构化 AgentSpec）→ Validator → Draft Version → Tool Authorization → Run Service（创建任务）→ Worker（计划/工具/验证循环）→ Checkpoint/Event DB → Tool Gateway / Model Gateway → Trace/Eval → Web 输出。

## AgentSpec v1（最小字段与约束）

- Identity：schema_version、agent_id、version、name、description、owner/workspace。
- Goal：task、scope、success_criteria、deliverable schema。
- Context：knowledge sources、citation policy、context budget、source precedence。
- Tools：每工具 id、version、JSON Schema、权限策略、速率/超时。
- State：run/checkpoint 与恢复策略，幂等键。
- Memory：类型（session/workspace/agent/user）、写入审批、来源、TTL 与删除。
- Planning：enabled、模式、最大改计划次数。
- Tool Calling：结构化参数校验与错误策略。
- Permissions：允许、需要审批、拒绝三种决策与策略版本。
- Environment：执行环境能力、资源和网络限制。
- Observability：trace 级别、脱敏、保留周期。
- Evaluation：测试套件/发布门槛。
- Stop Condition：max_steps、max_tools、max_duration、max_cost、no_progress_limit、user cancel。

AgentSpec 通过版本化 JSON Schema/Pydantic 做结构检查。草稿只在确认用户授权后才能使用外部工具。生产运行必须绑定不可变的 agent_version_id 和 policy_version。

## 领域模型 / 数据库

workspaces; users; memberships; agents; agent_versions; runs; run_events; run_checkpoints; tools; tool_connections; knowledge_sources; memory_records; approvals; eval_suites; eval_runs; deployments; artifacts; audit_events。

所有含用户内容的对象关联 workspace_id，应用服务层与数据库行级安全双重校验。Memory/向量检索同样执行权限过滤。凭据密文或密钥引用独立保存，禁止进入 trace 原文。

## API 契约候选

POST /v1/agents/drafts/generate：结构化生成草案。
POST /v1/agents、GET /v1/agents：管理助手。
POST /v1/agents/{id}/versions：创建不可变版本。
POST /v1/agents/{id}/runs：启动执行，使用 idempotency-key。
GET /v1/runs/{run_id}、GET /v1/runs/{run_id}/events：查询状态及事件。
POST /v1/runs/{run_id}/cancel、.../pause、.../resume：后台控制。
POST /v1/approvals/{approval_id}/decision：操作审批。
POST /v1/evaluations/runs：执行版本测试。
POST /v1/agents/{id}/deployments：发布版本。

要求所有响应包含 request_id 与规范错误码，Run 状态至少具 QUEUED、RUNNING、WAITING_INPUT、WAITING_APPROVAL、PAUSED、SUCCEEDED、PARTIAL、FAILED、CANCELED；各终态有原因和最后检查点。

## 安全控制与非功能要求

- 多租户身份认证、工作空间 RBAC/ABAC、工具凭据最小权限。
- 高风险 Tool Gateway 服务端审批，不靠提示词“自觉”。
- Tool schema、参数、URL 与 API 域名白名单检查；外部文字只做数据，不可变更权限策略。
- 写工具具幂等键与审批记录；重复投递不能重复发信或重复支付。
- 速率、预算、时间、步骤、无进展检测都是硬中断。
- 日志、追踪脱敏；默认禁止向 LLM 发送未授权用户资料。
- 备份、灾难恢复、删除策略、模型使用透明度与可审计性均为公测前门槛。
- 灰度、回滚、契约兼容性和数据库迁移走 CI/CD。

## 开发环境说明

当前 WebCodex Runner 的宿主机为 Windows，Python 3.14；LangGraph/生产镜像计划以 Python 3.12/3.13 锁定版本。开发阶段可使用本地 SQLite 验证 Schema，但多租户/事务/pgvector 必须在 PostgreSQL 集成测试中验收。

## 已实施的运行时边界（2026-10-10）

当前已经实际集成 **LangGraph 1.2.14 StateGraph** 与 **langgraph-checkpoint-sqlite 3.1.1**。流程为 prepare → decide → tools / verify / stop；Web/API 把任务放入本地 SQLite 队列，独立单进程 Worker 执行。每个运行的 LangGraph thread_id 绑定 run_id；异常中断后在租约到期时可从最近节点检查点继续。Run/Event 与图检查点分别保存；GET /v1/runs/{id}/checkpoint 返回非敏感执行摘要。

**单机持久化不等于生产级可靠队列。** 当前不存在 PostgreSQL 多租户、Redis 任务队列、费用预算、外部写操作幂等或人工审批，仅允许两个内置只读工具。SUCCEEDED 只表示图产生非空回答，不等于研究结论经过事实与准确性评价。

**安全阻断：** APP_ENV=production 时拒绝启动；开发 API 只接受 loopback 请求；LangGraph 检查点启用严格 msgpack 反序列化。正式安全/沙盒需要独立开发与验收。
