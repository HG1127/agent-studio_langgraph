import type { useStudio } from "@/src/features/studio/useStudio";

type Props = { studio: ReturnType<typeof useStudio> };

const outstanding = [
  "正式身份认证与工作空间隔离",
  "MCP / OAuth 连接器",
  "人工审批与独立工具沙盒",
  "生产级 PostgreSQL 与分布式任务调度",
];

export function ToolCenter({ studio }: Props) {
  return (
    <>
      <div className="section-heading title">
        <div>
          <div className="eyebrow">TOOL REGISTRY</div>
          <h1>工具与能力中心</h1>
          <p>所有工具都必须注册，并通过服务端最小权限检查</p>
        </div>
        <span className="pill">仅本地只读工具</span>
      </div>
      <div className="tool-grid">
        {studio.tools.map((tool) => (
          <section className="card" key={tool.id}>
            <span className="template-icon">⌘</span>
            <h3>{tool.id}</h3>
            <p>{tool.description}</p>
            <span className="pill">{tool.risk === "read_only" ? "只读" : tool.risk}</span>
          </section>
        ))}
      </div>
      <section className="card spaced">
        <h2>安全与能力边界</h2>
        <p>
          当前实现工具 allowlist、结构化参数校验与单机 SQLite 检查点；
          正式认证、MCP、沙盒和多实例调度尚未交付。后端拒绝生产模式启动。
        </p>
        <div className="todo-grid">
          {outstanding.map((item) => (
            <div key={item}>
              <b>待开发</b>
              <span>{item}</span>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
