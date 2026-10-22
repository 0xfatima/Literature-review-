"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useState } from "react";
import IdeShell from "@/components/layout/IdeShell";
import { ApiError, apiFetch, json } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { Project } from "@/lib/types";

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const qc = useQueryClient();
  const me = useRequireAuth();
  const [title, setTitle] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["project", id],
    queryFn: () => apiFetch<Project>(`/projects/${id}`),
    enabled: me.isSuccess,
    retry: false,
  });
  const rename = useMutation({
    mutationFn: (p: Project) =>
      apiFetch<Project>(`/projects/${p.id}`, {
        method: "PATCH",
        ...json({ expected_version: p.row_version, title }),
      }),
    onSuccess: (p) => {
      qc.setQueryData(["project", id], p);
      setTitle(null);
      setMessage("Saved.");
    },
    onError: (e) =>
      setMessage(
        e instanceof ApiError && e.code === "version_conflict"
          ? "Changed elsewhere. Reload to see the latest version."
          : "Could not save.",
      ),
  });

  if (me.isPending || (me.isSuccess && q.isPending)) return <p className="p-6">Loading…</p>;
  if (!me.isSuccess) return null;
  if (q.error instanceof ApiError && q.error.status === 404)
    return (
      <main className="p-6">
        <h1 className="text-xl font-semibold">Project not found</h1>
        <p className="mt-2 text-sm">It may have been deleted, or you may not have access.</p>
        <Link className="mt-4 inline-block underline" href="/projects">
          Back to projects
        </Link>
      </main>
    );
  if (!q.isSuccess)
    return (
      <p role="alert" className="p-6">
        Could not load this project.
      </p>
    );
  const p = q.data;
  const editing = title !== null;

  return (
    <IdeShell
      header={
        <>
          <div className="flex items-center gap-3">
            <Link href="/projects" className="text-sm underline">
              ← Projects
            </Link>
            {editing ? (
              <form
                className="flex gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  rename.mutate(p);
                }}
              >
                <label htmlFor="rename" className="sr-only">
                  Project title
                </label>
                <input
                  id="rename"
                  className="input"
                  value={title}
                  maxLength={200}
                  onChange={(e) => setTitle(e.target.value)}
                />
                <button className="btn" disabled={!title.trim() || rename.isPending}>
                  Save
                </button>
                <button type="button" className="btn-secondary" onClick={() => setTitle(null)}>
                  Cancel
                </button>
              </form>
            ) : (
              <>
                <h1 className="text-lg font-semibold">{p.title}</h1>
                <button
                  className="btn-secondary"
                  onClick={() => {
                    setMessage(null);
                    setTitle(p.title);
                  }}
                >
                  Rename
                </button>
              </>
            )}
          </div>
          <span role="status" className="text-xs text-slate-500">
            {message}
          </span>
        </>
      }
      statusBar={
        <>
          Status: {p.status} · version {p.row_version}
          {p.archived_at ? " · archived" : ""}
        </>
      }
    >
      <h2 className="mb-4 text-xl font-semibold">Overview</h2>
      <dl className="grid max-w-2xl grid-cols-[10rem_1fr] gap-y-3 text-sm">
        <dt className="font-medium">Original topic</dt>
        <dd className="whitespace-pre-wrap">{p.topic_original}</dd>
        <dt className="font-medium">Status</dt>
        <dd>{p.status}</dd>
        <dt className="font-medium">Created</dt>
        <dd>{new Date(p.created_at).toLocaleString()}</dd>
        <dt className="font-medium">Last updated</dt>
        <dd>{new Date(p.updated_at).toLocaleString()}</dd>
      </dl>
      <p className="mt-8 text-sm text-slate-500">
        Protocol, search, screening and later stages are built in subsequent phases.
      </p>
    </IdeShell>
  );
}
