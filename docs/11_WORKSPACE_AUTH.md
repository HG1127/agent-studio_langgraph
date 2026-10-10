# Workspace & Local Authentication v0.4 — Implementation Record

日期：2026-10-10。范围：**仅 localhost 本地开发身份与工作空间隔离**；不等同于已通过生产安全评估。

## 1. 功能已实现

- 邮箱注册、密码登录、注销、查询当前会话。密码采用独立随机盐的 scrypt 哈希，不存明文。
- 服务端生成随机会话令牌；数据库仅保存令牌 SHA-256 摘要和 12 小时到期时间。Cookie HttpOnly、SameSite=Strict，退出删除并撤销服务端会话。
- 注册时创建个人工作空间及 owner 成员资格。登录后可创建其他工作空间，按工作空间切换 Agent、版本、Run。
- 每次 Agent/Run 访问都要求服务端验证 X-Workspace-ID 与登录用户的实时成员资格，未指定范围返回 400，无工作空间权限返回 403，跨工作空间对象 ID 返回 404。
- 工作空间角色 owner/admin/member/viewer；viewer 可读取但不能创建 Agent、版本、任务或操作暂停/取消。owner/admin 可按邮箱授予已注册用户 member/viewer/admin；owner 角色不可被成员接口覆盖。
- FastAPI 路由按 identity/agents/runs/system 划分，domain.identity、application.identity_service 和 infrastructure.sqlite.identity_repository 分层，不把权限只写在 LLM Prompt 或前端。
- Next.js 同源代理 `/backend/*` → loopback FastAPI；浏览器仅与 Next 同源交互，Cookie 不暴露给浏览器 JS。App 登录后方装载工作台，工作空间切换以独立组件 key 重置原上下文。
- 登出即删除 Cookie，服务端会话撤销；会话过期 / 越权操作由后端拒绝。
- 本地 API 仍拒绝非 loopback 客户端，APP_ENV=production 仍拒绝启动。已增加 Origin 拦截、敏感内容 no-store 与 nosniff 响应头。

## 2. 持久化与历史记录治理

SQLite 新建 users、sessions、workspaces、workspace_members 表，agents、runs 增加可为空的 workspace_id。

**安全迁移原则：历史无归属数据不自动分配给新账户。** 旧 Agent/Run 的 workspace_id 留 NULL，API 不显示，Worker 不领取；后续需经可信归属核验，由受控维护工具显式迁移。没有任何“首个登录者自动继承旧数据”的捷径。

新 Agent 创建时写入确切 workspace_id；新 Run 与版本快照在事务内按 workspace_id 校验 Agent 归属。所有公开 GET、POST、控制及检查点接口范围均由服务端确定，不接受前端声称的角色。

## 3. API 契约

| 路径 | 认证 | 作用 |
| --- | --- | --- |
| POST /v1/auth/register | 不需要 | 注册并创建个人 Workspace，设置安全属性 Cookie |
| POST /v1/auth/login | 不需要 | 校验凭据并签发会话 |
| POST /v1/auth/logout | 可重复执行 | 删除/撤销 Cookie Session |
| GET /v1/auth/me | 已登录 | 获取当前用户及有权访问的工作空间 |
| GET/POST /v1/workspaces | 已登录 | 查看或创建当前用户 Workspace |
| GET/POST /v1/workspaces/{id}/members | 成员/管理员 | 查看成员，管理员新增或修改成员 |
| /v1/agents/* | Workspace Header | 读写按角色授权与资源归属过滤 |
| /v1/runs/* | Workspace Header | 任务、事件、Checkpoint、控制按角色及归属过滤 |

## 4. 本轮回归范围

- 原有不可变 Agent 版本、任务队列、LangGraph Checkpoint/工具调用与失败恢复回归继续执行；
- 两个不同用户的跨工作空间 Agent/版本/Run/Events/Checkpoint/Control 越权负向测试；
- 同一用户双工作空间隔离；
- viewer 只读与成员角色调整后即时生效；
- Session 退出/过期、密码哈希与 Cookie 属性；
- 旧无主数据隔离而非自动归属；
- Next.js 实际进程 → same-origin proxy → FastAPI 注册/认证/资源创建/退出的 HTTP 集成测试；
- 测试中模型 HTTP Stub 不代表真实 LLM 调用。

运行方式：
```powershell
# 在 services/api 目录下
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe ..\..\scripts\smoke_process.py
.\.venv\Scripts\python.exe ..\..\scripts\smoke_web_proxy.py

# 在 apps/web 目录下
npm run lint
npm run format:check
npm run build
```

## 5. 尚未达到的生产要求（不得宣称已上线）
**本地代理安全边界：** Next.js dev/start npm 脚本强制监听 127.0.0.1，防止后端 loopback 限制被公开前端代理间接绕过。不得通过反向代理或自定义命令将本地 Web 应用向外公开。

- OIDC / 邮箱验证、多因子认证、密码重置、登录速率限制、审计告警、凭据轮换、安全事件响应；
- 正式 HTTPS、Cookie Secure 属性、CSRF 策略在真实域名与反向代理后的全面验证；
- PostgreSQL/Alembic 迁移、RLS、权限回收与数据库连接池；
- Worker 的分布式租约、外部写操作审批和幂等；
- 真实浏览器 Playwright 全流程/无障碍测试、渗透测试、灾备和备份；
- 真实大模型账户授权、MCP/搜索/PDF/知识与长期记忆、Eval、计费。

**当前里程碑：本地工作空间隔离功能阶段性落地，尚不可公网部署。** 下一步优先推进 PostgreSQL+Alembic 的正式身份/工作空间迁移设计与测试，再评估生产级托管身份方案。
