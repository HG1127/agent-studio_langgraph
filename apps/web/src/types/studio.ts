/** Type-safe client contract. OpenAPI generated types will replace this manually maintained file. */

export type ToolId = "utc_now" | "add_numbers";

export type ModelProtocol = "openai" | "anthropic" | "gemini" | "azure_openai";

export interface ModelProvider {
  id: string;
  name: string;
  protocol: ModelProtocol;
  base_url: string;
  group: string;
}

export interface ModelConnection {
  id: string;
  provider: string;
  name: string;
  protocol: ModelProtocol;
  base_url: string;
  model_id: string;
  is_default: boolean;
  has_api_key: boolean;
  last_status: string | null;
  last_latency_ms: number | null;
  last_tokens_per_second: number | null;
  last_tested_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ModelConnectionInput {
  provider: string;
  name: string;
  protocol: ModelProtocol;
  base_url: string;
  api_key: string;
  model_id: string;
  is_default: boolean;
}

export interface ModelDiscovery {
  models: { id: string; name: string }[];
  source: "provider_api" | "manual";
  notice: string;
}

export interface ModelSpeedResult {
  success: boolean;
  model_id: string;
  samples: number;
  latency_ms: number;
  min_latency_ms: number;
  max_latency_ms: number;
  output_tokens: number | null;
  effective_tokens_per_second: number | null;
  usage_reported: boolean;
  metric_note: string;
}

export interface AgentSpec {
  schema_version: "1.0";
  name: string;
  description: string;
  execution_mode: "autonomous";
  goal: { task: string; success_criteria: string[] };
  context: { sources: string[]; require_citations: boolean };
  tools: { enabled: ToolId[] };
  state: {
    persistence: "langgraph_sqlite_development" | "event_log_development";
    resume_enabled: boolean;
  };
  memory: { enabled: boolean; scope: "none" };
  planning: { enabled: boolean; max_replans: number };
  tool_calling: { structured_arguments: boolean; retry_limit: number };
  permissions: { allowed_tools: ToolId[]; external_write_allowed: false };
  environment: { profile: "local_read_only" };
  observability: { trace_enabled: boolean; redact_secrets: boolean };
  evaluation: { suite_id: string | null; enforced: boolean };
  stop_conditions: {
    max_model_rounds: number;
    max_tool_calls: number;
    max_duration_seconds: number;
    max_no_progress_steps: number;
  };
}

export interface Agent {
  id: string;
  spec: AgentSpec;
  created_at: string;
  current_version: number;
}

export interface AgentVersion {
  id: string;
  agent_id: string;
  version: number;
  spec: AgentSpec;
  created_at: string;
}

export type RunStatus =
  | "QUEUED"
  | "RUNNING"
  | "PAUSE_REQUESTED"
  | "PAUSED"
  | "CANCEL_REQUESTED"
  | "CANCELED"
  | "SUCCEEDED"
  | "PARTIAL"
  | "FAILED";

export interface Run {
  id: string;
  agent_id: string;
  agent_version_id: string | null;
  status: RunStatus;
  input: string;
  output: string | null;
  error: string | null;
  created_at: string;
}

export interface RunEvent {
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Checkpoint {
  available: boolean;
  model_rounds: number;
  tool_calls: number;
  next_nodes: string[];
  final_status: string | null;
}

export interface Health {
  status: string;
  database_backend: string;
  postgres_schema_status: string;
  model_key_configured: boolean;
  authentication_available: boolean;
  worker_online: boolean;
  checkpointing: string;
}

export interface StudioTool {
  id: string;
  description: string;
  risk: string;
}

export type StudioPage = "create" | "agents" | "runs" | "tools" | "models";
export type RunAction = "pause" | "resume" | "cancel" | "retry";

export interface Feedback {
  error: boolean;
  text: string;
}
