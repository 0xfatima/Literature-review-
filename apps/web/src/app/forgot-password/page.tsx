"use client";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import AuthShell from "@/components/auth/AuthShell";
import type { AuthConfig } from "@/lib/account";
import { ApiError, apiFetch, json } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const config = useQuery({ queryKey: ["auth-config"], queryFn: () => apiFetch<AuthConfig>("/auth/config") });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await apiFetch("/auth/password-reset/request", { method: "POST", ...json({ email }) });
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (config.data && !config.data.password_reset_available)
    return (
      <AuthShell title="Reset your password">
        <p className="text-sm text-muted">
          This server cannot send email, so self-service reset is switched off. Ask the administrator to reset
          your password.
        </p>
      </AuthShell>
    );

  return (
    <AuthShell title="Reset your password">
      {sent ? (
        <p role="status" className="text-sm">
          If an account exists for <strong>{email}</strong>, we sent it a link to choose a new password. The
          link expires soon and works once. Check your spam folder if it does not arrive.
        </p>
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
          <p className="text-sm text-muted">Enter your account email and we will send you a reset link.</p>
          <div className="flex flex-col gap-1">
            <label htmlFor="email" className="text-sm font-medium">
              Email
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              required
              className="input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div role="alert" aria-live="polite" className="min-h-5 text-sm text-danger">
            {error}
          </div>
          <button type="submit" className="btn" disabled={busy || !email}>
            {busy ? "Please wait…" : "Send reset link"}
          </button>
        </form>
      )}
    </AuthShell>
  );
}
