import type { Agent, AgentSpec, AgentVersion, Run } from "@/src/types/studio";

export const API_BASE_URL = "/backend";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(API_BASE_URL + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    cache: "no-store",
    credentials: "same-origin",
  });
  const raw = await response.text();
  let payload: unknown;
  try {
    payload = raw ? JSON.parse(raw) : {};
  } catch {
    payload = { detail: raw };
  }
  if (!response.ok) {
    const detail = (payload as { detail?: unknown }).detail;
    throw new ApiError(
      response.status,
      typeof detail === "string" ? detail : `请求失败（${response.status}）`,
    );
  }
  return payload as T;
}

export function scopedRequest<T>(
  path: string,
  workspaceId: string,
  options: RequestInit = {},
): Promise<T> {
  return request<T>(path, {
    ...options,
    headers: { ...options.headers, "X-Workspace-ID": workspaceId },
  });
}

export const studioApi = {
  createDraft: (description: string, workspaceId: string) =>
    scopedRequest<{ spec: AgentSpec }>("/v1/agents/drafts/generate", workspaceId, {
      method: "POST",
      body: JSON.stringify({ description }),
    }),
  createAgent: (spec: AgentSpec, workspaceId: string) =>
    scopedRequest<Agent>("/v1/agents", workspaceId, { method: "POST", body: JSON.stringify(spec) }),
  listVersions: (id: string, workspaceId: string) =>
    scopedRequest<AgentVersion[]>(`/v1/agents/${id}/versions`, workspaceId),
  createVersion: (id: string, spec: AgentSpec, expectedVersion: number, workspaceId: string) =>
    scopedRequest<AgentVersion>(`/v1/agents/${id}/versions`, workspaceId, {
      method: "POST",
      body: JSON.stringify({ expected_version: expectedVersion, spec }),
    }),
  createRun: (agentId: string, input: string, workspaceId: string) =>
    scopedRequest<Run>(`/v1/agents/${agentId}/runs`, workspaceId, {
      method: "POST",
      body: JSON.stringify({ input }),
    }),
};
