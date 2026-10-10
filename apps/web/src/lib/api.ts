import type { Agent, AgentSpec, AgentVersion, Run } from "@/src/types/studio";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

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

export const studioApi = {
  createDraft: (description: string) =>
    request<{ spec: AgentSpec }>("/v1/agents/drafts/generate", {
      method: "POST",
      body: JSON.stringify({ description }),
    }),
  createAgent: (spec: AgentSpec) =>
    request<Agent>("/v1/agents", { method: "POST", body: JSON.stringify(spec) }),
  listVersions: (id: string) => request<AgentVersion[]>(`/v1/agents/${id}/versions`),
  createVersion: (id: string, spec: AgentSpec, expectedVersion: number) =>
    request<AgentVersion>(`/v1/agents/${id}/versions`, {
      method: "POST",
      body: JSON.stringify({ expected_version: expectedVersion, spec }),
    }),
  createRun: (agentId: string, input: string) =>
    request<Run>(`/v1/agents/${agentId}/runs`, {
      method: "POST",
      body: JSON.stringify({ input }),
    }),
};
