"use client";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import AuthFrame from "@/components/auth/AuthFrame";
import AuthIcon from "@/components/auth/AuthIcon";
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
    <AuthFrame title={isRegister ? "Create Account" : "User Login"}>
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <div className="relative">
          <label htmlFor="email" className="sr-only">
            Email
          </label>
          <AuthIcon kind="user" />
          <input
            id="email"
            placeholder="Email"
            name="email"
            type="email"
            autoComplete="email"
            required
            className="auth-input"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="relative">
          <label htmlFor="password" className="sr-only">
            Password
          </label>
          <AuthIcon kind="lock" />
          <input
            id="password"
            placeholder="Password"
            name="password"
            type="password"
            required
            autoComplete={isRegister ? "new-password" : "current-password"}
            className="auth-input"
            aria-describedby={isRegister ? "pw-hint" : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {isRegister && (
          <p id="pw-hint" className="-mt-2 px-2 text-xs text-[#59636e]">
            At least 12 characters.
          </p>
        )}
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
        {!isRegister && (
          <Link
            href="/forgot-password"
            className="text-center text-xs text-[#59636e] no-underline hover:underline"
          >
            Forgot password?
          </Link>
        )}
      </form>
      <p className="mt-10 text-center text-xs text-[#59636e]">
        {isRegister ? (
          <>
            Already have an account?{" "}
            <Link href={next === "/projects" ? "/login" : `/login?next=${encodeURIComponent(next)}`}>
              Sign in <span aria-hidden>→</span>
            </Link>
          </>
        ) : (
          <>
            {" "}
            <Link href={next === "/projects" ? "/register" : `/register?next=${encodeURIComponent(next)}`}>
              Create your account <span aria-hidden>→</span>
            </Link>
          </>
        )}
      </p>
    </AuthFrame>
  );
}
