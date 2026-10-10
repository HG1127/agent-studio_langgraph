import { runStatusLabels } from "@/src/features/studio/catalog";
import type { useStudio } from "@/src/features/studio/useStudio";
import type { RunAction } from "@/src/types/studio";

type Props = { studio: ReturnType<typeof useStudio> };

function TaskAction({
  action,
  label,
  working,
  onClick,
}: {
  action: RunAction;
  label: string;
  working: boolean;
  onClick: (action: RunAction) => Promise<void>;
}) {
  return (
    <button
      type="button"
      className={`btn secondary ${action === "cancel" ? "danger" : ""}`}
      disabled={working}
      onClick={() => void onClick(action)}
    >
      {label}
    </button>
  );
}

export function RunMonitor({ studio }: Props) {
  const selected = studio.selectedRun;
  return (
    <>
      <div className="section-heading title">
        <div>
          <div className="eyebrow">RUN MONITOR</div>
          <h1>运行中心</h1>
          <p>任务状态、真实调用、LangGraph 检查点与错误记录</p>
        </div>
        <button type="button" className="btn secondary" onClick={() => void studio.refresh()}>
          刷新 ↻
        </button>
      </div>
      <div className="run-grid">
        <section className="card">
          <h3>最近的任务</h3>
          {!studio.runs.length && <div className="empty">暂无任务</div>}
          {studio.runs.map((run) => (
            <button
              type="button"
              key={run.id}
              className={`run-item ${studio.selectedRunId === run.id ? "selected" : ""}`}
              onClick={() => studio.selectRun(run.id)}
            >
              <strong>{run.input}</strong>
              <small>
                <span className={`status ${run.status.toLowerCase()}`}>
                  {runStatusLabels[run.status]}
                </span>{" "}
                {new Date(run.created_at).toLocaleString("zh-CN")}
              </small>
            </button>
          ))}
        </section>
        <section className="card">
          {!selected ? (
            <div className="empty">选择左侧任务查看详情</div>
          ) : (
            <>
              <div className="section-heading">
                <h3>任务详情</h3>
                <span className={`status ${selected.status.toLowerCase()}`}>
                  {runStatusLabels[selected.status]}
                </span>
              </div>
              <p className="task-quote">{selected.input}</p>
              {selected.agent_version_id && (
                <small className="hint">已绑定不可变 Agent 版本：{selected.agent_version_id}</small>
              )}
              <div className="run-controls">
                {(selected.status === "QUEUED" || selected.status === "RUNNING") && (
                  <TaskAction
                    action="pause"
                    label="Ⅱ 暂停"
                    working={studio.working || !studio.canEdit}
                    onClick={studio.controlRun}
                  />
                )}
                {selected.status === "PAUSED" && (
                  <TaskAction
                    action="resume"
                    label="▷ 恢复执行"
                    working={studio.working || !studio.canEdit}
                    onClick={studio.controlRun}
                  />
                )}
                {selected.status === "FAILED" && (
                  <TaskAction
                    action="retry"
                    label="↻ 从检查点重试"
                    working={studio.working || !studio.canEdit}
                    onClick={studio.controlRun}
                  />
                )}
                {["QUEUED", "RUNNING", "PAUSED", "PAUSE_REQUESTED"].includes(selected.status) && (
                  <TaskAction
                    action="cancel"
                    label="× 取消"
                    working={studio.working || !studio.canEdit}
                    onClick={studio.controlRun}
                  />
                )}
              </div>
              {selected.error && <p className="error-box">{selected.error}</p>}
              {selected.output && (
                <div className="result">
                  <h3>模型最终回答</h3>
                  <p>{selected.output}</p>
                </div>
              )}
              {studio.checkpoint?.available && (
                <div className="checkpoint-summary">
                  <strong>LangGraph 检查点已保存</strong>
                  <span>
                    模型轮次：{studio.checkpoint.model_rounds} · 工具调用：
                    {studio.checkpoint.tool_calls}
                  </span>
                  <small>
                    下一步：
                    {studio.checkpoint.next_nodes.length
                      ? studio.checkpoint.next_nodes.join("、")
                      : "当前检查点没有待执行节点"}
                  </small>
                </div>
              )}
              <h3>执行时间线 · {studio.events.length} 条事件</h3>
              <div className="timeline">
                {studio.events.map((event) => (
                  <div className="event" key={event.seq}>
                    <b>{event.type}</b>
                    <time>{new Date(event.created_at).toLocaleTimeString("zh-CN")}</time>
                    <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>
      </div>
    </>
  );
}
