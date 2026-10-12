# S2 Evaluation foundation

Scope: offline deterministic evaluation only; does not establish live model results, release readiness or production permissions.

This document tracks the isolated S2-09 development work without altering other sessions' uncommitted files.

## 2026-10-10：确定性领域基础实现

本轮仅修改本文件、`services/api/app/domain/evaluation.py` 和新增
`services/api/tests/test_evaluation.py`。已阅读 AGENTS、代码规范、验收矩阵、
本文件、根 README、现有领域模型和 unittest/架构测试结构。
Git 不在 PATH，使用 `C:/Program Files/Git/cmd/git.exe status --short` 检查工作区。
已有大量其他未提交修改，包括 changelog；全部保留。按本轮显式三文件范围，
实现与验证记录集中于本文件，不另写 `docs/10_IMPLEMENTATION_CHANGELOG.md`。
占位评测模块和本文件在写入前通过 SHA256 复核，新测试文件写入前确认不存在。

### 契约与评分

- 使用标准库 frozen dataclass，不增加依赖，不进行模型、网络、存储或真实数据操作。
- `EvalCase(case_id, expected_answer, required_evidence=())`：ID 和已知预期答案
  必须是非空白字符串；`expected_answer=None` 表示没有可比较 oracle。
  证据为不可变 tuple，其中每个标识必须为非空白字符串且不重复。
- `evaluate(case, answer, evidence=()) -> EvalResult`：答案必须为字符串，允许空白，
  但空白归因为 `empty_response`。仅去除答案两端空白后进行大小写敏感的精确比较，
  不折叠内部空白、不作语义推断。证据按原始标识精确包含比较，允许额外证据；
  标识存在仅代表调用者提供该标识，不证明来源真实或内容支持结论。
- 缺少任何必需证据归因为 `missing_evidence`；非空但答案不一致为
  `answer_mismatch`。多项原因同时保留，顺序固定为：空答复、缺失证据、
  答案不一致、不可比较。空答复不重复记答案不一致。
- 存在 oracle 且所有硬条件满足：`status=passed, score=1, reasons=()`；
  任一硬条件失败：`status=failed, score=0`，没有软评分或部分得分。
  没有 oracle：`status=incomparable, score=None`，保留空答复/缺证据原因并添加
  `not_comparable`；不将未知质量当作失败分或通过分。
- `EvalResult` 构造验证 ID、状态/分数/原因一致性、原因顺序和唯一性；
  拒绝布尔/浮点分数、未知原因和不可能的原因组合。构造错误抛出
  `TypeError` 或 `ValueError`；不自动转换字符串、列表或数值类型。
- `EvalSummary(results)` 接受结果 tuple，拒绝重复 case_id，防止重复计权。
  提供 total/passed/failed/incomparable、mean_score、hard_pass、failure_counts。
  均分分母仅含可比较项；空集或全部不可比较时为 None。
  hard_pass 仅在非空且全部通过时为 True，不可比较项不能绕过门槛。
  归因次数按原因分别累计，多原因之和可超过失败用例数；返回独立字典。

示例（仅合成文本）：

```python
from app.domain.evaluation import EvalCase, EvalSummary, evaluate

case = EvalCase("synthetic-1", "8", ("fixture:v1",))
result = evaluate(case, " 8\n", ("fixture:v1",))
assert result.status == "passed" and result.score == 1
assert EvalSummary((result,)).hard_pass
```

### 验证事实与复跑

工作目录 `services/api`，以下命令实际执行：

| 命令 | 本轮结果 |
| --- | --- |
| `.venv/Scripts/ruff.exe format --no-cache app/domain/evaluation.py tests/test_evaluation.py` | 格式化两个授权文件 |
| `.venv/Scripts/ruff.exe check --no-cache app/domain/evaluation.py tests/test_evaluation.py` | All checks passed |
| `.venv/Scripts/ruff.exe format --check --no-cache app/domain/evaluation.py tests/test_evaluation.py` | 2 files already formatted |
| `.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_evaluation.py -v` | 启动失败，未运行测试 |
| `.venv/Scripts/python.exe -B ../../scripts/check_architecture.py` | 启动失败，未完成架构检查 |
| `.venv/Scripts/python.exe -B ../../scripts/smoke_process.py` | 启动失败，未运行协议模拟 Smoke |

Python 阻碍：venv 引用的
`C:/Users/Administrator/AppData/Local/Python/pythoncore-3.14-64/python.exe`
不存在，提示 `did not find executable`。本轮没有安装依赖或修改环境，不能制造
unittest/Smoke 通过的证据。Ruff 通过不等同运行时测试通过。

新增 8 个 unittest 方法覆盖：确定性与外部空白、大小写、多重失败、证据版本缺失、
空答复、不可比较及其附带原因、非法输入类型/空值/重复项/未知字段、非法结果组合、
聚合计数与分母/门槛、结果顺序、空集/全部不可比较、重复 ID 和不可变性。
所有输入均为合成数据，不读取凭据、用户资料或数据库。

### 剩余缺口与验收边界

须修复本地 Python 环境后复跑上述单测、架构检查和 Smoke；现有全套回归尚未验收。
本轮未涉及 Web，因此未运行 Web 构建检查。未接入 API、Worker、版本比较、评测持久化、
租户权限或发布流程，不能宣称完成验收矩阵 C08 或 G2 发布门槛。
精确字符串 oracle 不衡量开放式答案的语义质量；证据真实性、归属和引用支持度
需未来由经过权限校验的调用方验证。未来持久化实体必须添加 workspace_id，
不得直接把当前纯离线值对象作为可访问用户数据的 HTTP 契约。
没有 commit/push、部署、生产数据库操作或外部服务调用。

## 2026-10-10 20:01（UTC+08）独立验收复核

上一节“Python 解释器不存在、单测未执行”是先前轮次的历史结果；本轮在同一 Windows Runner 复查时，`pythoncore-3.14-64/python.exe` 与 `services/api/.venv/Scripts/python.exe` 均存在并输出 `Python 3.14.3`。不推测期间环境为何恢复，也不回写原始历史记录。

- `services/api` 中执行 `.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_evaluation.py -v`：**8/8 PASS**；确定性 Eval 的新契约测试已实际运行。
- 同目录执行 `.venv/Scripts/python.exe -B -m unittest discover -s tests -v`：**116/116 PASS**，25.021s，exit code 0（模拟与离线数据，不代表真实 LLM/PG17 联机验收）。
- `.venv/Scripts/ruff.exe check --no-cache app/domain/evaluation.py tests/test_evaluation.py`：通过；`ruff format --check --no-cache` 对上述两个文件：**2 files already formatted**。
- 全项目 `.venv/Scripts/ruff.exe check --no-cache .`：通过；全项目 `ruff format --check --no-cache .`：**失败（exit 1）**，仅既有未提交修改 `app/llm.py:138` 需一行无语义变化的格式整理，61 个其他文件已格式化。为避免覆盖其他工作会话，本轮未改 `llm.py`。
- `.venv/Scripts/python.exe -B ../../scripts/check_architecture.py`：**Architecture boundaries: PASS**，exit 0。
- 本轮再次发起 Smoke 和部分 Web 检查被执行平台安全检查阻断，**未执行成功**，不使用上一轮记录替代新验收。
- Git 工作区仍有 16 项已修改、14 项未跟踪（包含其他会话工作）；未 reset/stash/clean/commit/push、部署、修改生产数据库、使用收费模型或读取密钥。
- Codex ACP Run `wc_agent_run_iSXkKuv6Xb2dyj-lICJ0j-g3CoXHbvoqAnUbvLTWwjw` 经独立 observe 为 `completed/end_turn`，但所属持久任务 `wc_agent_task_o3a-AnGBXmkd6KNC` 在 reconcile 时出现 `outcome_unknown`。避免在状态未一致前重复派发同一 Eval 任务。此模块仍未接 API、审批、发布/回滚，验收矩阵 C08 未通过。

