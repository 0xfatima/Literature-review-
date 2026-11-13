"use client";
import Logo from "@/components/brand/Logo";
import Link from "next/link";
import AiSettings from "@/components/settings/AiSettings";
import { useRequireAuth } from "@/lib/auth";

export default function SettingsPage() {
  const me = useRequireAuth();
  if (me.isPending) return <p className="p-6 text-muted">Loading…</p>;
  if (!me.isSuccess) return null;
  return (
    <main className="mx-auto max-w-3xl p-6">
      <header className="mb-8 flex items-center justify-between">
        <div>
          <Logo size={22} />
          <h1 className="text-2xl font-semibold">AI model</h1>
        </div>
        <Link className="btn-secondary" href="/projects">
          Back to projects
        </Link>
      </header>
      <AiSettings />
    </main>
  );
}
