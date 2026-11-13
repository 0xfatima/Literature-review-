"use client";
import Icon from "@/components/ui/Icon";
import Logo from "@/components/brand/Logo";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, apiFetch, json } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { stageLabel } from "@/lib/stages";
import type { Project, ProjectList } from "@/lib/types";

export default function ProjectsPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const me = useRequireAuth();
  const [showArchived, setShowArchived] = useState(false);
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  const list = useQuery({
    queryKey: ["projects", showArchived],
    queryFn: () => apiFetch<ProjectList>(`/projects?include_archived=${showArchived}`),
    enabled: me.isSuccess,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["projects"] });
  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : "Request failed.");

  const create = useMutation({
    mutationFn: () => apiFetch<Project>("/projects", { method: "POST", ...json({ title, topic }) }),
    onSuccess: (p) => {
      setTitle("");
      setTopic("");
      // The list (also used by the workspace's project switcher) is now stale.
      qc.invalidateQueries({ queryKey: ["projects"] });
      router.push(`/projects/${p.id}`);
    },
    onError: fail,
  });
  const archive = useMutation({
    mutationFn: (p: Project) =>
      apiFetch<Project>(`/projects/${p.id}`, {
        method: "PATCH",
        ...json({ expected_version: p.row_version, archived: p.archived_at === null }),
      }),
    onSuccess: refresh,
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/projects/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setConfirmDelete(null);
      refresh();
    },
    onError: fail,
  });
  const logout = useMutation({
    mutationFn: () => apiFetch<void>("/auth/logout", { method: "POST" }),
    // Full page load: drops every cached query of the old session from memory, and avoids the auth guard
    // treating the now-missing session as "expired" (which would add ?next= and send the user back here).
    onSettled: () => window.location.assign("/login"),
  });

  if (me.isPending) return <p className="p-6 text-muted">Loading…</p>;
  if (!me.isSuccess) return null;

  return (
    <div className="flex min-h-screen bg-panel">
      {/* All projects live in the sidebar; the main area is for starting a new one. */}
      <aside
        aria-label="Your projects"
        className="flex w-72 shrink-0 flex-col border-r border-line bg-canvas max-md:hidden"
      >
        <div className="px-5 pb-4 pt-5">
          <Logo size={26} />
        </div>
        <div className="flex items-center justify-between px-5 pb-2">
          <button
            type="button"
            className="flex items-center gap-1 text-sm font-semibold text-ink"
            aria-expanded={!collapsed}
            onClick={() => setCollapsed((v) => !v)}
          >
            Projects and chats
            <Icon name="chevronDown" size={14} className={collapsed ? "-rotate-90" : ""} />
          </button>
          <label className="flex items-center gap-1.5 text-xs text-muted">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            />
            Show archived
          </label>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 pb-4" hidden={collapsed}>
          {list.isPending && <p className="px-3 text-sm text-muted">Loading…</p>}
          {list.isError && (
            <p role="alert" className="px-3 text-sm text-danger">
              Could not load projects.
            </p>
          )}
          {list.isSuccess && list.data.items.length === 0 && (
            <p className="px-3 text-sm text-muted">No projects yet. Your reviews will appear here.</p>
          )}
          <ul className="flex flex-col">
            {list.data?.items.map((p) => (
              <li key={p.id} className="group flex items-start gap-2 rounded-lg px-3 py-2 hover:bg-raised">
                <span aria-hidden className="mt-1.5 h-2 w-2 shrink-0 rounded-full border border-muted" />
                <div className="min-w-0 flex-1">
                  <Link
                    className="block truncate text-sm text-ink no-underline hover:no-underline"
                    href={`/projects/${p.id}`}
                    title={p.title}
                  >
                    {p.title}
                  </Link>
                  <div className="mt-0.5 flex items-center gap-2 text-xs text-muted">
                    <span className="truncate">
                      {stageLabel(p.status)}
                      {p.archived_at ? " · Archived" : ""} · {new Date(p.updated_at).toLocaleDateString()}
                    </span>
                    <span className="ml-auto flex shrink-0 items-center gap-1">
                      {confirmDelete === p.id ? (
                        <>
                          <button
                            className="rounded px-1 font-semibold text-danger hover:bg-canvas"
                            onClick={() => remove.mutate(p.id)}
                          >
                            Delete?
                          </button>
                          <button
                            className="rounded px-1 hover:bg-canvas"
                            onClick={() => setConfirmDelete(null)}
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            className="rounded px-1 hover:bg-canvas hover:text-ink"
                            title={p.archived_at ? "Unarchive" : "Archive"}
                            onClick={() => archive.mutate(p)}
                          >
                            {p.archived_at ? "Unarchive" : "Archive"}
                          </button>
                          <button
                            className="rounded px-1 hover:bg-canvas hover:text-danger"
                            title="Delete"
                            onClick={() => setConfirmDelete(p.id)}
                          >
                            Delete
                          </button>
                        </>
                      )}
                    </span>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </nav>
        <div className="flex flex-col gap-2 border-t border-line px-5 py-4 text-xs text-muted">
          <span className="truncate" aria-label="Signed in as">
            {me.data.email}
          </span>
          <div className="flex gap-2">
            <Link className="btn-secondary px-2 py-1 text-xs" href="/settings">
              AI model
            </Link>
            <button className="btn-secondary px-2 py-1 text-xs" onClick={() => logout.mutate()}>
              Sign out
            </button>
          </div>
        </div>
      </aside>

      <main className="flex flex-1 items-start justify-center p-6 md:items-center">
        <div className="w-full max-w-xl">
          <div className="mb-6 flex items-center justify-between md:hidden">
            <Logo size={24} />
            <button className="btn-secondary" onClick={() => logout.mutate()}>
              Sign out
            </button>
          </div>
          <h1 className="mb-1 text-3xl font-semibold tracking-tight">New project</h1>
          <p className="mb-6 text-sm text-muted">What do you want to find out?</p>
          <form
            className="flex flex-col gap-3 rounded-2xl border border-line bg-canvas p-6 shadow-sm"
            onSubmit={(e) => {
              e.preventDefault();
              setError(null);
              create.mutate();
            }}
          >
            <label className="text-sm font-medium" htmlFor="title">
              Title
            </label>
            <input
              id="title"
              className="auth-input"
              required
              maxLength={200}
              placeholder="e.g. Quantum machine learning in clinical practice"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
            <label className="mt-2 text-sm font-medium" htmlFor="topic">
              Research topic
            </label>
            <textarea
              id="topic"
              className="auth-input"
              rows={4}
              required
              maxLength={5000}
              placeholder="Describe the question your review should answer."
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
            />
            <div role="alert" aria-live="polite" className="min-h-5 text-sm text-danger">
              {error}
            </div>
            <button className="auth-submit" disabled={create.isPending || !title.trim() || !topic.trim()}>
              {create.isPending ? "Creating…" : "Create project"}
            </button>
          </form>
          {/* On phones the sidebar is hidden: list the projects under the form instead. */}
          {list.isSuccess && list.data.items.length > 0 && (
            <ul className="mt-6 flex flex-col gap-1 md:hidden">
              {list.data.items.map((p) => (
                <li key={p.id}>
                  <Link
                    className="block rounded-lg px-3 py-2 text-sm hover:bg-raised"
                    href={`/projects/${p.id}`}
                  >
                    {p.title}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </main>
    </div>
  );
}
