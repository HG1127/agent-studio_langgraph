"use client";

import { useState } from "react";
import type { useSession } from "@/src/features/auth/useSession";

type Auth = ReturnType<typeof useSession>;

export function WorkspacePanel({ auth }: { auth: Auth }) {
  const [name, setName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"admin" | "member" | "viewer">("viewer");
  const [expanded, setExpanded] = useState(false);
  const editable = auth.currentWorkspace?.role !== "viewer";
  const manager = ["owner", "admin"].includes(auth.currentWorkspace?.role ?? "");

  return (
    <div className="workspace-panel">
      <label htmlFor="workspace-select">当前工作空间</label>
      <select
        id="workspace-select"
        value={auth.workspaceId ?? ""}
        onChange={(event) => auth.selectWorkspace(event.target.value)}
      >
        {auth.profile?.workspaces.map((space) => (
          <option key={space.id} value={space.id}>
            {space.name} · {space.role}
          </option>
        ))}
      </select>
      <small>{editable ? "可编辑 Agent" : "只读权限"}</small>
      <button
        type="button"
        className="workspace-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
      >
        {expanded ? "隐藏工作空间管理" : "管理工作空间 +"}
      </button>
      {expanded && (
        <div className="workspace-controls">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (name.trim()) {
                void auth.createWorkspace(name.trim());
                setName("");
              }
            }}
          >
            <label htmlFor="workspace-new">新工作空间</label>
            <input
              id="workspace-new"
              value={name}
              minLength={2}
              maxLength={100}
              placeholder="例如：投研团队"
              onChange={(event) => setName(event.target.value)}
            />
            <button type="submit" disabled={auth.working || name.trim().length < 2}>
              创建
            </button>
          </form>
          {manager && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void auth.inviteMember(inviteEmail, inviteRole);
              }}
            >
              <label htmlFor="invite-member">添加已注册成员</label>
              <input
                id="invite-member"
                type="email"
                placeholder="成员邮箱"
                value={inviteEmail}
                onChange={(event) => setInviteEmail(event.target.value)}
              />
              <select
                value={inviteRole}
                onChange={(event) => setInviteRole(event.target.value as typeof inviteRole)}
                aria-label="成员角色"
              >
                <option value="viewer">只读</option>
                <option value="member">成员</option>
                <option value="admin">管理员</option>
              </select>
              <button type="submit" disabled={auth.working || !inviteEmail.trim()}>
                添加成员
              </button>
            </form>
          )}
        </div>
      )}
      {auth.error && (
        <p className="workspace-error" role="alert">
          {auth.error}
        </p>
      )}
    </div>
  );
}
