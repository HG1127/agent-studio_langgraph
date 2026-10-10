import type { useStudio } from "@/src/features/studio/useStudio";

type Props = { studio: ReturnType<typeof useStudio> };

export function AgentLibrary({ studio }: Props) {
  const agent = studio.selectedAgent;
  return (
    <>
      <div className="section-heading title">
        <div>
          <div className="eyebrow">YOUR ASSISTANTS</div>
          <h1>我的 Agents</h1>
          <p>管理配置版本，为每次执行保留可追溯的版本快照</p>
        </div>
        <button type="button" className="btn primary" onClick={() => studio.navigate("create")}>
          + 新建 Agent
        </button>
      </div>

      {!studio.agents.length && (
        <div className="empty">还没有 Agent。进入创建中心保存一份配置。</div>
      )}
      <div className="agent-grid">
        {studio.agents.map((item) => (
          <button
            type="button"
            key={item.id}
            className={`agent ${studio.selectedAgentId === item.id ? "selected" : ""}`}
            onClick={() => studio.selectAgent(item.id)}
          >
            <span className="template-icon">✦</span>
            <strong>{item.spec.name}</strong>
            <small>{item.spec.goal.task}</small>
            <b>
              v{item.current_version} · {item.spec.tools.enabled.length} 个本地工具
            </b>
          </button>
        ))}
      </div>

      {agent && (
        <>
          <section className="card spaced">
            <div className="section-heading">
              <h2>{agent.spec.name} · 执行工作台</h2>
              <span className="pill">当前 v{agent.current_version}</span>
            </div>
            <p>{agent.spec.goal.task}</p>
            <label className="label" htmlFor="run-input">
              本次任务
            </label>
            <textarea
              id="run-input"
              className="task-input"
              placeholder="例如：请调用工具计算 120 + 350。"
              value={studio.task}
              onChange={(event) => studio.setTask(event.target.value)}
            />
            <div className="actions">
              <button
                type="button"
                className="btn primary"
                disabled={studio.working}
                onClick={() => void studio.startRun()}
              >
                ▷ 开始真实执行
              </button>
            </div>
            <small className="hint">
              每个任务绑定启动时的 Agent 版本。当前仅支持 UTC 时间与数字加法只读工具；
              未连接模型时后台会记录明确的失败状态。
            </small>
          </section>

          <section className="card spaced">
            <div className="section-heading">
              <div>
                <div className="eyebrow">IMMUTABLE VERSIONS</div>
                <h2>版本历史与安全升级</h2>
              </div>
              <span className="pill">当前版本 v{agent.current_version}</span>
            </div>
            <p>
              修改目标后系统将创建一个新版本，而不是覆盖原来的 AgentSpec。
              已启动的历史任务仍然使用当时的完整配置快照。
            </p>
            <label className="label" htmlFor="next-version-goal">
              新版本的目标（保留其他 11 项配置）
            </label>
            <textarea
              id="next-version-goal"
              className="task-input"
              value={studio.versionGoal}
              onChange={(event) => studio.setVersionGoal(event.target.value)}
            />
            <div className="actions">
              <button
                type="button"
                className="btn primary"
                disabled={studio.working || studio.versionGoal.trim().length < 5}
                onClick={() => void studio.createVersion()}
              >
                保存为 v{agent.current_version + 1}
              </button>
            </div>
            <div className="version-list">
              {studio.versions.map((version) => (
                <div key={version.id} className="version-item">
                  <span className="pill">v{version.version}</span>
                  <div>
                    <strong>{version.spec.goal.task}</strong>
                    <small>{new Date(version.created_at).toLocaleString("zh-CN")}</small>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </>
  );
}
