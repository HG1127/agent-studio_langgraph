# Agent Studio 工程代码结构与质量规范 V1.0

> 目标：构建能由多人长期维护、稳定迭代的 Agent Studio。首先是明确边界和可自动执行的检查，而非追逐“最新框架”。本规范适用于当前 FastAPI + LangGraph + Next.js 项目。

## 1. 架构：模块化单体（Modular Monolith）

第一阶段不拆微服务；API 与 Worker 为两个进程，共用领域模型和存储接口。一个功能以单一业务模块纵向落地，而不是把全站逻辑写进入口文件。业务边界：
- **Agent 定义与版本**：AgentSpec、不可变版本、并发更新冲突；
- **Run 生命周期**：任务队列、状态转换、事件、检查点；
- **Harness**：StateGraph、模型推理、Tool Calling、Stop；
- **工具连接**：工具注册、授权、Schema，默认只读；
- **用户/工作空间**：后续实现身份与租户隔离，届时所有数据必须 scoped；
- **Web 工作台**：Agent 创建、版本查看、执行监控和工具状态。

## 2. 目录规划（示例为本轮实现目标）

```text
agent studio/
├── AGENTS.md                        # 工程规则（人和编码 Agent 共用）
├── .editorconfig                    # 换行/缩进规范
├── .github/workflows/ci.yml        # 可执行质量门禁
├── docs/
│   ├── 09_CODE_STANDARDS.md
│   └── 10_IMPLEMENTATION_CHANGELOG.md
├── scripts/
│   ├── check_architecture.py       # Python 模块依赖静态检查
│   └── smoke_process.py
├── services/api/
│   ├── app/
│   │   ├── main.py                  # 应用装配与安全中间件
│   │   ├── api/routes/              # system.py / agents.py / runs.py
│   │   ├── domain/                  # specs.py / errors.py
│   │   ├── application/             # agent_service.py
│   │   ├── infrastructure/sqlite/   # connection, migrations, agents, runs, events, workers
│   │   ├── harness/                 # graph.py / executor.py
│   │   ├── db.py                    # 旧接口兼容 facade（过渡期）
│   │   ├── runner.py                # 旧接口兼容 facade（过渡期）
│   │   ├── llm.py / tools.py        # 独立受控集成
│   │   └── worker.py                # 单机开发 Worker 入口
│   └── tests/                       # 测试在每个边界验证，模型模拟仅限测试
└── apps/web/
    ├── app/                         # Next.js 路由入口和根样式
    └── src/
        ├── lib/api.ts               # 统一 HTTP、错误边界
        ├── types/studio.ts          # 后端契约类型
        ├── features/agents/         # 创建、保存、不可变版本
        ├── features/runs/           # 执行状态与检查点
        ├── features/tools/          # 工具与能力
        └── features/studio/         # 工作台入口、导航与组合
```

## 3. 模块依赖约束

`domain` 不允许导入 `api`、`infrastructure`、`application`、`harness`、LLM 或 FastAPI；`infrastructure` / `harness` 不导入 API；API 路由不能直接执行 SQL 或调用模型 HTTP 客户端；Web `app/page.tsx` 不承担所有状态和业务 UI。

公开接口及工具调用通过版本化 Schema。数据库替换时保留 application/domain 契约；不允许在 Agent 节点里直接修改数据库表结构。禁止循环依赖，禁止全局可变权限策略。

## 4. 风格、类型与命名

- Python ≥3.12，4 空格缩进，UTF-8，`snake_case` 函数、`PascalCase` 类，类型标注；Ruff 负责格式、导入排序、静态检查。公共接口必须有清晰错误语义。
- TS 严格模式，不用 `any` 绕过类型校验（无法确定类型时用 `unknown` 并验证）；`PascalCase` 组件、`camelCase` 方法、`*.tsx` UI 组件、`*.ts` 无 JSX 逻辑。
- 组件只负责展示/交互，API 网络请求统一 `src/lib/api.ts`；不在 React render 中进行网络调用。
- 保持单文件适中、职责清楚；出现超过约 300 行的混合职责模块应进一步拆分，避免为了硬性行数牺牲可读性。
- 每个接口/实体有一个权威定义，避免前后端手工复制大量不一致的业务约束；后续引入 OpenAPI 类型生成。

## 5. 数据、版本与迁移

- 每个 Agent 具备不可变 `agent_versions`，Run 绑定创建时的具体版本，并持有执行配置快照。
- 更新 Agent 必须显式提交新版本，使用 `expected_version` 乐观锁，旧版本不得被 UPDATE 覆盖。
- SQLite 初期沿用幂等迁移并测试既有数据；正式多租户 PostgreSQL 必须使用独立迁移工具、租户隔离/事务测试，不能将 SQLite 结果当生产级证明。
- 数据库修改必须有升级与旧数据回填测试；失败需回滚，不能静默变更历史 Run 归属。
- 状态、持久化 checkpoint 和审计事件不能混为一份结构；用户隐藏、删除记忆需将来制定独立治理策略。

## 6. 安全

- 当前单机开发版本拒绝公网运行；没有认证、租户隔离/审批/安全审查前不得部署生产。
- 所有模型/工具执行结果需要标注来源与成功范围；`SUCCEEDED` 不代表业务正确率通过 Eval。
- 工具按服务端 allowlist + JSON Schema 执行；只读工具可在开发版本复演，写工具必须独立审批与幂等。
- 秘钥仅进入受保护的环境/Secret Store，禁止出现在源码、trace、构建产物或客户端 Bundle。

## 7. 单元、集成、契约与端到端验收

- 单测验证领域规则、版本冲突、迁移回填和任务状态；模拟 LLM 只限测试，不能伪装成线上能力。
- 集成测试验证 FastAPI → Run Queue → 独立 Worker → LangGraph → 真实已注册只读工具 → Checkpoint；现有 Smoke 使用协议 Stub，单独标注。
- 前端运行 ESLint、TypeScript、Next production build；浏览器自动点击与可访问性验证仍需要单独补齐。
- PR 准入：后端单测、Ruff、架构依赖、前端 lint/typecheck/build、Smoke 全部通过；无新敏感密钥、无无授权外部写入。
- 通过一次 green build 不代表生产安全、真实供应商模型联机通过，验收报告必须区分。

## 8. 代码评审清单

验证业务契约和边界；数据库是否保持原子性；版本是否不可变；模型和外部输入是否低信任；请求失败/冲突/重试/取消是否测试；是否添加不必要抽象；是否重复造框架已有能力；日志是否泄露敏感信息；测试是否可独立重复且 CI 真正执行。

## 9. 本轮范围与以后补充

本轮先完成工程结构、自动规则、历史迁移与不可变版本能力；OAuth、PostgreSQL、工作空间和真实模型联机仍未完成，不能以“完成分层”替代安全与产品验收。

## 10. 工作空间权限与会话规则（v0.4 追加）

- Agent、Run、版本、执行事件、Checkpoint 等一律要求已登录及服务端 workspace 成员资格校验；HTTP X-Workspace-ID 不是权限凭据，必须逐次验证。
- Scope 对象隔离应在 Repository 的 WHERE workspace_id 实现，跨 workspace 的对象 ID 一律不可见（404）；未登录 401、无成员资格 403；viewer 禁止写。
- 运行 Worker 只可领取归属已确认的 workspace_id 不为空的任务；旧 NULL 数据隔离保留，不由首位注册用户自动继承。
- 浏览器禁止把会话令牌保存在 LocalStorage 或全局 JS；应通过同源 Next.js 代理 + HttpOnly Cookie，后端保存令牌摘要并支持失效。
- 本地开发 Cookie 不具备正式 HTTPS、安全审计、密码找回、限速及 PostgreSQL RLS，因此禁止对公网部署。详见 docs/11_WORKSPACE_AUTH.md。
