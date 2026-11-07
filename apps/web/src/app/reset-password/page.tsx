"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import AuthShell from "@/components/auth/AuthShell";
import { ApiError, apiFetch, json } from "@/lib/api";

function ResetForm() {
  const token = useSearchParams().get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await apiFetch("/auth/password-reset/confirm", { method: "POST", ...json({ token, password }) });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (!token)
    return (
      <p className="text-sm">
        This link is incomplete. <Link href="/forgot-password">Request a new reset link</Link>.
      </p>
    );
  if (done)
    return (
      <p role="status" className="text-sm">
        Your password was changed and every device was signed out. <Link href="/login">Sign in</Link> with the
        new password.
      </p>
    );
  return (
    <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
      <div className="flex flex-col gap-1">
        <label htmlFor="password" className="text-sm font-medium">
          New password
        </label>
        <input
          id="password"
          type="password"
          autoComplete="new-password"
          className="input"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-describedby="pw-hint"
        />
        <p id="pw-hint" className="text-xs text-muted">
          At least 12 characters.
        </p>
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="confirm" className="text-sm font-medium">
          Repeat the new password
        </label>
        <input
          id="confirm"
          type="password"
          autoComplete="new-password"
          className="input"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
      </div>
      <div role="alert" aria-live="polite" className="min-h-5 text-sm text-danger">
        {error}
        {error?.includes("expired") && (
          <>
            {" "}
            <Link href="/forgot-password">Request a new link</Link>.
          </>
        )}
      </div>
      <button type="submit" className="btn" disabled={busy || !password}>
        {busy ? "Please wait…" : "Set new password"}
      </button>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <AuthShell title="Choose a new password">
      <Suspense>
        <ResetForm />
      </Suspense>
    </AuthShell>
  );
}
