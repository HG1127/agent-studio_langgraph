# LangGraph S1 运行时改造交付记录

日期：2026-10-10

## 一、阶段结论

在不 Fork LangGraph 源码的前提下，将原先 FastAPI 请求内的同步临时循环替换为独立后台 Worker + LangGraph StateGraph。已有 Next.js 工作台保留并扩展 Run 监控和控制入口。

**交付等级：本地开发验证版。** 不能将本轮完成视为 G1 的正式验收，因为用户尚未授权实际大模型 API，未完成前端真实浏览器点击测试及生产身份/安全体系。

## 二、已经实际编写的模块

| 模块 | 路径 | 实际能力 |
| --- | --- | --- |
| StateGraph Harness | services/api/app/runner.py | prepare → decide → tools → decide / verify / stop；结构化工具执行、边界检查、可观察事件 |
| 本地检查点 | langgraph-checkpoint-sqlite + db.checkpoint_path() | 每个 Run 独立 thread_id，按节点持久化；崩溃后基于 last checkpoint 恢复 |
| 本地任务队列 | services/api/app/db.py | 任务创建、单 Worker 原子领取、租约、心跳、到期重排、幂等事件 |
| 独立执行进程 | services/api/app/worker.py | 独立轮询，Worker 状态/任务 lease 心跳，API 不再直接执行耗时任务 |
| Run API | services/api/app/main.py | 创建/查询、after_seq 事件、检查点摘要、pause/resume/retry/cancel |
| 模型网关 | services/api/app/llm.py | OpenAI 兼容 Chat Completions、模型密钥检查、模型调用超时 |
| Web 运行中心 | apps/web/app/page.tsx | Worker 状态、任务轨迹、检查点摘要、暂停/恢复/取消/重试 |
| 回归测试 | services/api/tests/test_api.py | 临时数据库的自动测试，覆盖恢复、权限、控制和边界 |
| 跨进程 Smoke | scripts/smoke_process.py | 真正启动独立 API/Worker 进程，通过本地模拟 provider 验证 HTTP→Graph→Tool→Result |

## 三、验收记录

- 后端自动测试：23 项通过，包括模拟 Worker 意外退出后从检查点恢复，不重复已完成的只读工具步骤。
- 跨进程 Smoke：FastAPI 200 / Agent POST 201 / Run POST 202 / 独立 Worker SUCCEEDED；结果事件含工具返回值，SQLite checkpoint 存在。
- 前端：Next.js 16.4.0 TypeScript 校验与生产构建通过。
- 安全：开发 API 拒绝非 loopback 客户端；生产模式仍阻止启动；模型密钥不写入仓库；工具只支持 utc_now / add_numbers。
- 已验证的 API 端到端使用 **本地模型协议模拟器**，不是正式大模型供应商；未测试真实费用、限速、模型拒答和真实联网研究。
- 尚未推送远程仓库、部署生产或验证真实浏览器 UI 点击；本地 Git 提交情况以最终项目状态为准。

## 四、持久状态和失败处理

| 状态/事件 | 说明 |
| --- | --- |
| QUEUED | API 持久化任务，等待 Worker；用户离开页面不影响 |
| RUNNING | Worker 原子领取并建立租约，执行 LangGraph |
| PAUSE_REQUESTED / PAUSED | 用户请求暂停；Worker 在可控安全边界确认 |
| CANCEL_REQUESTED / CANCELED | 取消优先于完成终态；不能强制瞬时杀死在途模型 HTTP 请求 |
| FAILED | 无密钥、模型错误或执行异常；可显式 retry（有限次数） |
| PARTIAL | 超时、最大模型轮次、工具数量或重复调用边界触发 |
| SUCCEEDED | 图产生非空最终结果；**仅表示技术执行完成，不代表内容准确率或业务 Eval 通过** |
| worker_recovered | 单机 Worker 租约过期重排，从持久图状态恢复 |
| checkpoint_resumed | 继续未完成 LangGraph 节点（非从头开始） |

检查点与 Run/Event 数据分别保存在本地 SQLite 文件里。只读工具若正好在“执行完成但 checkpoint 尚未提交”的崩溃窗口，可能被重放；因此本阶段**禁止外部写操作**。邮件、支付、交易及数据库更新工具必须待幂等与审批服务实现后再接入。

## 五、明确未实现

1. PostgreSQL、事务隔离、行级安全、正式用户身份/工作空间授权。
2. Redis / Temporal 级别多进程生产调度、分布式租约与自动扩容。
3. 真实外部搜索 API、文件/PDF 解析、MCP/OAuth、知识/长期记忆。
4. 模型 token/费用计量、预算硬限制、自动 Eval 与灰度发布门槛。
5. 复杂自主重规划及可视化拖拽工作流（当前仅基本执行图与模型工具循环）。
6. 高风险工具人工审批、沙盒、数据脱敏治理与公网部署。
7. 真实供应商 LLM 联机验证、真实浏览器端到端验收。

## 六、下一阶段最小依赖顺序

**优先 A：用用户授权的真实模型完成联机验证。** 不能将模型协议模拟器等同于实际服务。

**优先 B：建立 PostgreSQL + 不可变 Agent 版本 + Workspace Auth。** 解决使用者隔离和线上访问的安全前提。

**优先 C：扩展 Tool Gateway 与独立沙盒。** 先接只读联网搜索/PDF/文件，具备来源与引用；延后外部写工具。

**优先 D：升级多 Worker / Redis 或适合的 durable workflow 编排。** 与数据库租约、审批中断、事件流、幂等一起验证。

**优先 E：正式 Eval / Token/费用 / 版本发布。** 通过质量门槛才能对真实用户开放。
