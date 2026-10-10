"use client";

import { useCallback, useEffect, useState } from "react";

import { request, scopedRequest, studioApi } from "@/src/lib/api";
import type {
  Agent,
  AgentSpec,
  AgentVersion,
  Checkpoint,
  Feedback,
  Health,
  Run,
  RunAction,
  RunEvent,
  StudioPage,
  StudioTool,
} from "@/src/types/studio";
import { manualSpec } from "@/src/features/agents/manualSpec";

const friendlyError = (error: unknown) =>
  error instanceof Error ? error.message : "操作失败，请检查后重试";

/** All stateful network actions are centralized; view components have no fetch calls. */
export function useStudio(workspaceId: string, canEdit: boolean) {
  const [page, setPage] = useState<StudioPage>("create");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [versions, setVersions] = useState<AgentVersion[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [tools, setTools] = useState<StudioTool[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [connected, setConnected] = useState(false);
  const [description, setDescription] = useState("");
  const [draft, setDraft] = useState<AgentSpec | null>(null);
  const [working, setWorking] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [task, setTask] = useState("");
  const [versionGoal, setVersionGoal] = useState("");
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [checkpoint, setCheckpoint] = useState<Checkpoint | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const selectedAgent = agents.find((agent) => agent.id === selectedAgentId);
  const selectedRun = runs.find((run) => run.id === selectedRunId);

  const refresh = useCallback(async () => {
    try {
      const [newHealth, newAgents, newRuns, newTools] = await Promise.all([
        request<Health>("/v1/health"),
        scopedRequest<Agent[]>("/v1/agents", workspaceId),
        scopedRequest<Run[]>("/v1/runs", workspaceId),
        request<StudioTool[]>("/v1/tools"),
      ]);
      setHealth(newHealth);
      setAgents(newAgents);
      setRuns(newRuns);
      setTools(newTools);
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }, [workspaceId]);

  const refreshRun = useCallback(
    async (id: string) => {
      try {
        const [run, stream, saved] = await Promise.all([
          scopedRequest<Run>(`/v1/runs/${id}`, workspaceId),
          scopedRequest<RunEvent[]>(`/v1/runs/${id}/events`, workspaceId),
          scopedRequest<Checkpoint>(`/v1/runs/${id}/checkpoint`, workspaceId),
        ]);
        setRuns((previous) => [run, ...previous.filter((item) => item.id !== run.id)]);
        setEvents(stream);
        setCheckpoint(saved);
      } catch {
        setConnected(false);
      }
    },
    [workspaceId],
  );

  const loadVersions = useCallback(
    async (agentId: string) => {
      try {
        const result = await studioApi.listVersions(agentId, workspaceId);
        setVersions(result);
      } catch (error) {
        setFeedback({ error: true, text: friendlyError(error) });
      }
    },
    [workspaceId],
  );

  useEffect(() => {
    // Network synchronization runs after React finishes the effect setup.
    const initial = window.setTimeout(() => void refresh(), 0);
    const interval = window.setInterval(() => void refresh(), 7000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(interval);
    };
  }, [refresh]);

  useEffect(() => {
    if (!selectedRunId) return;
    const initial = window.setTimeout(() => void refreshRun(selectedRunId), 0);
    const interval = window.setInterval(() => void refreshRun(selectedRunId), 2400);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(interval);
    };
  }, [selectedRunId, refreshRun]);

  function navigate(next: StudioPage) {
    setPage(next);
    setFeedback(null);
  }

  function selectAgent(id: string) {
    setSelectedAgentId(id);
    const agent = agents.find((item) => item.id === id);
    setVersionGoal(agent?.spec.goal.task ?? "");
    setVersions([]);
    void loadVersions(id);
  }

  function selectRun(id: string) {
    setSelectedRunId(id);
    setEvents([]);
    setCheckpoint(null);
  }

  async function prepareDraft(withAI: boolean) {
    if (description.trim().length < 5) {
      setFeedback({ error: true, text: "请至少用 5 个字描述 Agent 的目标。" });
      return;
    }
    setWorking(true);
    setFeedback(null);
    try {
      const result = withAI
        ? (await studioApi.createDraft(description, workspaceId)).spec
        : manualSpec(description.trim());
      setDraft(result);
      setFeedback({
        error: false,
        text: withAI ? "AI 已生成结构化草稿，请审查后保存。" : "手动创建配置草稿，未调用 AI。",
      });
    } catch (error) {
      setFeedback({ error: true, text: friendlyError(error) });
    } finally {
      setWorking(false);
    }
  }

  async function saveDraft() {
    if (!draft) return;
    setWorking(true);
    setFeedback(null);
    try {
      const created = await studioApi.createAgent(draft, workspaceId);
      await refresh();
      setSelectedAgentId(created.id);
      setVersionGoal(created.spec.goal.task);
      setVersions([]);
      await loadVersions(created.id);
      setDraft(null);
      setPage("agents");
      setFeedback({
        error: false,
        text: "Agent 配置已保存为 v1。当前仅支持已注册的只读工具。",
      });
    } catch (error) {
      setFeedback({ error: true, text: friendlyError(error) });
    } finally {
      setWorking(false);
    }
  }

  async function createVersion() {
    if (!selectedAgent || versionGoal.trim().length < 5) {
      setFeedback({ error: true, text: "请选择 Agent 并填写不少于 5 字的新目标。" });
      return;
    }
    setWorking(true);
    setFeedback(null);
    try {
      const updatedSpec: AgentSpec = {
        ...selectedAgent.spec,
        goal: { ...selectedAgent.spec.goal, task: versionGoal.trim() },
      };
      const saved = await studioApi.createVersion(
        selectedAgent.id,
        updatedSpec,
        selectedAgent.current_version,
        workspaceId,
      );
      await refresh();
      await loadVersions(selectedAgent.id);
      setFeedback({
        error: false,
        text: `已保存 v${saved.version}。旧版本仍可查阅，历史任务不受影响。`,
      });
    } catch (error) {
      setFeedback({ error: true, text: friendlyError(error) });
      // Refresh to make version conflicts visible instead of silently overriding.
      await refresh();
    } finally {
      setWorking(false);
    }
  }

  async function startRun() {
    if (!selectedAgentId || !task.trim()) {
      setFeedback({ error: true, text: "请选择 Agent 并填写任务内容。" });
      return;
    }
    setWorking(true);
    setFeedback(null);
    try {
      const created = await studioApi.createRun(selectedAgentId, task.trim(), workspaceId);
      setSelectedRunId(created.id);
      setEvents([]);
      setCheckpoint(null);
      setPage("runs");
      await refresh();
      setFeedback({
        error: false,
        text: health?.worker_online
          ? "任务已加入执行队列，请查看运行轨迹。"
          : "任务已入队，但 Worker 未启动；请按 README 启动后台进程。",
      });
    } catch (error) {
      setFeedback({ error: true, text: friendlyError(error) });
    } finally {
      setWorking(false);
    }
  }

  async function controlRun(action: RunAction) {
    if (!selectedRunId) return;
    setWorking(true);
    setFeedback(null);
    try {
      const updated = await scopedRequest<Run>(`/v1/runs/${selectedRunId}/${action}`, workspaceId, {
        method: "POST",
      });
      setRuns((previous) => [updated, ...previous.filter((run) => run.id !== updated.id)]);
      await refreshRun(selectedRunId);
      const messages: Record<RunAction, string> = {
        pause: "暂停请求已提交，将在安全步骤边界生效。",
        resume: "任务已恢复排队。",
        cancel: "取消请求已提交，将在安全步骤边界生效。",
        retry: "已重新排队，将从最近的检查点尝试继续。",
      };
      setFeedback({ error: false, text: messages[action] });
    } catch (error) {
      setFeedback({ error: true, text: friendlyError(error) });
    } finally {
      setWorking(false);
    }
  }

  return {
    page,
    canEdit,
    navigate,
    agents,
    versions,
    runs,
    tools,
    health,
    connected,
    description,
    setDescription,
    draft,
    setDraft,
    working,
    selectedAgent,
    selectedAgentId,
    selectAgent,
    selectedRun,
    selectedRunId,
    selectRun,
    task,
    setTask,
    versionGoal,
    setVersionGoal,
    events,
    checkpoint,
    feedback,
    setFeedback,
    prepareDraft,
    saveDraft,
    createVersion,
    startRun,
    controlRun,
    refresh,
  };
}
