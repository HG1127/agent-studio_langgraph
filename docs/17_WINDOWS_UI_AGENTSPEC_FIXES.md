# Agent Studio V0.8｜Windows 脚本、AgentSpec 结构生成与状态显示修复

日期：2026-10-10

## 截图反馈与原因

1. `scripts/pg_staging.ps1` 在 Windows PowerShell 5.1 中以错误编码读取未带 BOM 的 UTF-8 中文提示，出现引号破坏、UnexpectedToken 和 try/finally 解析错误。修复：脚本改为纯 ASCII 文本和 UTF-8 安全的英文提示，保留 SSH 端口探测、隐藏输入、数据库目标范围限制及显式 MIGRATE 确认。`-Action Check` 仅只读，不自动修改远程 PostgreSQL。
2. `模型生成的结构不符合 AgentSpec` 出现在模型输出只接受严格平铺 JSON 字段的旧解析器。新增 `application/draft_normalizer.py`，容纳平铺 / `goal.task` 嵌套 JSON、JSON 代码围栏、文本前缀、`spec/agent/data/result` 包装、成功标准字符串/数组，同时由后端覆盖工具与权限字段。模型格式错误会再请求一次 JSON；仍失败则提示检查模型的 JSON 支持或使用手动草稿，不将错误归咎用户输入。
3. 顶部状态提示字体偏小、信息重复、数据库和 Worker 状态挤在单行，关键信息可能折行或截断。`StudioShell.tsx` 和 `runtime.css` 现采用可展开说明、独立状态行、长文本自动换行以及可复制 Worker 命令，不伪装 Worker 已运行或 PostgreSQL 已激活。

## 当前安全边界与未完成项目

- PostgreSQL 17.11 + pgvector 在宝塔已部署、Windows SSH 隧道及账号登录已确认；**Alembic Migrate / RlsTest 是否成功尚无输出**，不要把数据库建表或运行时切换标记为完成。
- 上游模型配置要具有真实的受授权供应商接入；新增正常化不会虚构 Agent 已有工具能力。
- 开发项目有另一个会话的模型连接功能正在修改，不能自动覆盖或将对方未完成文件混入 Git commit。
- LangGraph Worker 红色未运行指示取自服务端 `worker_online`，不是单纯 UI 错误。请另开 PowerShell 从根目录执行 `cd services/api; .\.venv\Scripts\python.exe -m app.worker` 启动。下一步还需检验任务链路。

## 下一步验证命令

在 Windows 本地项目根目录且另一个 SSH 隧道窗口保持运行时：

```powershell
cd "D:\工作项目\agent studio"
& .\scripts\pg_staging.ps1 -Action Check
```

通过只读预检后，显示 `schema_state: initial_empty` 且 `safe_for_baseline: True`，才可以经用户确认选择 `-Action Migrate`；迁移后再单独执行 `-Action RlsTest`。不清除 SQLite 原数据，不向公网部署。

后端建议执行 `python -m unittest discover -s tests -p test_agent_draft_normalization.py -v`；前端建议执行 `npm run lint`、`npm run build`。真正供应商不同 JSON 响应必须用授权的真实连接验证。
