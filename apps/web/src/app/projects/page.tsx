"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, apiFetch, json } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
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

  const list = useQuery({
    queryKey: ["projects", showArchived],
    queryFn: () => apiFetch<ProjectList>(`/projects?include_archived=${showArchived}`),
    enabled: me.isSuccess,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["projects"] });
  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : "Request failed.");

  const create = useMutation({
    mutationFn: () => apiFetch<Project>("/projects", { method: "POST", ...json({ title, topic }) }),
    onSuccess: (p) => { setTitle(""); setTopic(""); router.push(`/projects/${p.id}`); },
    onError: fail,
  });
  const archive = useMutation({
    mutationFn: (p: Project) => apiFetch<Project>(`/projects/${p.id}`, {
      method: "PATCH", ...json({ expected_version: p.row_version, archived: p.archived_at === null }) }),
    onSuccess: refresh, onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/projects/${id}`, { method: "DELETE" }),
    onSuccess: () => { setConfirmDelete(null); refresh(); }, onError: fail,
  });
  const logout = useMutation({
    mutationFn: () => apiFetch<void>("/auth/logout", { method: "POST" }),
    onSettled: () => { qc.clear(); router.push("/login"); },
  });

  if (me.isPending) return <p className="p-6">Loading…</p>;
  if (!me.isSuccess) return null;

  return (
    <main className="mx-auto max-w-3xl p-6">
      <header className="mb-8 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Projects</h1>
        <div className="flex items-center gap-3 text-sm">
          <span aria-label="Signed in as">{me.data.email}</span>
          <button className="btn-secondary" onClick={() => logout.mutate()}>Sign out</button>
        </div>
      </header>

      <form className="mb-8 flex flex-col gap-3 rounded-lg border border-slate-200 p-4 dark:border-slate-800"
            onSubmit={(e) => { e.preventDefault(); setError(null); create.mutate(); }}>
        <h2 className="font-medium">New review project</h2>
        <label className="text-sm font-medium" htmlFor="title">Title</label>
        <input id="title" className="input" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
        <label className="text-sm font-medium" htmlFor="topic">Research topic</label>
        <textarea id="topic" className="input" rows={3} required maxLength={5000} value={topic}
                  onChange={(e) => setTopic(e.target.value)} />
        <button className="btn self-start" disabled={create.isPending || !title.trim() || !topic.trim()}>Create project</button>
      </form>

      <div role="alert" aria-live="polite" className="mb-3 min-h-5 text-sm text-red-700 dark:text-red-400">{error}</div>

      <label className="mb-3 flex items-center gap-2 text-sm">
        <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> Show archived
      </label>

      {list.isPending && <p>Loading projects…</p>}
      {list.isError && <p role="alert">Could not load projects.</p>}
      {list.isSuccess && list.data.items.length === 0 && <p className="text-slate-500">No projects yet. Create one above.</p>}
      <ul className="flex flex-col gap-3">
        {list.data?.items.map((p) => (
          <li key={p.id} className="flex items-center justify-between rounded-lg border border-slate-200 p-4 dark:border-slate-800">
            <div>
              <Link className="font-medium underline" href={`/projects/${p.id}`}>{p.title}</Link>
              <p className="text-xs text-slate-500">
                Status: {p.status}{p.archived_at ? " · archived" : ""} · created {new Date(p.created_at).toLocaleDateString()}
              </p>
            </div>
            <div className="flex gap-2">
              <button className="btn-secondary" onClick={() => archive.mutate(p)}>{p.archived_at ? "Unarchive" : "Archive"}</button>
              {confirmDelete === p.id ? (
                <>
                  <button className="btn-danger" onClick={() => remove.mutate(p.id)}>Confirm delete</button>
                  <button className="btn-secondary" onClick={() => setConfirmDelete(null)}>Cancel</button>
                </>
              ) : (
                <button className="btn-danger" onClick={() => setConfirmDelete(p.id)}>Delete</button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </main>
  );
}
