# Agent Studio 本轮实施记录

更新日期：2026-10-10

## 已核验的环境

- WebCodex 项目名称：agent studio
- 起点：目录为空；当时不是 Git 仓库
- Windows Runner；Node 24.14.1 / npm 11.11.0 / Python 3.14.3
- 已安装的本地 Python 包含 FastAPI、Pydantic、Uvicorn 与 HTTPX

## 本轮交付工作

- 需求与项目管理：实施计划、产品范围、技术决策、十二要素验收矩阵、可导入 CSV backlog。
- 服务端：FastAPI API、严格 AgentSpec、SQLite 本地测试存储、工具注册与参数校验、真实模型 API 网关、本地最小执行循环、任务事件。
- 浏览器：Next.js 工作台、自然语言草稿输入、手动草稿、保存 Agent、执行并查看事件、工具目录和能力状态。
- 安全边界：生产模式禁止启动；无模型凭据返回明确失败；仅允许内置只读工具；不允许 model 任意执行代码。
- 自动测试：后端 9 项单测通过；前端 TypeScript 检查和 Next.js 生产构建通过；已生成 npm 锁文件。
- 本地 HTTP 联通冒烟测试通过：API health 返回 200、Next.js 首页返回 200、Agent 创建返回 201、Agent 查询返回 200；测试过程自动停止服务，没有在后台保留长期运行进程。
- 已创建 .github/workflows/ci.yml，真实 GitHub CI 还未触发；需要推送到受控远程仓库后验证。
- 前端浏览器真实点击、模型线上调用、跨进程运行恢复和外部工具仍未达到端到端验收门槛。

## 没有完成的功能

- 还没有统一登录、多租户权限和版本化发布，不能面向公网。
- 还没有 LangGraph checkpoint + Redis worker；SQLite events 不能替代生产级状态恢复。
- 还没有外部 Web/文件/MCP 工具、可治理长期记忆、人工审批、沙盒、评测及配额计费。
- 尚未使用真实用户提供的模型密钥执行在线 LLM 回归测试。
- 不会把测试中 mock 的模型答复称为线上运行证据。

## 里程碑状态

- S0 需求/架构基线：已写入。
- S0 后端骨架：已编写，9 项单元测试通过。
- S0 前端骨架：已编写，TypeScript 与生产构建通过；浏览器实测仍待验收。
- S0 Git 仓库与 CI：本地 Git main 仓库已初始化，已创建 GitHub Actions CI 文件；没有推送远程仓库，线上 CI 尚未触发。
- S1 真实模型试跑：待配置合法模型密钥及网络测试。
- S2–S4：尚未启动。

## 后续迭代次序

1. 在专用开发环境完成合法真实模型测试和实时运行事件流。
2. PostgreSQL + AgentSpec 不可变版本/工作空间授权。
3. LangGraph + Redis worker + checkpoint 与恢复。
4. 外部工具网关、安全审批、知识库与记忆。
5. Eval 发布门槛、零代码体验验证及私有测试部署。
