"use client";

import { AgentBuilder } from "@/src/features/agents/AgentBuilder";
import { AgentLibrary } from "@/src/features/agents/AgentLibrary";
import { RunMonitor } from "@/src/features/runs/RunMonitor";
import { StudioShell } from "@/src/features/studio/StudioShell";
import { useStudio } from "@/src/features/studio/useStudio";
import { ToolCenter } from "@/src/features/tools/ToolCenter";

/** Thin composition root: business state lives in useStudio, rendering in features. */
export function StudioApp() {
  const studio = useStudio();

  return (
    <StudioShell studio={studio}>
      {studio.page === "create" && <AgentBuilder studio={studio} />}
      {studio.page === "agents" && <AgentLibrary studio={studio} />}
      {studio.page === "runs" && <RunMonitor studio={studio} />}
      {studio.page === "tools" && <ToolCenter studio={studio} />}
    </StudioShell>
  );
}
