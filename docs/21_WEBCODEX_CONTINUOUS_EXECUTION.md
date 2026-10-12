# WebCodex 连续开发执行诊断与恢复队列

关联 Goal：`wc_goal_0BvtHuyXwSJoWnkM`。这是仓库内关联；本轮无法访问远端 Goal，未修改其状态或建立已确认的服务端关联。

## 本轮结论（2026-10-10）

连续开发机制尚未确认可调用，未启动新 Coding Agent 任务。只完成只读诊断、质量检查尝试和本文档 checkpoint。不能声称已有后台 Codex 在运行。

| 检查项 | 实测证据 | 结论 |
| --- | --- | --- |
| WebCodex MCP 连接 | `tool_manifest(category=runtime)`、全局/指定 Runner `runtime_status`、`list_jobs(limit=20)` 均返回 HTTP 404，`tunnel_client_not_seen`，超过 300 秒未见客户端 | 连接层阻断；不是 provider 缺失的证明 |
| ACP Coding Agent provider | 本地 `runner-coding-agent-runs-v1/**/state.json` 有 `provider_id=codex`、`provider_instance_id=acp_883b1a1dceb14188926e218eabc7b369` | 有历史配置证据；当前登录、授权和可调用性未知 |
| Host Endpoint | MCP 无法返回 Host 状态 | `production_auto_resume_available` 未知，不能视为 true 或 false |
| 活跃 Job | 远端 `list_jobs` 无法读取；本地 9 份 detached Job 记录均 `phase=terminal` | 本地快照不能证明服务端无活跃 Job，也不能排除阻断 |
| Coding Agent 非终结记录 | `wc_agent_run_5XJNyPwTXXEv9r2aTInUbyi3mXasQfk3XW65JPgVIhY`：`running/started`，`prompt_dispatch_may_have_occurred`，最后更新 2026-10-10 10:52:52 UTC | 可能已派发；必须核对后再继续，不能盲目重试或重复启动 |
| 上一条终结记录 | `wc_agent_run_w3ro-puFr8bPfIENlLc4VUjaBIkHoCKMUbA8mG8PytY`：`completed`、`stop_reason=end_turn`，更新 10:51:42 UTC | 只证明该条历史执行结束，不证明质量门禁通过 |

本地 Runner：`pc-20260117cybz-d6400459d85747f3`；Project：`agent:pc-20260117cybz-d6400459d85747f3:agent-studio-2ef5c169`。进程存在仅用于定位，不能作为 Job 存活或自动恢复的证据。未读取凭据、修改 provider、重启进程或清理状态文件。computer-use 技能已读取，但当前没有 `node_repl` 工具，未执行桌面 UI 自动化；受保护的桌面 environment 目录不可读。

## 用户当前唯一需要完成的连接操作

在正在运行的 WebCodex Desktop 中，恢复其现有 tunnel-client 与**本会话 WebCodex 连接**，直到连接界面显示已连接。当前证据只支持此连接操作；不要求新增 API Key、购买服务或重新登录 Codex。具体按钮名称未通过 UI 验证。

恢复后由代理复验：

1. 再调用全局 `runtime_status`，确认不再返回 `tunnel_client_not_seen`；核对实际在线 client_id，避免沿用已失效实例。
2. 调用精确 Runner `runtime_status`，记录 Host Endpoint 的 `production_auto_resume_available` 原值及原因。字段缺失仍记为未知。false 时只可按实际能力执行当前会话，不宣称离线自动续跑。
3. 用 `tool_manifest` 发现当前 coding-agent/provider/host/goal 只读工具的精确契约，再检查 ACP provider 的注册、认证、执行能力和授权范围，禁止猜测调用参数。
4. 若此时明确缺少或未认证 Coding Agent，仅要求用户在 Runner 的 ACP Coding Agent 配置中连接并登录其已授权的 Codex provider；登录完成后重复 provider 检查，以真实返回值确认可调用。该操作是条件性后续步骤，不是本轮已诊断出的缺失项。
5. 按精确 Project 查询 Job，并观察上述非终结 run 的现状和阻断原因。保留 Job/run ID 与工作区；不 kill、不删除记录，不在派发结果不明时再次 start。
6. 读取 Goal `wc_goal_0BvtHuyXwSJoWnkM`，确认既有队列、Session 和授权；按已发现契约关联本文档。远端关联成功前保持“仅仓库关联”。

## 可恢复队列

| 顺序 | 任务 | 状态 / 完成条件 |
| --- | --- | --- |
| Q0 | 恢复连接并复核 provider、Host、Job、Goal | 等待连接；三项可调用条件与非终结 run 均有明确结论后才能派发 |
| Q1 | 重建当前工作区验证基线 | 重新读取 AGENTS.md、工程规范、README、Git diff 和测试；修复或定位本地 Python 环境，禁止覆盖既有改动 |
| Q2 | 最小任务：Python 格式门禁修复 | 待 Q0/Q1；仅在届时 diff 确认安全且既有 run 不再修改相同文件后，对 `app/domain/evaluation.py` 与 `app/llm.py` 的 Ruff 格式问题作最小修复，不改业务/API/数据库 |
| Q3 | 测试、评审、checkpoint | 全部规定门禁通过后审阅完整差异，记录本任务文件及哈希、验证结果、下一任务；失败则停留当前任务，不能标为完成 |
| Q4 | 下一任务 | 根据 Goal 现有优先级重新选一个明确用例；不得自动扩展到 PG 切换、真实付费模型探测、push 或部署 |

每个任务采用 `read → edit → test → review → checkpoint → 下一任务`。checkpoint 是可恢复证据记录，不默认创建 Git commit。记录 Goal、Project、Session、Job/run、基线 HEAD、文件哈希、修改范围、测试退出码和下一步。中断后先观察旧执行并重读当前文件，再恢复，禁止直接重放 edit/start。既有未提交和未跟踪文件全部保留，不 reset/stash/clean。

## 本轮验证 checkpoint

- 已读 `docs/09_CODE_STANDARDS.md`、根 README、变更日志，并通过绝对路径 Git 检查到多项既有修改和未跟踪文件。仅新增本诊断文档并追加实现日志，未启动业务代码修改任务。
- `ruff check .`：通过。
- `ruff format --check .`：失败；`app/domain/evaluation.py`、`app/llm.py` 需格式化。本轮保留原状。
- `unittest`、架构检查、`scripts/smoke_process.py`：均尝试执行，但 venv 引用不存在的 `C:/Users/Administrator/AppData/Local/Python/pythoncore-3.14-64/python.exe`，启动失败，未执行测试。Smoke 本身是本地模型协议模拟，不能算真实 LLM。
- Web 初次检查因 PATH 无 node 启动失败；在当前检查进程 PATH 加入已安装 Node 后重新执行，lint/typecheck/build 均通过。
- 基线 HEAD：`a4ff5951032cacfcd06c05c887b5d95c606395ed`。文档 checkpoint 留在工作区，未提交。差异评审未发现本轮业务代码变更；原变更日志末尾空行提示由追加日志消除。
- 无远程 push、部署、数据删除、供应商计费调用；未声明生产安全或连续后台执行已验收。

## 宿主 WebCodex 复核后的状态纠正（2026-10-10）

上文描述的“tunnel_client_not_seen / 无法访问 WebCodex”是 Codex ACP **子进程内部 MCP 连接器**的观测，不能代表外层 WebCodex Server / Windows Runner 的实际状态；以下以独立宿主查询为准：

- Windows Runner pc-20260117cybz-d6400459d85747f3 在线（connected=true），配置中 codex provider 已注册；Runner 配置检查 valid=true、restart_required=false，SystemRoot 环境变量映射已持久化。
- Codex ACP 只读真实执行 wc_agent_run_w3ro-puFr8bPfIENlLc4VUjaBIkHoCKMUbA8mG8PytY 已 completed/end_turn，实际模型响应并检查了 AGENTS.md、开发计划、验收矩阵与工作区未提交更改。
- 持久 AgentTask 对应执行 wc_agent_run_5XJNyPwTXXEv9r2aTInUbyi3mXasQfk3XW65JPgVIhY 也已 completed/end_turn，并经服务器 reconcile 为 succeeded。此次主要生成本诊断文档并追加变更日志；任务结束不代表全部质量门槛已通过。
- 子进程内部连接器仍曾报 tunnel_client_not_seen；应将“宿主已成功派发并结束”与“子进程内部工具连接可用”分开记载。后续需进一步诊断内部连接器，不应要求用户为此重复安装/登录 Codex。
- 宿主 Agent 当前 production_auto_resume_available=false。现有每小时续接任务可定时发起下一次可授权工作，但未证明 24x7 连续自唤醒；启动前必须查服务端 task/run 实际状态，避免重复执行已结束任务。
- 既有工作区未提交变更保留；Ruff 格式、Python 虚拟环境以及独立 Eval 占位仍待验证处理，不得把 ACP 调用成功视为 S2 验收完成。
