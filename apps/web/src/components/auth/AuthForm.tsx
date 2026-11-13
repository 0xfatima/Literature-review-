"use client";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import AuthFrame from "@/components/auth/AuthFrame";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import type { RegisterResult } from "@/lib/account";
import { ApiError, apiFetch, json } from "@/lib/api";
import { safeNext } from "@/lib/nav";

export default function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const qc = useQueryClient();
  const next = safeNext(useSearchParams().get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [unverified, setUnverified] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const isRegister = mode === "register";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setUnverified(false);
    setBusy(true);
    try {
      if (isRegister) {
        const r = await apiFetch<RegisterResult>("/auth/register", {
          method: "POST",
          ...json({ email, password }),
        });
        if (r.verification_required) {
          setNotice(
            `We sent a confirmation link to ${r.email}. Open it to activate your account, then sign in.`,
          );
          return;
        }
      }
      await apiFetch("/auth/login", { method: "POST", ...json({ email, password }) });
      await qc.invalidateQueries({ queryKey: ["me"] });
      router.push(next);
    } catch (err) {
      if (err instanceof ApiError && err.code === "email_not_verified") setUnverified(true);
      setError(err instanceof ApiError ? err.message : "Could not reach the server. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setBusy(true);
    try {
      await apiFetch("/auth/verify-email/request", { method: "POST", ...json({ email }) });
      setError(null);
      setUnverified(false);
      setNotice(`If ${email} has an unconfirmed account, a new confirmation link is on its way.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthFrame
      title={isRegister ? "Create your account" : "Welcome back"}
      subtitle={isRegister ? "Start your first review in a minute." : "Sign in to continue your reviews."}
    >
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <div className="flex flex-col gap-1">
          <label htmlFor="email" className="text-sm font-medium">
            Email
          </label>
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            required
            className="auth-input"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="password" className="text-sm font-medium">
            Password
          </label>
          <input
            id="password"
            name="password"
            type="password"
            required
            autoComplete={isRegister ? "new-password" : "current-password"}
            className="auth-input"
            aria-describedby={isRegister ? "pw-hint" : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {isRegister ? (
            <p id="pw-hint" className="text-xs text-muted">
              At least 12 characters.
            </p>
          ) : (
            <Link href="/forgot-password" className="self-end text-xs">
              Forgot your password?
            </Link>
          )}
        </div>
        <div role="alert" aria-live="polite" className="min-h-5 text-sm text-danger">
          {error}
          {unverified && (
            <button type="button" className="btn-ghost ml-2" onClick={resend} disabled={busy}>
              Send a new link
            </button>
          )}
        </div>
        {notice && (
          <p role="status" className="text-sm text-ink">
            {notice}
          </p>
        )}
        <button type="submit" className="auth-submit" disabled={busy}>
          {busy ? "Please wait…" : isRegister ? "Create account" : "Sign in"}
        </button>
      </form>
      <p className="text-sm">
        {isRegister ? (
          <>
            Already registered?{" "}
            <Link href={next === "/projects" ? "/login" : `/login?next=${encodeURIComponent(next)}`}>
              Sign in
            </Link>
          </>
        ) : (
          <>
            New here?{" "}
            <Link href={next === "/projects" ? "/register" : `/register?next=${encodeURIComponent(next)}`}>
              Create an account
            </Link>
          </>
        )}
      </p>
    </AuthFrame>
  );
}
