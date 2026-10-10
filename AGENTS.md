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
