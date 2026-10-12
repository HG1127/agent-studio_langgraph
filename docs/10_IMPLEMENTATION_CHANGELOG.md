# Agent Studio 本轮架构重构与版本管理实施记录

日期：2026-10-10 · 目标：在本地可运行 S1 版本上，执行工程结构规范、重构和不可变 Agent 版本。

## 1. 交付范围（已编写与验证）

- 新增 `AGENTS.md`、`.editorconfig`、`docs/09_CODE_STANDARDS.md`；明确架构边界、命名、测试和发布阻断条件。
- 新增 `scripts/check_architecture.py`：基于 AST 检查 Python domain/infrastructure/harness/application 的违规层依赖，并要求 Next.js `app/page.tsx` 为薄路由。
- 后端重构为 `domain`、`application`、`infrastructure/sqlite`、`api/routes` 和 `harness`，保持 `app.db` / `app.runner` 兼容 facade；没有更换原有 LangGraph StateGraph。
- `app/main.py` 只装配 FastAPI 和安全中间件；路由按 agents/runs/system 组织；前端拆分为 `src/features/agents`、`src/features/runs`、`src/features/tools`、`src/features/studio` 和统一 `src/lib/api.ts`、`src/types/studio.ts`。
- 新增 SQLite `agent_versions` 不可变记录，含 UPDATE/DELETE 阻断触发器、乐观锁 `expected_version`、冲突 409 和缺失 404；旧库幂等回填、历史 Run 保留。
- Run 创建与取 Agent 当前版本的过程在 SQLite 单事务中完成；每条新 Run 绑定具体 `agent_version_id` 和 `spec_snapshot`，更新 Agent 不影响正在排队或执行中的 Run。
- Web 的 Agent 详情可以查看不可变版本历史、更新目标并提交新的配置版本；不是“公开发布/灰度发布”功能。
- 新增后端 Ruff 静态检查与格式配置、前端 ESLint/Prettier 与 CI 规则，构建与测试在本地执行。

## 2. 自动验收事实

| 项目 | 实际执行或验证 | 结果 |
| --- | --- | --- |
| 后端回归 | Python unittest discover（包含新增 Agent 版本与历史迁移用例） | 31 项通过 |
| 历史 SQLite 迁移 | 创建旧表与旧 Run、重复 initialize、验证回填与原历史 Run | 通过 |
| 版本并发控制 | v1 → v2、过期 v1 更新返回 HTTP 409 | 通过 |
| 版本不可变性 | UPDATE/DELETE agent_versions 被 SQLite 触发器拒绝 | 通过 |
| Run 快照 | v1 Run 排队后创建 v2，Run 仍绑定 v1 配置及版本 ID | 通过 |
| 跨进程 Smoke | API → SQLite → 独立 Worker → LangGraph → 已注册工具 → Checkpoint | 通过 |
| 架构边界 | Python AST 依赖检查、Next 页面薄入口 | 通过 |
| Python 质量 | Ruff lint + format --check | 通过 |
| Web 质量 | ESLint、Prettier、TypeScript、Next.js production build | 通过 |

**注意：** Smoke 中的大模型接口是本地 HTTP 协议模拟器，并不是实际供应商调用。GitHub Actions 在未推送远程前未运行；未执行真实浏览器用户操作 E2E、负载测试或生产级安全评估。

## 3. 不包括的能力和上线阻断

- 正式用户登录、RBAC、工作空间及多租户隔离；当前本地 API 无认证，仍只允许 loopback 请求。
- PostgreSQL/Alembic 与生产级版本部署机制。当前 Agent 版本仅在 SQLite 单机开发数据库中落地。
- Redis Worker、正式分布式租约、幂等外部写工具、审批、沙盒。
- 实际 LLM 供应商联机、模型 token/费用计量、长期记忆、联网搜索/PDF/MCP、正式 Eval 与可视化画布。
- 真实浏览器 UI E2E 和无障碍验收；目前只有前端静态检查与 production build。

没有上述认证隔离与安全能力，**严禁公网部署**。

## 4. 推荐下一步

按照以下依赖推进：`正式 Workspace/Auth` → `PostgreSQL + Alembic 不可变版本迁移` → `真实模型授权接入` → `受控 Tool Gateway` → `生产级 Worker 与幂等审批` → `Eval/发布与计费`。每个阶段做真实数据迁移与回归，不要在未验收时宣称生产可用。

## 2026-10-10 v0.4 本地工作空间权限实施补充

新增 localhost 本地邮箱/密码认证、scrypt 密码哈希、不可逆会话令牌摘要、HttpOnly SameSite=Strict Cookie、个人与多个工作空间，以及 owner/admin/member/viewer 访问规则。服务端按 workspace 过滤 Agent、版本、Run、事件和检查点，未归属的旧资料保留但隔离。前端加入登录和空间切换，并使用 Next.js 同源 BFF 代理。

新增后端多用户/跨空间/权限/Session 生命周期负向测试，且保留 LangGraph 独立 Worker 集成；Next→FastAPI 同源代理的 HTTP Cookie 链路独立验证（非真实浏览器点击/非正式安全评估）。完整测试数量及最终代码提交以本轮交付结果为准。详情：docs/11_WORKSPACE_AUTH.md。

## 2026-10-10 v0.5 PostgreSQL 数据库底座实施补充

选择 PostgreSQL 17 + pgvector 为最终业务主数据库。新增 SQLAlchemy ORM 模型（12 张表）、第一版 PostgreSQL 专用 Alembic 迁移、JSONB AgentSpec、不可变版本触发器、工作空间复合外键、RunEvent 只追加约束、RLS 角色及权限策略。未来检索使用 pgvector，Redis 单独处理队列/缓存，文件交由对象存储。

工程新增 PG17 开发 Docker Compose、离线迁移测试、事务内 SET LOCAL 范围帮助函数、SQLite 旧数据只读统计工具及仅限本地数据库的 RLS 真实集成测试脚本。CI 新增 pgvector/pg17 容器真实 RLS 测试 Job，尚未推送远程执行。数据库角色受 NOLOGIN/NOBYPASSRLS 限制，不授权普通运行角色访问会话/用户/隔离旧数据。

目前 Docker Desktop Linux Engine 在目标 Windows 环境中未启动，未执行真正 PG 建表或 RLS 在线测试。原 FastAPI、Next.js 和 LangGraph Worker **仍使用 SQLite**；设置 STUDIO_DATABASE_BACKEND=postgres 会 fail-closed 而非假装已经完成迁移。需按照 docs/12_POSTGRES_DATABASE_V05.md 完成 DB2–DB5 实际验收后切换。

本地测试记录：Python 50/50 项通过（含新增 PG schema、Alembic offline、权限配置与安全迁移评估）；已生成可供 DBA 检查的离线 PG17 建库 SQL；Docker Compose config、Ruff、架构约束、Next ESLint 与 build 通过。GitHub Actions postgres-rls-integration Job 已编写但未经远程执行，因此不能声称在线 PG RLS 测试已通过。

## 2026-10-10 v0.6 PostgreSQL 适配器与 SQL 安全修复

修复 PostgreSQL Alembic 初始版本中 `END $;` 少一个分隔符的语法错误；新增 `pglast` 依赖，在 CI 用 PostgreSQL 解析器直接检查整份升级 SQL（此前仅字符串包含检查可漏过错误）。

新增 `app/infrastructure/postgres/scoped_store.py`，实现当前工作空间内 Agent、AgentVersion、Run、初始 RunEvent 的事务内 SQLAlchemy CRUD 和乐观版本控制，封装清晰的业务范围过滤；在 `transaction.py` 强制验证非管理员数据库登录，使用 `SET LOCAL ROLE agentstudio_runtime` 和可信 UUID 会话作用域。新增 Mock/SQL 编译层面的事务、版本、权限负向测试，并扩展未来真正 PostgreSQL CI smoke 的 owner / viewer / Cross-Workspace 场景。

现有 SQLite API/Worker **保持运行并拒绝未经验证的 PostgreSQL 后端标志**。由于此 Runner Docker Engine 未安装/不可连接，真实 PG17 RLS、实际迁移、身份服务切换、Worker/Checkpoint 与旧数据库导入仍未验收。详见 `docs/13_POSTGRES_V06_ADAPTER.md`。

本地最终测试：Python unittest 60/60 项通过；Alembic 整份迁移 SQL 共 92 条语句通过 PostgreSQL 语法解析；Ruff、架构边界、前端 ESLint + Next.js 生产构建及两条原有 HTTP 模拟服务 Smoke 验证通过。CI PostgreSQL 真机集成测试额外覆盖 PgScopedStore 的 Agent v1→v2、Run/Event、viewer 禁止写与跨工作空间隔离；但尚无真实 PG 在线日志，DB2 保持未验收。

## 2026-10-10 宝塔服务器 PG17 真实连通性确认（后续增量）

用户已在宝塔服务器建成独立 `agent_studio` PostgreSQL 17.11 容器并启用 pgvector 0.8.7，Windows 通过独立 SSH 账号的 `127.0.0.1:15429` 本地隧道已成功使用 `studio_migrator` 账户登录数据库。此项是由用户亲自操作并确认的连接事实；**尚未代表 Alembic 迁移成功或 RLS 在线测试通过**。

在仓库新增 `scripts/check_postgres_staging.py`（只读预检）与 `scripts/pg_staging.ps1`（不回显输入密码、限定数据库与端口，分 Check/Migrate/RlsTest 三个步骤），并调整 `scripts/smoke_postgres_rls.py` 支持 SSH 隧道端口。单测只使用模拟数据库连接，不使用用户密码，检测错误目标、未知表、非法 Alembic 状态时拒绝修改。操作说明见 `docs/14_BAOTA_PG17_TUNNEL_MIGRATION.md`。

## 2026-10-10 模型连接中心（V0.7 本地新增）

新增工作空间级模型连接 Web 工作台、22 个供应商/自定义入口、OpenAI 兼容及 Anthropic/Gemini 原生协议适配、在线模型列表发现、真实推理测试与 1/3 次样本测速。配置使用 SQLite 工作空间隔离与 Fernet 本地凭据加密，仅工作空间管理员可以修改密钥或发起可能计费的探测。AI 草稿与 Worker 已读取模型连接，新建 Run 绑定当时的连接 ID 和模型 ID。

回归验收：本地 76 项 Python unittest 全部通过；原有 LangGraph 跨进程模拟供应商执行通过；Next.js lint/typecheck/build 与架构边界检查通过。真实供应商尚无 API Key 可验证，此阶段不宣称外网模型已联通或测得真实厂商速度。详见 docs/15_MODEL_CONNECTIONS.md。生产安全、PG17/RLS 迁移、计费/配额/TTFT、强制 egress 和 KMS 留待后续。

## 2026-10-10 WebCodex 连续执行机制诊断

新增 `docs/21_WEBCODEX_CONTINUOUS_EXECUTION.md`，以仓库记录关联 Goal `wc_goal_0BvtHuyXwSJoWnkM`，保存连接阻断、ACP 历史执行证据、非终结 run、复验步骤和可恢复单任务队列。MCP manifest/runtime_status/list_jobs 均返回 `tunnel_client_not_seen`，因此 provider 当前认证、Host Endpoint `production_auto_resume_available` 和服务端活跃 Job 阻断状态仍未知，远端 Goal 关联未确认。用户当前唯一连接操作是恢复 WebCodex Desktop 的现有 tunnel-client 与本会话连接；没有证据要求重新登录 Coding Agent。

本轮保留所有既有更改，未启动新 Coding Agent，未改业务代码或执行外部写入。Ruff lint 通过；format --check 指出 evaluation.py 和 llm.py 两处既有格式问题。Python unittest、架构检查与 smoke_process.py 已尝试，但 venv 引用的 Python 3.14 解释器不存在，均未执行；不沿用旧轮次通过结果。当前进程补充 Node PATH 后 Web lint/typecheck/build 通过。后续先复验连接和非终结 run，避免重复派发，再修复验证环境并按 read→edit→test→review→checkpoint 推进一个最小格式修复任务。此记录不代表后台连续执行或生产安全验收通过。

## 2026-10-10 S2 确定性离线 Eval 与独立回归验收

此前独立 Coding Agent 已在 `services/api/app/domain/evaluation.py` 实现标准库 EvalCase/EvalResult/EvalSummary/evaluate（精确答案、证据缺失、不可比较、空结果、多重失败归因与硬门槛），新增 `services/api/tests/test_evaluation.py` 的 8 个测试和 `docs/20_S2_EVALUATION_FOUNDATION.md`。本轮不重派已完成 Codex Run，复查这些成果并真实执行目标 8/8、全套 116/116 Python unittest 通过，Ruff 全项目 lint 与架构检查通过。Python 3.14.3 虚拟环境本轮实际可执行，与上一轮故障记录不同。

质量门禁尚未全部绿灯：`ruff format --check .` 仍因其他未提交开发文件 `app/llm.py:138` 不通过；Smoke/本轮 Web 验证因执行平台的安全检查拦截，没有本轮通过证据。真实模型、Postgres RLS、租户级 Eval API、发布审批和 C08 完整链路均未验收。原 Codex Run 已 completed/end_turn，但持久 AgentTask reconciliation `outcome_unknown`，需先修复状态不一致、避免双重派发。未改动其他源码或工作区中的用户改动，没有 commit/push、公开部署、收费 API 和生产数据操作。

## 2026-10-10 S2 格式门禁修复

仅格式化 services/api/app/llm.py 的一行字符串拼接，未更改执行逻辑。独立验证：Ruff check/format 均通过，Eval 8/8、全量 116/116 unittest 通过，架构检查 PASS；本地 Stub Smoke PASS，Web lint/typecheck/build PASS。旧 Eval AgentTask outcome_unknown 未重派；真实 LLM、PG17 与 C08 尚未验收。未推送、部署或使用收费 API。

## 2026-10-11 Eval gate 测试格式修复

仅调整 tests/test_evaluation_gate.py 六处换行。修改后 Ruff format 全部64文件通过，评测门槛11项、后端127项测试通过，模拟 Smoke、Web lint/build 通过。Ruff lint、架构检查的修改后复验及独立 Web typecheck 受平台安全检查阻断。未进行真实模型调用，C08仍未完成。Goal checkpoint rev12。

## 2026-10-11 S2 C08 评测套件完整性门槛

新增 app/domain/evaluation_suite_integrity.py、11 个离线契约测试 tests/test_evaluation_suite_integrity.py 与 docs/23_EVALUATION_SUITE_INTEGRITY.md。为现有评测回归门槛增加不可变套件 manifest 和规范 SHA-256 指纹：标准答案、证据条件、套件版本变化均阻断；结果集遗漏和不一致均阻断。用例/证据顺序变化不会误阻断。模块仅输出只读报告，不发布、不授权。

本轮实测：新增测试 11/11 通过；后端完整 unittest 138/138 通过（24.153 秒、exit=0）；Ruff lint 全部通过、Ruff format 66 文件通过、Python 架构边界 PASS；跨进程 LangGraph Stub Smoke PASS；Web lint、typecheck、Next.js build 全部通过；Git diff --check exit=0。

原 Eval 持久任务仍为 outcome_unknown，reconcile 未获得权威 terminal，历史 Run 的直接 observe 返回 unknown_coding_agent_run，没有重新派发或触碰其文件。新的 Codex ACP 编码委托受到执行平台安全检查拦截，改用 WebCodex 本地结构化编辑完成独立工作包。此进度不代表真实 LLM 评测、C08 发布审批/回滚或 PG17/RLS 验收。没有 commit/push、部署、收费 API 或生产数据操作；已有未提交修改全部保留。

## 2026-10-11 S2 C08 离线 Eval 批次回放与输入完整性

新建 `services/api/app/domain/evaluation_batch.py`、`services/api/tests/test_evaluation_batch.py`、`docs/24_EVAL_BATCH_REPLAY.md`。`EvalSubmission` 保存每条原始答案及证据标识；`replay_eval_batch` 验证 EvalSuiteManifest 与提交严格一对一、拒绝重复/缺失/新增 ID 和非法类型，按 case_id 稳定顺序调用已有 evaluate() 生成 EvalSummary。未修改原 Eval/Release Gate/Suite Integrity 代码，也未对旧 outcome_unknown 持久任务重派；无数据库/网络/付费模型调用。

本轮实际本地验收：新增批次回放契约测试 14/14 通过；Python 全量 unittest **152/152** 通过（24.603 秒、exit=0；测试的失败路径在 stderr 输出预期异常堆栈，但测试整体 OK）；Ruff 全项目 lint 通过、format --check **68 文件**通过，Python 架构检查 PASS；LangGraph 跨进程 Smoke PASS（本地 HTTP stub，非真实 LLM）；Web lint、typecheck、Next.js build 全部通过。最终 Git diff --check exit=0（已修复本轮新增日志末尾的多余空行）。

真实 Codex ACP 编码委托遭平台 safety checks 拦截，使用已授权 WebCodex 结构化编辑完成新文件。该模块只做**给定输入的离线确定性重放**，不能证明输入来自可信执行器，也不连接版本发布/审批/回滚，不构成 S2 G2/C08 验收。仍需后续可信评测执行器、结果存储不可篡改、租户权限与回滚闭环。保留全部既有未提交修改；无 git commit/push、公网部署、生产 PG 写入/迁移/删除或任何外部写入。

## 2026-10-11 S2 C08 离线执行记录作用域对账

新增 app/domain/evaluation_execution_binding.py、tests/test_evaluation_execution_binding.py 和 docs/25_EVAL_EXECUTION_BINDING.md。逐项对照上层提供的预期 workspace/Agent/版本/Run 作用域、EvalSuiteManifest 指纹、完整用例和答案/证据，结果仅由服务端记录离线重算；不独立证明记录来源，不授予发布权限。新增契约测试 24/24 通过；全套 Python unittest 176/176 通过（24.934 秒，exit 0）；Ruff lint、format（70 文件）、架构检查通过；本地模拟 Smoke 和 Web lint/typecheck/build 通过。Codex 委托受平台检查阻断，使用 WebCodex 结构化编辑；旧 Eval outcome_unknown 未重派。未推送/部署或接触生产数据库；真实 LLM、可信执行器、审批和回滚仍未验收。

## 2026-10-11 S2 C08 评测应用层只读授权与撤权边界

新增 `app/application/evaluation_read_service.py`、`tests/test_evaluation_read_service.py`、`docs/26_EVAL_AUTHORIZED_READ.md`。新用例先通过可信存储端口查验 workspace 成员角色，再读取同一身份与 workspace/run 绑定的权威快照；要求 adapter 在原子只读快照查询中再次校验 membership，减少撤权期间的 TOCTOU 风险。严格核对 Agent/版本/Run、套件指纹、答案及证据，非成员/未知角色/缺失快照/作用域不一致均 fail closed。仅返回离线对账结果，不授权发布。

验收：新增目标 unittest **16/16**，后端全量 **192/192**（27.514s，exit=0）；Ruff check PASS、Ruff format --check **72 files**，架构边界 PASS；跨进程本地 HTTP Stub Smoke PASS；Web lint/typecheck/Next build PASS。先发现两处 Ruff 格式差异，已只修复本轮两个新文件并重新全部通过。后端测试有故意触发的 LangGraph 失败路径堆栈，但测试总结果为 OK。所有真实模型/PG17 RLS/生产身份仓储/发布回滚仍未验收；目前的 EvalReadStore 只有契约与测试 FakeStore，尚无可用于真实 HTTP 流程的可信持久化适配器。

Runner 在线，codex provider 已注册，开始前 0 活跃 Job；旧 expired/outcome_unknown Eval AgentTask 未重派。本轮 Codex 启动被执行平台 safety checks 拦截，改用 WebCodex 已授权本地结构化编辑。无 push/commit、公网部署、真实计费 API 或生产数据库读写/迁移/删除。保留全部用户原有未提交修改。

## 2026-10-11 S2 C08 SQLite 本地评测快照只读仓储（staging）

新增 `app/infrastructure/sqlite/evaluation_read_repository.py`、`tests/test_evaluation_sqlite_read_repository.py` 与 `docs/27_EVAL_SQLITE_READ_ADAPTER.md`。基于原有 `EvalReadStore` 协议实现开发用 SQLite 只读快照装载：每次验证服务端查询成员角色，在同一只读事务重新核验成员资格并联合约束 workspace/Agent/version/Run，严格解析 suite 与提交 JSON，校验 SHA-256 指纹、用例完整性及终态 Run。非法/篡改数据 fail closed。仓储只读取，**未增加实际数据库迁移或可信写入链路**，仅在临时 SQLite 测试数据库中创建所需表。

先运行新增目标测试发现一项测试隔离问题：3 个子场景在另一个连接中读取未提交的更新，导致 3 次预期失败。将测试改为先提交变更、校验独立连接、再复原数据后，新增目标 **16/16 PASS**，后端全量 **208/208 PASS**（25.186s，exit 0）；Ruff check PASS、format --check **74 files**、架构边界 PASS；LangGraph 跨进程 Stub Smoke PASS；Web ESLint、typecheck、Next production build PASS。Git 工作区旧变更保留。本轮真实 Codex ACP 新委托遭 safety checks 拦截，改用结构化项目编辑；原 Eval AgentTask reconcile 仍为 outcome_unknown，其 Run 直接 observe 返回 unknown_coding_agent_run，未重复派发。

新适配器未挂入 HTTP、未真实部署或操作生产/现有 SQLite 数据库；套件与执行记录无可信写入来源证明，PG17/RLS、生产鉴权、发布审批与回滚、真实 LLM 仍未验收。无 git commit/push、公网部署、计费 API 或其他外部写入。

## 2026-10-11 S2 C08 SQLite 仓储负向契约加固

仅在已有的 `services/api/tests/test_evaluation_sqlite_read_repository.py` 增补 4 项本地契约测试：SQL 注入形式的 user/workspace/run 标识不得扩展查询范围；超过 1 MiB 的持久化 JSON 必须拒绝；提交 JSON 重复键、非数组 evidence 必须 fail-closed。未改动业务实现、真实 SQLite 数据或表结构，也未启用任何 API/发布流程。

新增后仓储定向测试 **20/20 PASS**；使用项目 `services/api/.venv/Scripts/python.exe` 的后端全量 unittest **212/212 PASS**（24.281s，exit 0，负向用例的预期异常有测试日志）；Ruff check PASS、format --check **74 files**、架构检查 PASS；LangGraph 跨进程本地 HTTP Stub Smoke PASS；Web ESLint、typecheck、Next.js build PASS。注意：直接调用系统 Python 的全量测试曾因缺少 SQLAlchemy 等项目依赖而失败（120 tests, 1 failure/13 errors），系统 Python 也缺少 Ruff；切换已有项目虚拟环境后重新验证成功，不应把前一轮失败视为产品回归。

Runner 在线且注册 codex provider。新 Codex 委托仍被执行平台 safety checks 拦截，故仅用 WebCodex 结构化项目编辑完成本轮改动；旧 Eval AgentTask 为 expired/outcome_unknown，未重复执行。真实 LLM、PG17/RLS、可信快照写入、正式 HTTP Scope、发布审批及回滚仍属 S2 阻断；本轮没有 git commit/push、公网部署、收费 API 或生产数据变更。

Goal checkpoint 尝试更新 rev17→18 时受到执行平台 safety checks 拦截；其后通过只读 get_goal 核实仍为 **revision 17**。本轮进度已保存于此日志，持久 Goal 待下一次安全授权的 checkpoint 更新。

## 2026-10-11 S2 C08 SQLite Eval 持久化 JSON 非有限数拒绝

在 `app/infrastructure/sqlite/evaluation_read_repository.py` 的只读快照解析中新增 `parse_constant` 严格拒绝 `NaN`、`Infinity`、`-Infinity`，避免 Python `json.loads` 默认容忍 JSON 非标准数值；违规输入仍按既有仓储约定映射为作用域内 `LookupError`，递归异常处理保持不变。原契约测试文件新增两项负向测试，覆盖解码器直接拒绝和持久快照拒绝。变更仅涉及该模块、对应测试和本日志；没有数据库迁移/可信写入/HTTP 或发布操作。

本轮定向 SQLite Eval 仓储测试 **25/25 PASS**，项目 `.venv` 后端完整 unittest **217/217 PASS**（包含预期故障注入日志），Ruff check 与 format --check **74 files** 通过，Python 架构检查通过；LangGraph 跨进程本地 Stub Smoke、Web lint/typecheck/build 通过，`git diff --check` 成功。最初误用系统 Python 导致 Ruff 缺失、全量测试因缺少 LangGraph/pgvector 等依赖失败；切换项目 `.venv` 后重新验证上述通过结果。未据此宣称真实 LLM、PostgreSQL RLS 或生产级 C08 验收。

本轮开始前 Runner online、codex provider 已注册、0 active jobs，Goal 为 revision 18；旧 Eval 持久任务仍 expired/outcome_unknown，未重派。新 Codex 委托被执行平台 safety checks 拦截，使用允许的 WebCodex 结构化编辑完成。所有历史未提交文件保留，无 git commit/push、公开部署、生产数据库操作、付费模型或外部写操作。

## 2026-10-11 S2 C08 SQLite Eval 非法 Unicode 代理项拒绝

修复本地 SQLite 评测快照 `_decode()` 遇到未配对 UTF-16 surrogate 时泄漏 `UnicodeEncodeError` 的问题：原始非法代理项与 JSON 转义后解析出的非法代理项均转换为 `ValueError`，仓储读取沿用现有 `LookupError` 拒绝访问语义。仅改动 `services/api/app/infrastructure/sqlite/evaluation_read_repository.py` 和对应契约测试文件；新增 3 项测试，覆盖原始/转义代理项、持久化 suite/submission 与正常多语言 Unicode。未改变其他业务流程或数据库结构。

本轮 Codex ACP 委托 `wc_agent_run_mlQJsDeWDixgbfkkha5C1-uYDFkjHRPryc4KRsKX55A` 已由 `coding_agent_observe` 确认为 **completed/end_turn**；随后独立修复目标测试文件的 2 处 Ruff 格式问题并复测。目标 SQLite Eval 单测 **31/31 PASS**；格式修复后后端完整 unittest **223/223 PASS**（包含预期错误路径日志）；Ruff lint PASS、Ruff format --check **74 files**，架构检查 PASS；跨进程本地 Stub Smoke PASS；Web lint/typecheck/production build PASS；`git diff --check` 无报错。Codex 输出较多 tool_activity，但已权威终结，未重复派发。

旧 Eval AgentTask 仍为 expired/outcome_unknown，历史与前轮已终结任务没有重复派发。Git 工作区 48 个既有改动仍保留，未执行 commit/push、公网部署、生产数据库操作、外部写入或真实收费模型调用。该加固不代表可信 Eval 结果写入、正式 HTTP 权限、PG17/RLS、发布审批回滚或真实 LLM 验收已完成。

## 2026-10-11 S2 C08 开发日志末尾空行门禁修复

只修复 `docs/10_IMPLEMENTATION_CHANGELOG.md` 最末多余空行，并新增本条可恢复验收记录。修复前 `git diff --check` 明确报第 165 行 `new blank line at EOF`；Codex provider 的独立小范围 Run `wc_agent_run_LNSgVHoa8YLTi_jTj33RDvUzB-Yl3xJZjoBTy1-S5t8` 实际 `completed/end_turn`，但未完成该清理；随后 WebCodex 结构化编辑删除第 165 行。独立复验 `git diff --check` 退出 0，Python 只读字节检查确认文件恰好一个末尾换行。此项仅为 Markdown 质量门禁，不修改运行代码，故本轮不把前次 223 项 Python 测试等历史成功冒充本轮复测。

旧 Eval AgentTask 仍 `expired/outcome_unknown`，未重复派发。全部 48 个既有未提交/未跟踪工作区路径保留；无提交、推送、部署、模型费用或数据库操作。C08 可信评测写入、正式 HTTP 权限、PG17/RLS、发布审批与回滚、真实 LLM 验收仍未完成。

## 2026-10-11 S2 C08 同租户 Run 关联漂移负向契约

仅向 `services/api/tests/test_evaluation_sqlite_read_repository.py` 新增 2 项隔离 SQLite 契约测试，覆盖已存 Run 被改绑到同 workspace 其他有效 Agent/版本、或同 Agent 其他有效版本时，服务端 `read_reconciled_evaluation()` 必须拒绝与原始 EvalRunScope 不一致的读取。用例同时断言仓储能确实读到新的关联 Scope，防止用空查询的假阳性冒充安全验收；未修改业务代码、迁移或真实数据。

本轮新增目标测试 **33/33 PASS**；后端全量 unittest **225/225 PASS**（28.829s，预期故障注入堆栈不构成测试失败）；Ruff check PASS，format --check **72 files PASS**，架构边界 PASS；LangGraph 跨进程本地 Stub Smoke PASS；Web lint/typecheck/build PASS；执行前 `git diff --check` PASS。仅是离线 SQLite 负向契约覆盖，不代表 Run 关联允许被生产修改，也不代表可信写入、正式 HTTP 授权、PG17/RLS、发布审批/回滚或真实模型已验收。

开始前 Runner online，codex provider 已注册且 0 active jobs；Goal rev22，旧 Eval AgentTask expired/outcome_unknown 未重派。单项 Codex 委托被平台 safety checks 拦截，改用 WebCodex 结构化编辑。本轮原有 16 modified +32 untracked 路径保留；`credentials.py` 仅因未跟踪文件名触发 workspace hygiene 的启发式告警，不读取其内容，也不声称已核实密钥。无 git commit/push、公开部署、真实收费模型 API、外部写操作或生产数据库操作。

## 2026-10-11 S2 C08 Run 状态白名单契约测试

仅在 `services/api/tests/test_evaluation_sqlite_read_repository.py` 新增 2 项本地 SQLite 只读仓储契约测试：PARTIAL / FAILED 终态 Run 可读取一致性评测快照；DRAFT / QUEUED / RUNNING / CANCELLED / 含尾部空格的非精确状态不得读取。每个子用例均恢复原 Run 状态；不调整业务实现或迁移，测试采用临时 SQLite、无网络或真实模型调用。

本轮 SQLite 仓储定向测试 **35/35 PASS**；后端完整 unittest **227/227 PASS**（30.884s，预期故障注入堆栈仍见于日志）；Ruff check、format --check **72 files**、架构边界检查 PASS；LangGraph 跨进程本地 HTTP Stub Smoke PASS；Web lint、typecheck、build PASS；`git diff --check` PASS。此项证明的只是本地状态筛选契约，不能代替可信评测写入、正式 HTTP 授权、PG17/RLS、发布审批/回滚及真实模型验收。

预检 Runner online、codex provider 已注册、0 active jobs；Goal revision 23，既有 Eval Attempt `expired/outcome_unknown`。本轮调用 `reconcile_agent_task_coding_run` 回报既有绑定 Run `running/outcome_unknown` 且未改变任务状态；CodingAgentRun 直接观察返回调用方不可见，未据此判断执行已结束、未重派该任务。新的单项 Codex 委托受到执行平台 safety checks 拦截，未取得新 Run ID，故使用 WebCodex 项目结构化编辑完成。既有 16 modified +32 untracked 均保留，无 git commit/push、公网部署、付费 LLM、外部写入、真实生产数据库操作。

## 2026-10-11 S2 C08 越权访问损坏 Eval 快照契约

仅在 `services/api/tests/test_evaluation_sqlite_read_repository.py` 新增 2 项负向测试：当 `manifest_json` 已损坏时，撤销成员资格的调用者不应获知解析错误，服务层应拒绝；仅属于其他 workspace 的调用者也不能读取或从错误中推断原工作空间的快照。使用临时 SQLite，不调用真实模型、不修改业务实现或生产数据库。

Codex ACP Run `wc_agent_run_A53GrhLbz0QwD0S1ie3py0IpEXwfA26URMfXUdCeAAQ` 最终由观察确认 `completed/end_turn`；中途曾短暂返回 `outcome_unknown`，仅观察同一 Run，未重复派发。因其模型消耗高，尝试取消但被 safety checks 阻断。独立测试：定向 **37/37 PASS**，后端全量 **229/229 PASS**（故障注入日志预期），Ruff lint/format --check **74 files PASS**、架构检查、LangGraph 本地跨进程 Stub Smoke、Web lint/typecheck/build、`git diff --check` 均通过。新增测试文件仍为 untracked，git review 不含其 diff，已通过前后 `read_files` 核对确实新增两个方法。

旧 Eval AgentTask `expired/outcome_unknown` 未重派；保留原 16 modified +32 untracked 路径，未 git commit/push、公开部署、操作生产数据库或执行外部写入。`production_auto_resume_available=false`。可信 Eval 写入、HTTP 授权、PG17/RLS、发布审批与回滚、真实 LLM 均未验收。

## 2026-10-11 S2 C08 评测只读角色白名单契约

仅向现有未跟踪测试 `services/api/tests/test_evaluation_sqlite_read_repository.py` 新增 1 项隔离 SQLite 契约测试：owner/admin/member/viewer 成员角色更新后均能读取相同工作空间的合法评测快照并经 application 服务重新核对；切换至未授权 guest 角色后 member_role、load_snapshot 和服务端读取均拒绝。每个角色更改都先提交到临时数据库，未改业务实现、迁移、生产数据或 HTTP 接口。

独立验证：仓储定向 unittest **38/38 PASS**，后端全量 **230/230 PASS**（28.281s；预期故障注入日志不代表失败），Ruff check、format --check **72 files**、Python 架构检查 PASS；LangGraph 本地跨进程 HTTP Stub Smoke PASS（非真实 LLM）；Web lint/typecheck/build PASS，`git diff --check` PASS。测试文件为 untracked，不被普通 `git diff --check` 覆盖，已用 read_files 与 Ruff 验证新增内容。

预检 Runner online、codex provider 存在、0 active jobs，Goal rev24；历史 Eval AgentTask 仍 expired/outcome_unknown，未重派。新 Codex ACP `wc_agent_run_B-GlbgUzOiOXrCftoSbe0VQhmqfI1i2iYswcaM85onE` 运行期间用量观察达 **29,554 tokens**，为控制费用主动请求取消并确认最终 `cancelled`，不能记为完成任务；随后使用 WebCodex 本地结构化编辑完成唯一测试修改。原有全部 48 项未提交/未跟踪路径保留，无 commit/push、公网部署、外部写操作、真实模型调用或生产数据库更改。C08 可信写入、正式 HTTP 权限、PG17/RLS、发布审批/回滚与真实 LLM 验收仍阻断。

## 2026-10-11 S2 C08 Unicode 转义 JSON 字段名重复契约

仅在既有 SQLite Eval 测试文件中新增 1 项负向契约测试：合法评测套件与提交 JSON 里使用原名及等价 Unicode 转义字段名，即使二者的值完全相同，也必须拒绝重复键，返回通用 `LookupError`。覆盖 suite manifest 和 submissions 两个存储字段；只操作临时 SQLite，未改仓储代码、迁移、真实数据或 HTTP 接口。

定向 SQLite Eval unittest **41/41 PASS**，后端全量 **233/233 PASS**；Ruff check/format（74 files）、架构检查、跨进程本地 HTTP Stub Smoke、Web lint/typecheck/build、`git diff --check` 全部通过。`git diff --check` 不覆盖 untracked 测试文件，已另用 Ruff 与单测验收该文件。

Runner 在线，codex provider 已注册；旧 Eval AgentTask 仍 `expired/outcome_unknown`，未重派。新 Codex 委托被平台 safety checks 阻断，无新 Run ID；通过 WebCodex 结构化编辑完成。原有 16 modified + 32 untracked 路径保留；未 push、部署公网、操作生产数据库、调用收费模型或执行外部写入。`production_auto_resume_available=false`，S2 C08 可信快照写入、HTTP 鉴权、PG17/RLS、审批及回滚、真实 LLM 仍未验收。

## 2026-10-11 S2 C08 多用例 Eval 重放排序不变性契约

仅在现有隔离 SQLite Eval 仓储测试中新增 1 项集成契约：同一两用例套件、同一真实持久化快照与提交记录，在 cases/submissions 输入顺序反转后，suite fingerprint 和可复现评分摘要保持一致；一项答错、一项答对时，结果按 case_id 稳定排序，失败归因正确，hard_pass 必须为 false。只操作临时 SQLite；不增加 API、数据库迁移或外部模型调用。

本轮独立验收：目标 SQLite Eval unittest **43/43 PASS**，后端全量 **235/235 PASS**（exit 0；故障注入预期日志）；Ruff check PASS、format --check **74 files PASS**、Python 架构检查 PASS；跨进程本地 HTTP Stub Smoke PASS（不是供应商真实 LLM）；Web lint/typecheck/build PASS；`git diff --check` PASS。测试文件是 untracked，故另通过 read_files + Ruff + unittest 验证新增方法，不能只依赖 git diff。

预检 Runner online，codex provider 存在且无活动 Runner Job；持久 Goal revision 26；既有 Eval AgentTask latest_attempt 仍 expired/outcome_unknown，已完成/失败任务未重派。为避免此前 ACP 高 Token 消耗，在禁止高费用 API 的约束下仅通过 WebCodex 结构化本地编辑完成本轮；既有 16 modified +32 untracked 全部保留。未 git commit/push、公开部署、生产数据库操作、密钥读取、收费模型调用或对外写入。S2 C08 可信快照写入、HTTP 授权、PG17/RLS、发布审批/回滚和真实 LLM 仍是未完成项；production_auto_resume_available=false。

## 2026-10-11 S2 C08 Eval 套件内容篡改与指纹绑定契约

仅修改现有未跟踪文件 `services/api/tests/test_evaluation_sqlite_read_repository.py`：增加 1 项负向契约测试，共覆盖 4 种测试数据库快照篡改（suite_id、version、expected_answer、required_evidence），保留原有 manifest_fingerprint 不变时读取必须返回通用 LookupError，底层明确触发 suite fingerprint mismatch；各子例恢复原始快照，最终确认合法快照仍能读取。没有修改生产逻辑、数据库迁移或真实数据。

独立验收：SQLite Eval 定向 unittest **44/44 PASS**；后端全量 **236/236 PASS**（exit=0，测试故障注入预期堆栈）；Ruff check PASS、format --check **72 files**、Python 架构检查 PASS；LangGraph 跨进程本地 HTTP Stub Smoke PASS，Web typecheck PASS，`git diff --check` PASS。**Web 质量门禁并非全绿**：初次 `npm run lint` 遇 Node Zone Allocation out-of-memory 并崩溃；随后 `node --max-old-space-size=1536 node_modules/eslint/bin/eslint.js .` 退出成功。`npm run build` 虽编译成功，但独立 TypeScript 阶段 Node OOM，设置 `NODE_OPTIONS=--max-old-space-size=1536` 后重试仍 OOM，**build 未通过**，不得记作完成。需要后续在更充足内存/排除环境限制后独立重测。

开工核验 Runner online、provider codex 注册、无活动 Runner Job，Goal rev27；历史 Eval Task expired/outcome_unknown，不派发重复任务；先前 CodingAgentRun 从本调用方不可见。考虑既有 ACP 高 Token 开销及禁高费用 API，本轮只执行获准的本地 WebCodex 结构化编辑和测试。原有 16 modified +32 untracked 文件路径保留；无 git commit/push、公网部署、生产数据库操作、密钥输出、收费模型 API 或外部写入。可信 Eval 写入、正式 HTTP 授权、PG17/RLS、审批与回滚、真实模型尚未验收；production_auto_resume_available=false。

## 2026-10-11 S2 C08 重复评测提交记录拒绝契约

继续复用既有 Goal/Agent/Project/Session，核验 Runner online、codex provider 注册、Goal revision 28、无活动 Runner Job；既有 48 项未提交路径保留。Eval AgentTask attempt 仍为 expired/outcome_unknown：在同一绑定上 reconcile 仅返回 run_state=running / execution_status=outcome_unknown；直接观察该 Run 返回 unknown_coding_agent_run，**没有认定为完成或重派**。

仅在现有未跟踪文件 `services/api/tests/test_evaluation_sqlite_read_repository.py` 新增 1 项 SQLite 契约测试：把合法持久化提交重复写入相同 case_id，即使答案和证据完全相同，授权读必须以通用 LookupError 拒绝；底层明确为 duplicate case_id in execution record；恢复原始数据后合法快照可继续读取。仓储生产代码、HTTP API 和真实业务数据均未修改。

最终使用项目 `services/api/.venv/Scripts/python.exe` 验证：SQLite Eval 定向 **45/45 PASS**；后端完整 unittest **237/237 PASS**；Ruff check 与 format --check（**72 files**）PASS；架构边界检查 PASS；跨进程本地 LangGraph HTTP Stub Smoke PASS（非真实 LLM）；`git diff --check` PASS。最初误用系统 Python 3.14 运行全量测试，因缺少 SQLAlchemy 等项目依赖而失败，改用项目 venv 后回归正常，不能记为业务代码缺陷。新增测试文件仍为 untracked，`git diff --check` 本身不覆盖它，已通过 read_files、Ruff 与实际运行测试核验。

**Web 完整质量门禁仍存在上一轮 Node OOM 阻断**（Next.js production build 尚未通过，本轮未重试、未宣称通过）；S2 C08 可信 Eval 写入、正式 HTTP 鉴权、真实 PG17/RLS、发布审批和回滚仍未验收。为避免既有 Codex ACP 单项委托高 Token 花费，本轮采用本地结构化编辑，没有产生收费模型调用；没有 git commit/push、公开部署、生产数据库修改、对外写入或密钥输出。production_auto_resume_available=false。

## 2026-10-11 S2 C08 评测套件版本字段 JSON 类型混淆契约

在原有未跟踪 SQLite Eval 仓储测试文件新增 1 项回归契约，覆盖 manifest `version` 为 JSON `true`、`1.0`、`"1"`、`null` 四种错误类型。即使 Python `bool` 是 `int` 子类，版本仍必须严格为 JSON 整数；授权读取统一返回 `LookupError("evaluation run not found")`，底层保留 `ValueError("version must be a positive integer")`；每个子例恢复临时 SQLite 原数据并最终重读合法套件。未修改仓储生产代码、数据库迁移、HTTP 或任何实际业务数据。

验证：SQLite Eval 定向 unittest **46/46 PASS**；项目虚拟环境后端完整 unittest **238/238 PASS**（exit 0，故障注入的预期异常日志不代表失败）；Ruff check PASS、format --check **72 files PASS**、架构边界检查 PASS、LangGraph 本地 HTTP Stub 跨进程 Smoke PASS（非真实供应商模型）、`git diff --check` PASS。测试文件为 untracked，不受 git diff --check 覆盖，已通过结构化读取与实际测试核验。未复测此前受 Node OOM 阻断的 Next.js production build；Web 质量门禁不视为全绿。

复核 WebCodex Runner online、coding_agent_providers 含 codex、既有 Goal revision 29、历史 Eval AgentTask latest attempt expired/outcome_unknown 和 Git 原有 16 modified + 32 untracked；没有派发状态不明的任务。为控制此前 ACP 高 Token 消耗并遵守不调用高费用 API 的约束，本轮使用本地结构化编辑和测试，没有调用收费模型。S2 C08 可信评测写入、HTTP 授权、PG17/RLS、发布审批与回滚未验收；未 push、部署公网、改动生产数据库、访问密钥或对外写入。production_auto_resume_available=false。

## 2026-10-12 S2 C08 SQLite Eval 快照 staging 追加写入

新增 `services/api/app/infrastructure/sqlite/evaluation_snapshot_writer.py`、`services/api/tests/test_evaluation_snapshot_writer.py` 与 `docs/28_EVAL_SNAPSHOT_STAGING_WRITER.md`。只在调用方显式 SQLite 事务中校验终态 Run、workspace/Agent/版本关联、suite 指纹与完整用例，使用现有只读仓储兼容 JSON 形状执行一次性 INSERT；重复写入、越界作用域、未结束 Run、无效套件与超大载荷均被拒绝。9 项用例全部使用独立临时 SQLite 和虚构数据，不变更迁移/业务数据库，不接入外部写操作。

独立验证：新增契约 **9/9 PASS**；后端全量 unittest **247/247 PASS**（58.711s，预期故障注入堆栈并非失败），Ruff lint PASS、format --check **76 files PASS**、Python 架构边界 PASS、LangGraph HTTP 本地 Stub 跨进程 Smoke PASS（**非真实 LLM**）。`git diff --check` 最终 PASS（修复原开发日志 EOF 多余空行）；未修改 Web 代码，既有 Next.js 构建 Node OOM 阻碍未作复测。

开工核验 Runner online、codex provider 注册、Goal revision 30；原有 Eval AgentTask 最新 attempt expired/outcome_unknown，未再次派发；另有其他会话的长时 WebCodexContinuousDev serve 进程正在运行，未终止、重试或干扰。保留原有 **16 modified + 32 untracked**，本次仅新建 3 个独立文件并追加日志。该 staging writer **不能证明数据源可信**，尚未连接受控 Worker、正式迁移/HTTP Auth/PG17 RLS/发布审批回滚，C08 不可认定完成。为控制之前 Codex ACP 高 Token 消耗，本轮未开启新的 Codex/收费 API；未 git commit/push、公开部署或操作生产数据库。production_auto_resume_available=false。

## 2026-10-12 S2 C08 Run Event → 单用例 Eval 投影（staging）

新建 `services/api/app/infrastructure/sqlite/evaluation_run_projection.py`、`services/api/tests/test_evaluation_run_projection.py` 和 `docs/29_EVAL_RUN_EVENT_PROJECTION.md`，不修改既有生产逻辑、正式迁移、路由、Worker 或数据。投影函数仅在调用方显式 SQLite 事务内，按 Run ID 查询持久化 `runs.output` 和关联的工作空间/Agent/版本、连续 Run Event 序列，要求 `SUCCEEDED`、至少一个 model_step、唯一且末尾的规范 completed 事件；不信任调用者提供的答案，不从工具拒绝事件伪造完成证据。只支持单套件单用例，按已有只允许 INSERT 的 writer 固定快照，重复写入/跨空间关联/错误或缺失事件统一拒绝。工具完成仅衍生 `run-event:<seq>` 标识，不能据此认定答案引用或外部来源可信。

本轮先发现 1 项校验顺序错误和 Ruff 格式差异，修复后全部重新验证：新增独立临时 SQLite 契约 **17/17 PASS**；后端全量 unittest **264/264 PASS**（35.637s，测试中的预期故障注入堆栈不代表失败）；Ruff lint PASS、format --check **78 files PASS**；Python 架构边界 PASS；LangGraph 本地 Stub 跨进程 Smoke PASS（**非真实 LLM**）；Web ESLint 和 TypeScript typecheck PASS。此前 Next.js production build 发生 Node OOM，本轮因 Runner 实测可用内存约 **1,780 MiB** 且前端源代码未修改，没有重复启动高内存构建；该质量门槛仍**未通过/未复测**，不能宣称全绿。最终 `git diff --check` PASS（该命令不覆盖 untracked 文件；新增文件另由读取、Ruff 与测试验证）。

开工检查：Runner online、Codex provider 已注册、Goal Revision 31、原 Eval AgentTask 最新状态 expired/outcome_unknown，未重派；无并行活跃 Runner Job，所有原有 16 modified + 35 untracked 路径保留，本轮仅新建 3 个独立文件并更新开发日志。为遵守高费用 API 限制，没有新派 Codex ACP 或调用付费模型。**SQLite Run Event 与输出并非不可变可信存储，投影仅验证同事务一致性**，不能代替可信 Worker 生成多用例评测、正式 HTTP 授权、PG17/RLS、发布审批/版本回滚或真实 LLM 验收。未 git commit/push、公开部署、生产数据库操作、读取/打印密钥及外部写入；production_auto_resume_available=false。
