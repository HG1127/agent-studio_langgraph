# Agent Studio | 零 GitHub Actions 费用 CI 落地（2026-10-12）

## 范围与验收声明

仓库：`HG1127/agent-studio_langgraph`（Private）；`develop` 是开发分支，`main` 是稳定分支。本方案不自动合并 `main`，不从真实 PostgreSQL 数据库读取或删除数据，不需要 GitHub Actions 托管分钟数，也不会自动调用收费模型。

GitHub Actions 上的 `api/web/proxy/postgres-rls` 曾在执行前被计费限制拦截，不能把它们视为“代码测试失败”或“成功通过”。2026-10-12 的测试日志和历史宝塔 PG17 RLS 验收是不同证据。

### 第一步：Windows 本地 CI

`scripts/ci.ps1` 是一键测试器，默认 Full 模式：

```powershell
cd "D:\工作项目\agent studio"
& .\scripts\ci.ps1 -Mode Quick
& .\scripts\ci.ps1 -Mode Full
```

它对 **已提交的 Git SHA** 在 `.local/ci/worktrees/<random>` 创建单独 Git Worktree，Quick 模式复用依赖链接、Full 模式在临时工作树运行锁文件约束的 `npm ci`（Turbopack 不支持指向外部的 node_modules Junction），完成后移除临时工作树，避免覆盖本地预览页面的 `.next`、SQLite 数据库或工作区未提交改动。**Git Worktree 只隔离文件目录，不是进程级安全沙盒；仅可执行受信任分支的代码。** 报告会写入未跟踪的 `.local/ci/reports`。开发期间如有未提交文件，默认只会验证 Git 已提交的版本，不会默默把未提交代码当作已验证成果。

- Quick：Ruff lint/format，架构边界，所有 unittest，Next ESLint/Prettier/TypeScript。
- Full：Quick + Alembic 生成离线 SQL + LangGraph 独立 Worker 模拟 HTTP 供应商 Smoke + Next 生产构建。
- `-IncludeProxySmoke`：可选同源 Cookie HTTP 代理测试；**要求本机 3000 端口空闲**，不杀死已经运行的 Web 开发预览。
- **真实 PostgreSQL/RLS 不是上述 Full 的一部分。** 一定不能将本地 Full 通过解释为真实 PG17、真实模型或生产安全验收。
- 没有预配置模型 API Key 的情况下，CI 自动清理传入子进程的常见模型/数据库密钥环境变量；测试使用临时 SQLite 和本地模型模拟，不使用真实外部工具。

### 当前已实测的本地验收事实

2026-10-12 在 Windows 本地以已提交的 `develop` SHA `cfe8c3d5cf66` 运行以下测试：

- `scripts/ci.ps1 -Mode Quick`：通过；264 项 unittest、Ruff、架构检查、前端 lint/format/typecheck 均通过。
- `scripts/ci.ps1 -Mode Full`：最终通过（exit 0），13 项命名步骤全部通过；包含独立 `npm ci`（339 packages）、Alembic 离线 SQL、LangGraph 模拟服务跨进程 Smoke、Next.js 生产构建。检查输出显示 unittest 中预期的异常测试栈，但最终为 264 tests OK，不构成失败。历史上的两次 Full 失败系测试 Checkpoint 环境变量和 Windows Junction/Turbopack 构建路径问题，已修复，不能用失败记录覆盖最终通过事实。
- 三份 PowerShell CI 脚本：Windows 解析检查 0 错误，ASCII-only；Woodpecker Controller Compose `docker compose config --quiet` 通过；以本机 Administrator 尝试安装轮询任务被安全保护拒绝且未创建任务。
- 真实 PostgreSQL/RLS 和 Cookie 代理集成测试均**未**包含在当前 Full。真实 Woodpecker Agent/HTTPS/OAuth 尚未部署。

### 第二步：免费的 GitHub 开发分支自动检测

`scripts/ci-watch.ps1` 每运行一次就从 GitHub Private 仓库验证 `origin/develop` 的提交 SHA，获取对应提交后在隔离 worktree 内执行 `ci.ps1 -Mode Full`，并使用本机已有 `gh auth` 身份给该 SHA 写一个明确范围的 **`ci/windows-isolated`** Commit Status；一切由 GitHub CLI API 完成，不消耗 Actions Runner 分钟、不需要 webhook 或开放入站端口。

```powershell
# 先单次验证与 GitHub Commit Status 权限，预期约 1~3 分钟
& .\scripts\ci-watch.ps1 -Force

# 审查已有计划任务
& .\scripts\ci-schedule.ps1 -Action Check

# 切换到预先配置的专用非管理员 CI Windows 账号、完成 gh 登录后，
# 才可为该账号安装每 15 分钟轮询任务（管理员账号会被脚本拒绝）
& .\scripts\ci-schedule.ps1 -Action Install

# 如需停止
& .\scripts\ci-schedule.ps1 -Action Remove
```

该任务名固定为 `AgentStudio-Develop-CI`，使用 Windows Task Scheduler 的 **Interactive / Limited** 运行级别，只在该用户登录、电脑开机时执行。**目前本机操作账号为 Windows Administrator；基于自动执行仓库代码的风险，不应在该账号下安装定时执行任务，`ci-schedule.ps1 -Action Install` 会拒绝管理员。** 专用 CI 用户应最小化文件、网络和 GitHub 授权，不得访问真实项目数据库/模型密钥。它检查是否已经测试过该 Git SHA，不重复浪费 CPU；通过 `.local/ci/poll.lock` 避免并发；执行账户需要提前通过 `gh auth login`，能读取私有仓库、写 GitHub Commit Status。脚本不把 OAuth/Token 写入仓库、不自动修复错误、不推送、不合并。仅适用于 **自己可信的 develop 提交**；如果其他人获得该分支写权限，切勿在含其他敏感凭据的日常管理员账号下自动执行提交代码。若要长期稳定可靠，应该迁移至独立、低权限 CI 机器/虚拟机。

在 GitHub Pull Request 中可看到 `ci/windows-isolated` 的状态。但是这个状态只代表该脚本实际覆盖的检测，**不是完整发布验收**。正式合并需另有独立 PostgreSQL/RLS、HTTP proxy 和代码评审；不自动合并。

### GitHub Actions 账单拦截的处理

仓库中 `.github/workflows/ci.yml` 保留原来的四个 Job 的定义，暂时仅允许手动 `workflow_dispatch`，不再于 `push/pull_request` 自动派发产生计费失败通知。这不会改变仓库 Private 属性，也不等于之前失败的 CI 已经通过。恢复 GitHub Actions 需要审查账单状态并重新启用对应触发器；如果一直使用独立 CI，可保持关闭。

### 第三步：宝塔服务器 CI（需先取得独立授权/接入条件）

计划使用 Woodpecker v3 server，GitHub OAuth2 App、HTTPS 域名反向代理和独立的 Agent Worker。官方：[Docker Compose](https://woodpecker-ci.org/docs/administration/installation/docker-compose)，[GitHub OAuth](https://woodpecker-ci.org/docs/administration/configuration/forges/github)。

已提供 `infra/ci/woodpecker-compose.yml` 作为只含 **Controller** 的配置示例，HTTP 只绑定宝塔本机 `127.0.0.1:18080`。**不要**在已有 PostgreSQL/宝塔业务 Docker 宿主机上给 CI Worker 装载 `/var/run/docker.sock`，那相当于授予 Worker 对该宿主机全部容器、数据库卷的高权限。CI Agent 应置于**另一台受限主机或独立 VM**，运行 Docker 容器化的临时测试数据库。Worker 代码不允许任意触碰现有 `agent-studio-postgres` 或其他业务容器。

真实远程部署前需有：
1. 宝塔的被授权系统管理访问（目前 Windows 上现有受限 `agenttunnel` 账号的只读 SSH 命令认证被拒绝，无法确定剩余内存、Docker 存储、安全组）。
2. 一个指向 CI Controller 的有效 HTTPS 域名及反向代理/证书，用于 GitHub OAuth 回调 `https://<domain>/authorize` 和 webhook。
3. GitHub OAuth App 的 Client ID、Client Secret；这两项只能通过安全本地 Secret/受限权限文件配置，不得通过聊天、截图或 Git 传递。
4. 独立、低权限 CI Agent 主机及用于每次测试的临时 PostgreSQL 17 + pgvector；不直接访问业务数据库。
5. GitHub 上提交状态回传确认和 `main` 的安全合并策略。

**当前尚未部署 Woodpecker，也未取得远程管理员账号；不能将配置文件当作可用 CI 服务。** Windows 已可运行的 Commit Status 轮询是阶段性替代方案。

## 退出与恢复

- 本地 CI 任何失败都应保留报告并阻止 `main` 合并；脚本从不执行 `git push`、`git merge`、`git reset` 或数据库结构写入。
- `git worktree list` 可以查看所有独立工作树。脚本只删除它本次创建的唯一临时 worktree，不能清理其他开发者工作区。
- 如轮询无法读取 GitHub，请检查 `gh auth status` 或网络；不要发送个人访问令牌、密钥、密码给 AI。
- CI 不等于部署；Windows 开发工作台、宝塔 PostgreSQL 继续保持现有运行模式。
