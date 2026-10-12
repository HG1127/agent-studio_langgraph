import type { ReactNode } from "react";

import { navigation } from "@/src/features/studio/catalog";
import type { useStudio } from "@/src/features/studio/useStudio";
import type { useSession } from "@/src/features/auth/useSession";
import { WorkspacePanel } from "@/src/features/auth/WorkspacePanel";

type StudioController = ReturnType<typeof useStudio>;

interface Props {
  studio: StudioController;
  auth: ReturnType<typeof useSession>;
  children: ReactNode;
}

const WORKER_COMMAND = "cd services/api; .\\.venv\\Scripts\\python.exe -m app.worker";

export function StudioShell({ studio, auth, children }: Props) {
  const currentPage = navigation.find((item) => item.id === studio.page);
  const workerOnline = studio.health?.worker_online === true;
  const healthChecked = studio.health !== null;
  const database =
    studio.health?.database_backend === "sqlite_development"
      ? "SQLite（本地开发）"
      : studio.health?.database_backend || "等待数据库状态";

  async function copyWorkerCommand() {
    try {
      await navigator.clipboard.writeText(WORKER_COMMAND);
      studio.setFeedback({
        error: false,
        text: "Worker 启动命令已复制。请在项目根目录打开另一个 PowerShell 窗口执行。",
      });
    } catch {
      studio.setFeedback({
        error: true,
        text: "浏览器无法使用剪贴板，请选中并手动复制下方启动命令。",
      });
    }
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-icon">✦</span>
          <div>
            <strong>Agent Studio</strong>
            <small>Build · Run · Improve</small>
          </div>
        </div>
        <div className="nav-head">WORKSPACE</div>
        <nav aria-label="工作台菜单">
          {navigation.map((item) => (
            <button
              type="button"
              key={item.id}
              className={`nav ${studio.page === item.id ? "active" : ""}`}
              onClick={() => studio.navigate(item.id)}
            >
              <span>{item.glyph}</span>
              {item.label}
            </button>
          ))}
        </nav>
        <WorkspacePanel auth={auth} />
        <div className="side-bottom">
          <span className={`signal ${studio.connected ? "good" : "bad"}`} />
          {studio.connected ? "API 已连接" : "API 未连接"}
          <small>本地开发版 · PostgreSQL 迁移待验证</small>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <span>
            Workspace <b>/</b> {currentPage?.label}
          </span>
          <div>
            <span className="pill">本地开发</span>
            <div className="user-control">
              <span title={auth.profile?.user.email}>{auth.profile?.user.email}</span>
              <button type="button" onClick={() => void auth.logout()} disabled={auth.working}>
                退出登录
              </button>
            </div>
          </div>
        </header>
        <div className="container">
          <section className="notice development-status" aria-label="开发版提示">
            <div className="development-head">
              <strong>开发环境</strong>
              <span>目前可验证 Agent 创建、不可变版本及本地 LangGraph 执行</span>
            </div>
            <details className="development-more">
              <summary>查看数据隔离、权限和上线限制</summary>
              <p>
                当前采用本地账户与工作空间级权限校验，尚未通过生产级身份安全审计。 PostgreSQL 17 +
                pgvector 模型及 RLS 方案已准备，但尚未完成数据库迁移和 FastAPI / Worker
                切换。正式沙盒、生产级调度及安全验收也尚未完成。 不要公开部署或导入敏感资料。
              </p>
            </details>
          </section>

          <section className="runtime-strip" aria-label="系统运行状态">
            <div className="runtime-summary">
              <span className={`signal ${workerOnline ? "good" : "bad"}`} />
              <strong>
                {!healthChecked
                  ? "正在检查 Worker"
                  : workerOnline
                    ? "LangGraph Worker 已运行"
                    : "LangGraph Worker 未运行"}
              </strong>
              <span className="runtime-db">当前数据库：{database}</span>
              <span className="runtime-pg">PostgreSQL：准备中，尚未接管</span>
            </div>
            {healthChecked && !workerOnline && (
              <div className="runtime-guide">
                <span>运行任务前，需要在项目根目录打开另一个 PowerShell 窗口：</span>
                <code>{WORKER_COMMAND}</code>
                <button
                  type="button"
                  className="runtime-copy"
                  onClick={() => void copyWorkerCommand()}
                >
                  复制命令
                </button>
              </div>
            )}
          </section>

          {studio.feedback && (
            <div
              role={studio.feedback.error ? "alert" : "status"}
              className={`feedback ${studio.feedback.error ? "error" : "ok"}`}
            >
              <span>{studio.feedback.text}</span>
              <button type="button" aria-label="关闭提示" onClick={() => studio.setFeedback(null)}>
                ×
              </button>
            </div>
          )}
          {children}
        </div>
      </main>
    </div>
  );
}
