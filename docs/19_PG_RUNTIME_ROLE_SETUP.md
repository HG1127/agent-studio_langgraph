# Agent Studio｜PostgreSQL 独立运行账号阶段（V0.8）

更新：2026-10-10

## 真实验收状态

用户已在宝塔服务器完成独立 PostgreSQL 17.11 + pgvector 0.8.7 部署及 Alembic `0001_pg17_workspace` 初始化（12 张业务表）。用户又通过 Windows SSH 隧道执行真实 `RlsTest`，命令返回 `PostgreSQL RLS integration test passed.`，具体包含 Agent 版本不可变、Run 与初始事件原子创建、租约回收、未授权工作空间隔离、Viewer 禁止写和身份/隔离表保护。测试资料位于一个回滚的事务中。

**这是 DB2 基础权限/队列验收，不代表 Agent Studio 的 FastAPI/Worker 已连接 PG 数据库。** 目前用户可见应用和 LangGraph Checkpoint 仍为 SQLite 本地版。

## 下一阶段：创建低权限 PostgreSQL 登录账号

新脚本 `scripts/provision_pg_runtime.py` 与 Windows 启动器 `scripts/pg_staging.ps1` 新增三个可独立执行的动作：

| 命令 | 行为 | 会修改数据库吗 |
|---|---|---|
| `-Action RuntimeRoleCheck` | 只读检查迁移版、RLS 角色及专用登录账号状态 | 否 |
| `-Action ProvisionRuntime` | 检查完成后要求输入大写 `PROVISION`、两次隐藏输入一个新的强密码，并只创建 `agentstudio_app` | 是，显式确认后 |
| `-Action VerifyRuntime` | 以该非管理员账号真实登录，检查角色安全属性、`SET LOCAL ROLE` 和未绑定 Scope 时无可见 Agent | 否 |

整个过程要求现有 SSH 隧道保持运行；仍会提示输入已有 `studio_migrator` 迁移密码，但**不会输出、保存或发送任何密码**。新账号密码在创建时由用户自行提供，建议通过可靠密码管理器生成单独的 64 位十六进制密码并保存，切勿复用管理员密码。运行脚本期间密码仅用于数据库连接；若密码遗失，不应重复删除/创建角色。

### 1. 只读查询角色

在 Windows 第二个 PowerShell 中执行：

```powershell
cd "D:\工作项目\agent studio"
& .\scripts\pg_staging.ps1 -Action RuntimeRoleCheck
```

正常首次可能出现：

```text
RUNTIME_ROLE_STATUS: not_provisioned
```

如果角色已经存在且属性符合规定，则显示 `valid_least_privilege_role`，不要重复创建。

### 2. 用户确认创建账号

只有在 `not_provisioned` 且用户确实想进入下一阶段时，执行：

```powershell
& .\scripts\pg_staging.ps1 -Action ProvisionRuntime
```

先输入管理员密码，经只读验证后输入大写 `PROVISION`。随后 Python 在终端内**两次隐藏读取新账号密码**（同一密码）；不会将该密码放在命令行参数、PowerShell 历史或应用配置文件中。创建的 PostgreSQL 角色名固定为 `agentstudio_app`，具备 `LOGIN / NOINHERIT / NOSUPERUSER / NOCREATEDB / NOCREATEROLE / NOREPLICATION / NOBYPASSRLS`，且仅授权 `SET ROLE agentstudio_runtime`；不会给它读取 `users` / `sessions` / `quarantined_legacy_rows` 的权限。已有同名账号时拒绝修改密码。

### 3. 真实低权限登录验证

```powershell
& .\scripts\pg_staging.ps1 -Action VerifyRuntime
```

它会依次询问迁移管理员密码、运行账号密码。预期最终返回：

```text
PASS: real runtime LOGIN and SET LOCAL ROLE; unscoped rows hidden.
```

这才证明普通登录账号可经 SSH 隧道认证、在事务内切换到受 RLS 限制的角色，同时默认不能读取 Agent 或身份敏感表。

## 安全限制（必须保留）

- `studio_migrator` 仅用于数据库迁移、受控测试、角色初始化，**不得**配置成 Web API 或 Worker 的常驻数据库账号。
- `agentstudio_app` 是可信后端**数据库凭据**，不能交给浏览器、最终用户或不可信工具。PostgreSQL 自定义 `agent_studio.user_id/workspace_id` 会话参数由后端设置；数据库登录账号如果被泄漏，可伪造这些参数，因此本阶段 RLS 属于后端可信主体约束下的防线，不是“暴露数据库账号后仍绝对无法跨租户”的证明。
- 该账号的 `CONNECT` 授权针对 `agent_studio`，并不撤销 PostgreSQL 对其他数据库可能存在的默认 `PUBLIC CONNECT`。此数据库服务在独立容器中，仅绑定宝塔服务器本机地址；不得开放到公网。
- 未添加 LangGraph PostgresSaver、正式可信身份来源、PG Worker 跨租户任务领取权限、持久化调度及重启恢复验收前，**不能启用 `STUDIO_DATABASE_BACKEND=postgres`**。
- 任何出错都应停止。禁止删除/重建 PostgreSQL 容器、在公网开放 54329、禁用 RLS 或复制密码到日志/截图。
- 本轮只交付可运行的角色设置脚本与离线测试，不代表用户已经执行 `ProvisionRuntime` 或 `VerifyRuntime`。

## 后续计划

确认真实非管理员登录通过后，再开发后端 Postgres Session/Repository 路由、持久化 LangGraph PostgresSaver、Worker 执行和并发恢复。最后处理 SQLite 数据所有权映射、备份、分批导入及对账，再进行用户可见的数据库切换。
