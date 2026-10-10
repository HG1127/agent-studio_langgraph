import { capabilities, templates } from "@/src/features/studio/catalog";
import type { useStudio } from "@/src/features/studio/useStudio";

type Props = { studio: ReturnType<typeof useStudio> };

export function AgentBuilder({ studio }: Props) {
  return (
    <>
      <section className="hero">
        <div className="eyebrow">AI-NATIVE AGENT BUILDER</div>
        <h1>
          一句话，创建你的 <em>AI 助手</em>
        </h1>
        <p>
          不用理解复杂 Prompt 或流程节点。描述想完成的工作，系统生成结构化配置，
          再通过真实任务验证。
        </p>
        <div className="composer">
          <textarea
            aria-label="描述 Agent 目标"
            placeholder="例如：建立一个全球人物调研助手，梳理背景、商业关系和可核验来源……"
            value={studio.description}
            onChange={(event) => studio.setDescription(event.target.value)}
          />
          <div className="composer-actions">
            <span>目标 · 工具 · 记忆 · 执行规则</span>
            <div>
              <button
                className="btn secondary"
                type="button"
                disabled={studio.working}
                onClick={() => void studio.prepareDraft(false)}
              >
                手动配置草稿
              </button>
              <button
                className="btn primary"
                type="button"
                disabled={studio.working}
                onClick={() => void studio.prepareDraft(true)}
              >
                {studio.working ? "处理中…" : "✦ AI 生成草案"}
              </button>
            </div>
          </div>
        </div>
        {!studio.health?.model_key_configured && (
          <small className="hint">
            尚未连接模型。AI 创建和真实执行需要服务端 MODEL_API_KEY；
            手动草稿仅验证配置保存，不会伪造 AI 结果。
          </small>
        )}
      </section>

      {studio.draft && (
        <section className="card preview">
          <div className="section-heading">
            <h2>Agent 配置草稿</h2>
            <span className="pill">AgentSpec {studio.draft.schema_version}</span>
          </div>
          <div className="spec-lines">
            <p>
              <label>名称</label>
              <strong>{studio.draft.name}</strong>
            </p>
            <p>
              <label>任务目标</label>
              <strong>{studio.draft.goal.task}</strong>
            </p>
            <p>
              <label>允许的工具</label>
              <strong>{studio.draft.tools.enabled.join("、") || "无"}</strong>
            </p>
          </div>
          <div className="actions">
            <button type="button" className="btn secondary" onClick={() => studio.setDraft(null)}>
              取消
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={studio.working}
              onClick={() => void studio.saveDraft()}
            >
              保存为 Agent v1
            </button>
          </div>
        </section>
      )}

      <div className="section-heading">
        <div>
          <div className="eyebrow">GET STARTED</div>
          <h2>从具体工作场景开始</h2>
        </div>
        <small>模板只填写需求，不代表工具已授权</small>
      </div>
      <div className="templates">
        {templates.map((template) => (
          <button
            type="button"
            className="template"
            key={template.name}
            onClick={() => {
              studio.setDescription(template.prompt);
              studio.setDraft(null);
            }}
          >
            <span className="template-icon">{template.icon}</span>
            <strong>{template.name}</strong>
            <small>{template.detail}</small>
            <b>使用该需求 →</b>
          </button>
        ))}
      </div>

      <div className="section-heading spaced">
        <div>
          <div className="eyebrow">AGENT RUNTIME</div>
          <h2>Agent 的 12 项核心要素</h2>
        </div>
        <small>展示的是本地样板阶段，不代表生产级验收</small>
      </div>
      <div className="capabilities">
        {capabilities.map(([name, detail, phase], index) => (
          <div className="capability" key={name}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <div>
              <strong>{name}</strong>
              <small>{detail}</small>
            </div>
            <i>{phase}</i>
          </div>
        ))}
      </div>
    </>
  );
}
