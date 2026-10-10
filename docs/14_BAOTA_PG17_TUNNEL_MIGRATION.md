# Agent Studio V0.6 · 宝塔 PostgreSQL 真实迁移操作说明

更新：2026-10-10

## 现状（由用户现场验证）

- 宝塔 Linux 服务器中独立的 `agent-studio-postgres` Docker 容器运行 PostgreSQL 17.11；
- 独立数据库为 `agent_studio`，`pgvector` 0.8.7 已启用；
- 数据库绑定服务器 `127.0.0.1:54329`，未直接开放到公网；
- Windows 上已经使用 `agenttunnel` SSH 密钥建立安全本地转发：`127.0.0.1:15429` → 宝塔 `127.0.0.1:54329`；
- Windows 已实测 SSH 端口、PostgreSQL SSL 协商响应 `N`、以及通过 `studio_migrator` 账户成功登录并执行 `SELECT current_user,current_database()`；
- **尚未执行项目 Alembic 迁移，更不意味着 FastAPI/Worker 已切换到 PostgreSQL**。

## 1. 建立无密码明文输出的安全操作流程

项目已新增 `scripts/pg_staging.ps1` 与 `scripts/check_postgres_staging.py`，分三个独立动作：

1. `Check` — 只读核验 DB17、角色、数据库名、已启用 pgvector、当前表和 Alembic 修订版；如数据库目标错误、有未知表或未审核的旧结构立即阻止；
2. `Migrate` — 先完成 Check，随后要求输入大写 `MIGRATE` 才执行项目 **V0.6 Alembic** 初始迁移，完成后再次只读核验；
3. `RlsTest` — 先 Check，随后在数据库内进行真实 PostgreSQL RLS/版本/任务安全集成测试。测试使用临时合成数据、一个事务整体回滚；不导入 SQLite 数据。

脚本仅接受本机 SSH 隧道端口 `15429`，数据库 `agent_studio`、账号 `studio_migrator`；数据库连接密码会单独提示输入，不在命令行参数、脚本或输出中公开，URL 的特殊字符也会正确编码。测试脚本 `smoke_postgres_rls.py` 已适配端口 `15429`。

**Windows 本地 PowerShell**（保持另一个 SSH 隧道窗口运行）：

```powershell
cd "D:\工作项目\agent studio"
& .\scripts\pg_staging.ps1 -Action Check
```

如果输出 `schema_state: initial_empty`、`safe_for_baseline: True`，说明尚无 Agent Studio 表，可在确认服务器与 DB 符合预期后执行：

```powershell
& .\scripts\pg_staging.ps1 -Action Migrate
```

脚本再次要求输入数据库管理员密码并确认输入大写 `MIGRATE`，才会写入数据库结构。出现错误时不要强行重复执行、更不能手工删表/关闭权限策略。

当 Alembic 成功且迁移后 Check 输出 `schema_state: already_migrated`、`alembic_revision: 0001_pg17_workspace` 时，运行：

```powershell
& .\scripts\pg_staging.ps1 -Action RlsTest
```

**在看到完整实际输出前，任何“迁移成功/真实 RLS 通过”的说法都不成立。** 每个命令由用户本地直接运行，助手没有读取/保存数据库密码，也没有代替用户连接或修改远端服务器。

## 2026-10-10：Windows 密码认证失败的修复说明

运行 `& .\scripts\pg_staging.ps1 -Action Check` 后返回 `FATAL: password authentication failed for user studio_migrator`，表示 SSH 隧道已转发到 PostgreSQL，但**提交的数据库密码未通过验证**。Alembic 未开始迁移，数据库数据未被本次 Check 修改。

数据库初次部署生成密码的方法是 `openssl rand -hex 32`，因此原始密码为 **64 位十六进制字符**，存放于宝塔服务器 `/opt/agent-studio-pg/secrets/admin_password`。不要复制包含回车的命令文本、不要输入显示用的 `*`，不要在聊天或截图中公开密码。

更新的 `pg_staging.ps1` 检查隐藏输入必须是完整的 64 位十六进制原始凭据，输入截断时会直接停止，不访问数据库。若密码**已经被主动轮换**为其他格式，应先核实实际新凭据并调整本地检查规则，不要擅自重置密码。SQLAlchemy 密码/连接错误改为简洁、脱敏、可操作的提示，不再倾倒完整 Traceback。

恢复步骤：在宝塔终端自行读取原文件并完整复制密码，在 Windows 新 PowerShell 窗口保持 SSH 隧道运行，进入项目根目录重新执行 `& .\scripts\pg_staging.ps1 -Action Check`。只有当结果为 `schema_state: initial_empty` 和 `safe_for_baseline: True` 才能安排下一步迁移；若完整 64 位密码仍被拒绝，应停止重试并检查是否曾修改数据库角色密码。不得删除数据库或重建容器。

## 2. 重要安全限制

- `studio_migrator` 是初始化/测试管理员，不能作为 FastAPI/Worker 日常连接账号；
- 正式 API/Worker 的 PostgreSQL 运行时账号、受限角色、LangGraph PostgresSaver、任务 Worker/事件流及可恢复执行链路仍需独立开发和真实 DB2/DB3 级别验收；
- 旧 SQLite 数据未经所有权审核，不自动迁移；原本无主的数据不属于新账号；
- 本地 Auth 尚缺生产级 OIDC/MFA、HTTPS、限速、审计；切换数据层前后仍不得开放公网；
- 数据库 RLS 测试采用迁移账号创建临时数据，随后切换到受限角色模拟业务访问；这只验证 RLS 规则，不能代替上线时低权限账号的真实连接测试；
- 保留当前 SSH 隧道窗口；数据库端口始终仅本机绑定，不需要开放 `54329` 公网入站。

## 3. 开发和测试

离线单元测试覆盖本机 SSH 隧道/错误角色/错误库/未知表/不完整迁移的安全阻断，不使用真实密码：

```powershell
cd "D:\工作项目\agent studio\services\api"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_postgres_staging_preflight.py -v
```

验收状态：新 PowerShell 脚本已通过语法检查，预检纯只读行为通过 Mock 回归；实际远程目标当前的 Check/Migrate/RlsTest 需由用户在 Windows 上执行确认。
