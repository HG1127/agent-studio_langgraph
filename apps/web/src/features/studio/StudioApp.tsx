"use client";

import { AuthScreen } from "@/src/features/auth/AuthScreen";
import { useSession } from "@/src/features/auth/useSession";
import { AgentBuilder } from "@/src/features/agents/AgentBuilder";
import { AgentLibrary } from "@/src/features/agents/AgentLibrary";
import { RunMonitor } from "@/src/features/runs/RunMonitor";
import { StudioShell } from "@/src/features/studio/StudioShell";
import { useStudio } from "@/src/features/studio/useStudio";
import { ToolCenter } from "@/src/features/tools/ToolCenter";

type Authentication = ReturnType<typeof useSession>;

function AuthenticatedStudio({ auth, workspaceId }: { auth: Authentication; workspaceId: string }) {
  const studio = useStudio(workspaceId, auth.currentWorkspace?.role !== "viewer");
  return (
    <StudioShell studio={studio} auth={auth}>
      {studio.page === "create" && <AgentBuilder studio={studio} />}
      {studio.page === "agents" && <AgentLibrary studio={studio} />}
      {studio.page === "runs" && <RunMonitor studio={studio} />}
      {studio.page === "tools" && <ToolCenter studio={studio} />}
    </StudioShell>
  );
}

/** Authentication is a hard gate, not a CSS overlay around an unscoped workbench. */
export function StudioApp() {
  const auth = useSession();
  if (!auth.ready) {
    return (
      <main className="auth-page">
        <p>正在检查本地登录状态…</p>
      </main>
    );
  }
  if (!auth.profile || !auth.workspaceId) return <AuthScreen auth={auth} />;
  return <AuthenticatedStudio key={auth.workspaceId} auth={auth} workspaceId={auth.workspaceId} />;
}
