# Agent Studio｜模型连接管理 V0.7

## 用途与入口

工作台左侧「模型连接 / Model Hub」用于管理**工作空间级模型供应商**。支持预设供应商、基础 URL、访问密钥、可用模型发现、真实推理验证及三次采样测速。必须使用自行授权的模型 API Key；不能替代供应商注册、开通额度与授权管理。

供应商目录（22 个入口）包括：OpenAI、Anthropic Claude、Google Gemini、Azure OpenAI、DeepSeek、阿里百炼中国站/国际站、Moonshot/Kimi、智谱 GLM、硅基流动、火山方舟、xAI、OpenRouter、Groq、Together、Mistral、Cohere、Fireworks、Perplexity、Ollama、LM Studio 和自定义 API。

使用顺序：进入模型连接 → 选择供应商（或自定义）→ 提供 Base URL / API Key → 保存 → 获取模型列表（若支持）→ 选择并保存模型 ID → 测试连接 → 测试速率 → 设置默认模型 → AI 创建与 Agent 执行。

## 技术架构

- 供应商目录和校验：services/api/app/domain/model_connections.py
- 连接存储：services/api/app/infrastructure/sqlite/model_connection_repository.py
- 主密钥加密：services/api/app/infrastructure/credentials.py
- OpenAI-Compatible + Anthropic/Gemini 原生协议：services/api/app/infrastructure/model_transport.py
- 业务用例：services/api/app/application/model_connection_service.py
- 接口路由：services/api/app/api/routes/model_connections.py
- 前端页面：apps/web/src/features/models/ModelCenter.tsx
- SQLite 迁移：services/api/app/infrastructure/sqlite/migrations.py
- 测试：services/api/tests/test_model_connections.py

SQLite 将访问密钥的密文保存于工作空间连接表。默认在数据库旁的 .local/model_master.key 存放 Fernet 加密主密钥，或显式指定环境变量 STUDIO_MODEL_ENCRYPTION_KEY；需在备份中同时妥善保存主密钥。密钥绝不通过连接列表 API 回传浏览器；前端仅得到 has_api_key 状态位。生产环境必须替换为 KMS 或其他受管 Secret Store。

当前接口：GET /v1/models/providers（登录即可）；GET /v1/models/connections（空间成员）；POST /v1/models/connections（owner/admin）；PATCH /v1/models/connections/{id}（owner/admin）；DELETE /v1/models/connections/{id}（owner/admin）；POST /v1/models/connections/{id}/discover（owner/admin）；POST /v1/models/connections/{id}/test（owner/admin）。

除供应商目录外，各接口必须携带有效的工作空间 X-Workspace-ID，与登录 cookie 的实时成员权限匹配。一个工作空间只能有一个默认连接。Member/viewer 可以查看无密钥的连接概况；只有 owner/admin 可以写入、探测模型、测试连接。

## 发现与测速语义

OpenAI 兼容协议通常使用 /models；Anthropic 使用 /models，Google Gemini 使用 /models。Azure OpenAI v1 使用用户资源专属的 Deployment Name，**不保证通用列表可用**，此版本由用户手动填写部署 ID。其他 OpenAI 兼容服务不保证支持 /models；可在界面手动输入模型 ID。

模型列表来自服务商实际接口，**列出不代表有权限调用**。测试连接会向保存模型发送真实推理请求，供应商可能按 Token 计费。

- 平均耗时 latency_ms：完整端到端响应延迟；三次样本支持最小和最大耗时。
- effective_tokens_per_second：提供商返回的完成 Token 数除以完整请求总耗时，不是纯解码 TPS。
- 缺失供应商 usage 时，不根据生成文本字数伪造 Token 数据；展示速率为空。
- 目前不支持 TTFT、流式解码吞吐、并发 QPS、配额查询或成本估算。
- 对 401/403、404、429、超时/失败提供安全错误信息，不回显原始上游错误体。
- 测试结果只对实际保存的模型 ID 有效，模型或连接更新后需重新测试。
- 当前连接测试仅验证普通文本对话；模型能否完成 Function/Tool Calling 与 JSON 模式需要额外的能力验证。

## 与 Agent 的集成

AI 草稿创建优先使用当前工作空间默认连接。Run 创建时保存 model_connection_id、model_name，后台 Worker 优先使用当时绑定的模型 ID，避免队列期间更换默认模型后漂移；**此阶段只冻结连接 ID 与模型名称，不冻结 URL/密钥版本**。过去仅依赖 MODEL_API_KEY、MODEL_BASE_URL、MODEL_NAME 的历史任务保留旧逻辑，不能自动归属其他工作空间的新连接。

## 安全和生产限制

该功能仅供本地开发：公网需 HTTPS，拒绝 URL 携带用户名密码/查询字符串、拒绝私网和保留地址、阻止 HTTP 跳转，限制响应字节数与请求时长；本地 HTTP 仅允许指定的非特权回环端口（1024–65535）。DNS 检查还不是连接期 IP 锁定，不能当作生产级 SSRF 防护。正式上线前还需：独立 egress 代理、KMS、细分凭据权限、操作审计、配额限速、生产 RLS、组织级安全认证以及真实供应商联机验收。

专项用例覆盖：密钥加密与隐藏、跨工作空间隔离、owner/admin 权限、默认模型切换、Run 模型绑定、协议转换、列表发现、三次样本速率计算、服务商 429 错误和 URL 安全。所有供应商协议相关单测均采用受控 Mock；这些测试不是供应商的真实联机测试。
