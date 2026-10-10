## 本次变更（What & Why）

简述所属业务模块、要解决的问题及不用其他实现方式的原因。

## 架构边界（Boundaries）

- [ ] 领域层未引入数据库、Web 路由或 LangGraph
- [ ] API 只做校验、返回和调用用例，不包含 SQL
- [ ] Agent Runtime 保持真实授权与明确停止条件
- [ ] 前端网络请求在 `src/lib/api.ts` 或可测试的应用层
- [ ] 不为单个功能增加不必要的微服务或框架

## 数据与兼容性（Migration）

- [ ] 数据库迁移可重复执行；有旧数据回填与失败回滚验证
- [ ] AgentSpec/旧 Run 不会因新版修改被覆盖
- [ ] 对外 API 变更有合同与前端兼容测试

## 安全（Security）

- [ ] 不包含 API 密钥、个人敏感信息或演示成功伪造
- [ ] 新工具有服务端授权检查与输入 Schema
- [ ] 写操作需要独立审批与幂等措施（当前开发版禁止）

## 验证证据（Tests）

- [ ] Python Ruff lint / format
- [ ] 架构依赖检查
- [ ] 后端单测与 API/Worker Smoke
- [ ] 前端 ESLint / Prettier / TypeScript / production build
- [ ] 人工/浏览器 E2E（如未执行需说明原因）

## 部署与限制（Rollout）

明确本轮是否可以部署；生产身份认证/隔离尚未验收前不得开放公网。记录遗留事项与回滚方式。
