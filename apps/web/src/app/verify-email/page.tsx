"use client";
import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";
import AuthShell from "@/components/auth/AuthShell";
import { ApiError, apiFetch, json } from "@/lib/api";
import type { User } from "@/lib/types";

function Verify() {
  const token = useSearchParams().get("token") ?? "";
  const confirm = useMutation({
    mutationFn: () => apiFetch<User>("/auth/verify-email/confirm", { method: "POST", ...json({ token }) }),
  });
  useEffect(() => {
    if (token) confirm.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  if (!token) return <p className="text-sm">This link is incomplete.</p>;
  if (confirm.isSuccess)
    return (
      <p role="status" className="text-sm">
        Thank you: <strong>{confirm.data.email}</strong> is confirmed. <Link href="/login">Sign in</Link>.
      </p>
    );
  if (confirm.isError)
    return (
      <p role="alert" className="text-sm text-danger">
        {confirm.error instanceof ApiError ? confirm.error.message : "Could not reach the server."} Sign in to
        ask for a new link.
      </p>
    );
  return <p className="text-sm text-muted">Confirming your email address…</p>;
}

export default function VerifyEmailPage() {
  return (
    <AuthShell title="Confirm your email">
      <Suspense>
        <Verify />
      </Suspense>
    </AuthShell>
  );
}
