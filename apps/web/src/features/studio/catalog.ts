import type { RunStatus, StudioPage } from "@/src/types/studio";

export const navigation: { id: StudioPage; label: string; glyph: string }[] = [
  { id: "create", label: "AI 创建中心", glyph: "✦" },
  { id: "agents", label: "我的 Agents", glyph: "◉" },
  { id: "runs", label: "运行中心", glyph: "▤" },
  { id: "tools", label: "工具与能力", glyph: "⌘" },
];

export const templates = [
  {
    icon: "◈",
    name: "研究助手",
    detail: "整理问题、查找证据、生成报告",
    prompt:
      "创建一个研究助手，针对用户给定的研究目标，计划分析步骤，基于获授权信息回答，并明确区分事实和推测。",
  },
  {
    icon: "▦",
    name: "数据分析助手",
    detail: "计算、核对并解释数据",
    prompt:
      "创建一个数据分析助手，能够用已授权的计算工具核对数字，说明计算过程，输出清晰的分析结论。",
  },
  {
    icon: "✉",
    name: "办公助手",
    detail: "组织任务、准备内容草稿",
    prompt:
      "创建一个办公助手，根据用户给定文本整理待办、工作计划与邮件草稿，涉及对外操作时必须获得批准。",
  },
];

export const capabilities: [string, string, string][] = [
  ["Goal / Task", "任务目标与成功标准", "已建模"],
  ["Context", "上下文来源与引用", "基础配置"],
  ["Tools", "工具注册与使用", "本地工具"],
  ["State", "检查点、暂停与恢复", "单机可用"],
  ["Memory", "受控长期记忆", "待开发"],
  ["Planning", "基础任务规划与执行图", "初步实现"],
  ["Tool Calling", "真实结构化调用", "已接入"],
  ["Permissions", "权限与审批", "部分实现"],
  ["Environment", "沙盒与工作环境", "待开发"],
  ["Observability", "执行轨迹", "基础追踪"],
  ["Evaluation", "测试与发布门槛", "待开发"],
  ["Stop Condition", "轮数、时长、工具限额", "基础实施"],
];

export const runStatusLabels: Record<RunStatus, string> = {
  QUEUED: "等待执行",
  RUNNING: "执行中",
  PAUSE_REQUESTED: "正在暂停",
  PAUSED: "已暂停",
  CANCEL_REQUESTED: "正在取消",
  CANCELED: "已取消",
  SUCCEEDED: "执行完成",
  PARTIAL: "部分完成",
  FAILED: "执行失败",
};
