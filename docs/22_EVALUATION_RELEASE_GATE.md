# Agent Studio S2 — 离线 Eval 版本回归门槛

## 范围

纯领域规则：以 `EvalSummary` 比较基线版本与候选版本的同一固定用例集。
`assess_eval_regression(baseline, candidate)` 返回只读 `EvalGateReport`。
本模块**不会发布或回滚**，不访问数据库、网络、真实模型、密钥或任何用户数据。
该报告仅是上层可信发布服务重新评测后的证据，绝不能当作客户端授予的发布权限。

## Fail-closed 规则

候选版本只有在基线和候选均非空、用例 ID 集合完全相等、双方均没有
`incomparable`、候选全部 `passed` 的条件下才返回 `eligible=True`。
按 `case_id` 比较，不以序号匹配；输入顺序不影响结果。基线已失败的用例变成
候选通过属于修复，但基线通过变成失败或不可比较属于回归。
任何无 oracle、缺证据、测试集合漂移、空评测集均不会被自动当作通过。

`blockers` 使用固定顺序，分别为 `empty_suite`、`suite_mismatch`、
`baseline_incomparable`、`candidate_incomparable`、
`candidate_failed`、`case_regression`；同时提供已失败、不可比较、
回归的候选 case_id（字典序稳定），便于前端展示和审计。

## 工程限制和下一步

新增领域规则和合成契约测试，不变更既有 EvalCase/EvalResult/EvalSummary，
不碰状态为 outcome_unknown 的原 Eval 编码任务范围。
尚未与 S2 C08 的真实版本存储、租户权限、发布审批、部署及回滚接口接线，
没有真实 LLM 结果，不构成 G2 或 C08 完成验收。

测试可在 `services/api` 运行：
`./.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_evaluation_gate.py -v`。
全量单测、Ruff、架构检查和模拟 Smoke 需以实际退出码另行记录。

## 2026-10-10 本轮实际执行及验收

- 只创建三个互不冲突的文件：领域门槛 `evaluation_gate.py`、11 项合成契约测试 `test_evaluation_gate.py` 和本说明；旧的 Eval 领域文件/测试/原说明、全部其他未提交修改均未覆盖。
- 新增 11 项合同测试：**11/11 通过**。原有 Eval 测试 **8/8 通过**；新增后后端 `unittest discover -s tests -v` **127/127 通过**（22.997 秒，退出码 0）。
- LangGraph/Worker 跨进程协议 Smoke：**通过**，但明确使用本地 HTTP 模拟模型；不属于真实 LLM 联机测试或生产验收。
- Web `npm run lint`、`npm run typecheck`、`npm run build`：**均通过**（本轮未修改 Web 代码）。
- 本轮新增前全项目 Ruff format --check 报 62 个文件通过；新增后对新文件的 Ruff check/format --check 和新的架构检查遇执行平台安全检查阻断，**没有通过证据**；对两处长行的手动修正操作同样被阻断。必须在下一轮恢复这些静态质量门禁后才可宣称完成本小模块的全部验收。
- Git 仍存在其他会话未提交修改；三份新增文件未跟踪、未暂存或提交。旧持久 Eval 任务状态仍为 outcome_unknown，已核对没有权威 terminal 结果，因此没有重新派发它。新功能未接版本发布审批、租户隔离或回滚。
- 未调用收费模型、未进行外部写入、生产数据库操作、部署、git commit/push 或凭据读取。
