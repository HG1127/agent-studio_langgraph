"use client";

import { useState } from "react";
import type { useSession } from "@/src/features/auth/useSession";

type Auth = ReturnType<typeof useSession>;

export function AuthScreen({ auth }: { auth: Auth }) {
  const [mode, setMode] = useState<"login" | "register">("register");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="auth-brand">
          <span className="brand-icon">✦</span>
          <div>
            <strong>Agent Studio</strong>
            <small>Build · Run · Improve</small>
          </div>
        </div>
        <div className="eyebrow">YOUR PRIVATE WORKSPACE</div>
        <h1>{mode === "register" ? "创建你的 AI 工作空间" : "欢迎回来"}</h1>
        <p>登录后可创建并管理自己的 Agent。不同用户的数据按工作空间隔离； 当前仅供本地开发测试。</p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void auth.authenticate(mode, email.trim(), password);
          }}
        >
          <label htmlFor="studio-email">电子邮箱</label>
          <input
            id="studio-email"
            type="email"
            value={email}
            required
            autoComplete="username"
            onChange={(event) => setEmail(event.target.value)}
            placeholder="yourname@example.com"
          />
          <label htmlFor="studio-password">密码（至少 12 位）</label>
          <input
            id="studio-password"
            type="password"
            minLength={12}
            maxLength={128}
            required
            autoComplete={mode === "register" ? "new-password" : "current-password"}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="请输入不少于 12 位的密码"
          />
          {auth.error && (
            <p className="auth-error" role="alert">
              {auth.error}
            </p>
          )}
          <button className="btn primary auth-submit" disabled={auth.working}>
            {auth.working ? "请稍候…" : mode === "register" ? "创建工作空间" : "登录"}
          </button>
        </form>
        <button
          type="button"
          className="auth-switch"
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setPassword("");
          }}
        >
          {mode === "register" ? "已有账号？登录" : "还没有账号？创建工作空间"}
        </button>
        <div className="auth-disclaimer">
          <strong>本地开发版</strong>：尚未实现正式部署所需的 HTTPS、
          密码找回、登录限速、审计和安全运营。请勿使用真实敏感资料。
        </div>
      </section>
    </main>
  );
}
