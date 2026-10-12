# S2 评测套件完整性门槛

新增离线领域模块 `evaluation_suite_integrity.py`。`EvalSuiteManifest` 定义不可变、非空且 case_id 唯一的评测套件；SHA-256 指纹覆盖 suite_id、版本、答案和证据要求，忽略用例与证据排列顺序。`assess_versioned_eval_gate` 要求基线、候选套件指纹一致，结果 case_id 与套件完整对应，再运行原有回归门槛。

新模块仅输出只读判定报告，不执行真实模型调用、发布或回滚，也不构成权限证明。后续受信发布服务仍须自行从可信评测执行器取得结果，验证租户身份、Agent 版本、审批及审计，再依 C08 进行发布/回滚验证。旧 Eval outcome_unknown 任务仍未重派，原任务文件未改动。

新增 `test_evaluation_suite_integrity.py` 含 11 项离线契约测试。测试和全项目质量检查的实际结果记录在 `docs/10_IMPLEMENTATION_CHANGELOG.md`；请勿将合成测试等同于真实 LLM 或 G2 验收。
