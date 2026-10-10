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

export function StudioShell({ studio, auth, children }: Props) {
  const currentPage = navigation.find((item) => item.id === studio.page);
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
          <small>本地开发样板 · v0.4</small>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <span>
            Workspace <b>/</b> {currentPage?.label}
          </span>
          <div>
            <span className="pill">开发预览</span>
            <div className="user-control">
              <span title={auth.profile?.user.email}>{auth.profile?.user.email}</span>
              <button type="button" onClick={() => void auth.logout()} disabled={auth.working}>
                退出登录
              </button>
            </div>
          </div>
        </header>
        <div className="container">
          <div className="notice">
            <strong>本地验证版</strong> · 已接入 LangGraph 单机检查点和不可变 Agent 版本，
            当前实现本地登录及工作空间级对象权限校验，仍未通过生产级身份安全审计，且缺少
            PostgreSQL、独立沙盒和可靠调度。请勿公开部署或导入敏感资料。
          </div>
          <div className="runtime-strip">
            <span className={`signal ${studio.health?.worker_online ? "good" : "bad"}`} />
            {studio.health?.worker_online ? "LangGraph Worker 在线" : "LangGraph Worker 未运行"}
            <span>· SQLite 本地检查点（非生产）</span>
            {!studio.health?.worker_online && <small>在后端目录运行 python -m app.worker</small>}
          </div>
          {studio.feedback && (
            <div role="status" className={`feedback ${studio.feedback.error ? "error" : "ok"}`}>
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
