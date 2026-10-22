"use client";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
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
  const isRegister = mode === "register";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (isRegister) await apiFetch("/auth/register", { method: "POST", ...json({ email, password }) });
      await apiFetch("/auth/login", { method: "POST", ...json({ email, password }) });
      await qc.invalidateQueries({ queryKey: ["me"] });
      router.push(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-6 p-6">
      <div>
        <p className="mb-1 text-sm text-mark">Systematic Review Scientist</p>
        <h1 className="text-2xl font-semibold">{isRegister ? "Create your account" : "Sign in"}</h1>
      </div>
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
            className="input"
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
            className="input"
            aria-describedby={isRegister ? "pw-hint" : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {isRegister && (
            <p id="pw-hint" className="text-xs text-muted">
              At least 12 characters.
            </p>
          )}
        </div>
        <div role="alert" aria-live="polite" className="min-h-5 text-sm text-danger">
          {error}
        </div>
        <button type="submit" className="btn" disabled={busy}>
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
      {!isRegister && (
        <p className="text-xs text-muted">
          Password recovery is not available yet. If you are locked out, ask the administrator to reset your
          password.
        </p>
      )}
    </main>
  );
}
