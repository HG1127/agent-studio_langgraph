"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, request, scopedRequest } from "@/src/lib/api";

export type WorkspaceRole = "owner" | "admin" | "member" | "viewer";
export interface Workspace {
  id: string;
  name: string;
  role: WorkspaceRole;
}
export interface SessionProfile {
  user: { id: string; email: string };
  workspaces: Workspace[];
}

const explain = (error: unknown) =>
  error instanceof Error ? error.message : "无法连接到本地后端，请确认 API 已启动。";

export function useSession() {
  const [ready, setReady] = useState(false);
  const [profile, setProfile] = useState<SessionProfile | null>(null);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateProfile = useCallback((next: SessionProfile) => {
    setProfile(next);
    setWorkspaceId((current) =>
      next.workspaces.some((space) => space.id === current)
        ? current
        : (next.workspaces[0]?.id ?? null),
    );
  }, []);

  const refreshSession = useCallback(async () => {
    try {
      const next = await request<SessionProfile>("/v1/auth/me");
      updateProfile(next);
      setError(null);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        setProfile(null);
        setWorkspaceId(null);
        setError(null);
      } else {
        setError(explain(cause));
      }
    } finally {
      setReady(true);
    }
  }, [updateProfile]);

  useEffect(() => {
    const initial = window.setTimeout(() => void refreshSession(), 0);
    const periodic = window.setInterval(() => void refreshSession(), 60000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(periodic);
    };
  }, [refreshSession]);

  async function authenticate(mode: "login" | "register", email: string, password: string) {
    setWorking(true);
    setError(null);
    try {
      const next = await request<SessionProfile>("/v1/auth/" + mode, {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      updateProfile(next);
    } catch (cause) {
      setError(explain(cause));
    } finally {
      setWorking(false);
      setReady(true);
    }
  }

  async function logout() {
    setWorking(true);
    setError(null);
    try {
      await request<{ ok: boolean }>("/v1/auth/logout", { method: "POST" });
      setProfile(null);
      setWorkspaceId(null);
    } catch (cause) {
      setError(explain(cause));
    } finally {
      setWorking(false);
    }
  }

  async function createWorkspace(name: string) {
    setWorking(true);
    setError(null);
    try {
      const created = await request<Workspace>("/v1/workspaces", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      await refreshSession();
      setWorkspaceId(created.id);
    } catch (cause) {
      setError(explain(cause));
    } finally {
      setWorking(false);
    }
  }

  async function inviteMember(email: string, role: "admin" | "member" | "viewer") {
    if (!workspaceId) return;
    setWorking(true);
    setError(null);
    try {
      await scopedRequest("/v1/workspaces/" + workspaceId + "/members", workspaceId, {
        method: "POST",
        body: JSON.stringify({ user_email: email, role }),
      });
    } catch (cause) {
      setError(explain(cause));
    } finally {
      setWorking(false);
    }
  }

  function selectWorkspace(id: string) {
    if (profile?.workspaces.some((space) => space.id === id)) {
      setWorkspaceId(id);
      setError(null);
    }
  }

  const currentWorkspace = profile?.workspaces.find((space) => space.id === workspaceId);
  return {
    ready,
    profile,
    workspaceId,
    currentWorkspace,
    working,
    error,
    authenticate,
    logout,
    createWorkspace,
    inviteMember,
    selectWorkspace,
  };
}
