import type { AgentSpec } from "@/src/types/studio";

/** Manual configuration is explicitly labelled; no AI generation is claimed. */
export function manualSpec(task: string): AgentSpec {
  return {
    schema_version: "1.0",
    name: "我的自定义助手",
    description: "手动创建的配置草稿（并非 AI 生成）",
    execution_mode: "autonomous",
    goal: {
      task,
      success_criteria: ["提供明确结果，说明没有证据支持的部分"],
    },
    context: { sources: [], require_citations: false },
    tools: { enabled: ["utc_now", "add_numbers"] },
    state: { persistence: "langgraph_sqlite_development", resume_enabled: true },
    memory: { enabled: false, scope: "none" },
    planning: { enabled: true, max_replans: 1 },
    tool_calling: { structured_arguments: true, retry_limit: 1 },
    permissions: {
      allowed_tools: ["utc_now", "add_numbers"],
      external_write_allowed: false,
    },
    environment: { profile: "local_read_only" },
    observability: { trace_enabled: true, redact_secrets: true },
    evaluation: { suite_id: null, enforced: false },
    stop_conditions: {
      max_model_rounds: 8,
      max_tool_calls: 10,
      max_duration_seconds: 120,
      max_no_progress_steps: 3,
    },
  };
}
