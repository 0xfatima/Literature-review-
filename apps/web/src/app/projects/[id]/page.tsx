"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, use, useCallback, useEffect, useState } from "react";
import ChatPanel from "@/components/chat/ChatPanel";
import IdeShell from "@/components/layout/IdeShell";
import Sidebar from "@/components/sidebar/Sidebar";
import StatusBar from "@/components/status/StatusBar";
import { StatusProvider, useStatus } from "@/components/status/StatusContext";
import Workspace, { TABS, type TabId } from "@/components/workspace/Workspace";
import { ApiError, apiFetch, json } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { Conversation, ConversationList, Project } from "@/lib/types";

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <Suspense fallback={<p className="p-6 text-muted">Loading…</p>}>
      <StatusProvider>
        <ProjectWorkspace id={id} />
      </StatusProvider>
    </Suspense>
  );
}

function ProjectWorkspace({ id }: { id: string }) {
  const qc = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const me = useRequireAuth();
  const { setSave, reportError } = useStatus();
  const [title, setTitle] = useState<string | null>(null);

  const tabParam = search.get("tab");
  const tab: TabId = TABS.some((t) => t.id === tabParam) ? (tabParam as TabId) : "status";
  const threadParam = search.get("c");

  const setParams = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(search.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === null) next.delete(k);
        else next.set(k, v);
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [pathname, router, search],
  );

  const project = useQuery({
    queryKey: ["project", id],
    queryFn: () => apiFetch<Project>(`/projects/${id}`),
    enabled: me.isSuccess,
    retry: false,
  });
  const conversations = useQuery({
    queryKey: ["conversations", id],
    queryFn: () => apiFetch<ConversationList>(`/projects/${id}/conversations?limit=200`),
    enabled: project.isSuccess,
  });

  const list = conversations.data?.items;
  const selected: Conversation | null = list?.find((c) => c.id === threadParam) ?? null;

  // No thread in the URL (or one that no longer exists): open the most recently active thread, if any.
  useEffect(() => {
    if (!list) return;
    if (threadParam && list.some((c) => c.id === threadParam)) return;
    const fallback = list[0]?.id ?? null;
    if (fallback !== threadParam) setParams({ c: fallback });
  }, [list, threadParam, setParams]);

  const createThread = useMutation({
    mutationFn: () =>
      apiFetch<Conversation>(`/projects/${id}/conversations`, { method: "POST", ...json({}) }),
    onSuccess: (c) => {
      qc.setQueryData<ConversationList>(["conversations", id], (old) =>
        old ? { ...old, items: [c, ...old.items], total: old.total + 1 } : old,
      );
      setParams({ c: c.id });
      setSave({ kind: "saved", at: new Date() });
    },
    onError: (e) => reportError(e instanceof ApiError ? e.message : "Could not start a thread."),
  });

  const rename = useMutation({
    mutationFn: (p: Project) =>
      apiFetch<Project>(`/projects/${p.id}`, {
        method: "PATCH",
        ...json({ expected_version: p.row_version, title }),
      }),
    onMutate: () => setSave({ kind: "saving" }),
    onSuccess: (p) => {
      qc.setQueryData(["project", id], p);
      qc.invalidateQueries({ queryKey: ["projects"] });
      setTitle(null);
      setSave({ kind: "saved", at: new Date() });
    },
    onError: (e) => {
      setSave({ kind: "failed", message: "project title" });
      reportError(
        e instanceof ApiError && e.code === "version_conflict"
          ? "This project changed elsewhere. Reload to see the latest version."
          : "Could not save the project title.",
      );
    },
  });

  const logout = useMutation({
    mutationFn: () => apiFetch<void>("/auth/logout", { method: "POST" }),
    // Full page load: drops every cached query of the old session from memory, and avoids the auth guard
    // treating the now-missing session as "expired" (which would add ?next= and send the user back here).
    onSettled: () => window.location.assign("/login"),
  });

  if (me.isPending || (me.isSuccess && project.isPending)) return <p className="p-6 text-muted">Loading…</p>;
  if (!me.isSuccess) return null;
  if (project.error instanceof ApiError && project.error.status === 404)
    return (
      <main className="mx-auto max-w-prose p-10">
        <h1 className="text-xl font-semibold">Project not found</h1>
        <p className="mt-2 text-muted">It may have been deleted, or you may not have access to it.</p>
        <Link className="mt-4 inline-block" href="/projects">
          Back to projects
        </Link>
      </main>
    );
  if (!project.isSuccess)
    return (
      <p role="alert" className="p-6 text-danger">
        Could not load this project.{" "}
        <button type="button" className="underline" onClick={() => project.refetch()}>
          Try again
        </button>
      </p>
    );

  const p = project.data;
  const canWrite = p.archived_at === null;

  return (
    <IdeShell
      topBar={
        <>
          <div className="flex min-w-0 items-center gap-3">
            <Link href="/projects" className="shrink-0 text-sm">
              Projects
            </Link>
            <span aria-hidden className="text-muted">
              /
            </span>
            {title !== null ? (
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
                  className="input py-1"
                  value={title}
                  maxLength={200}
                  autoFocus
                  onChange={(e) => setTitle(e.target.value)}
                />
                <button className="btn" disabled={!title.trim() || rename.isPending}>
                  Save
                </button>
                <button type="button" className="btn-ghost" onClick={() => setTitle(null)}>
                  Cancel
                </button>
              </form>
            ) : (
              <>
                <h1 className="truncate font-semibold" title={p.title}>
                  {p.title}
                </h1>
                <button type="button" className="btn-ghost text-xs" onClick={() => setTitle(p.title)}>
                  Rename
                </button>
              </>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2 text-xs text-muted">
            <span aria-label="Signed in as">{me.data.email}</span>
            <button type="button" className="btn-ghost text-xs" onClick={() => logout.mutate()}>
              Sign out
            </button>
          </div>
        </>
      }
      sidebar={
        <Sidebar
          projectId={id}
          conversations={conversations.data}
          selectedId={selected?.id ?? null}
          onSelect={(c) => setParams({ c: c.id })}
          onNewThread={() => createThread.mutate()}
          canWrite={canWrite}
          creating={createThread.isPending}
        />
      }
      chat={
        <ChatPanel
          project={p}
          conversation={selected}
          onNewThread={() => createThread.mutate()}
          onDeleted={(deletedId) => {
            // Drop it from the cached list first so the fallback never re-selects a deleted thread.
            qc.setQueryData<ConversationList>(["conversations", id], (old) =>
              old
                ? { ...old, items: old.items.filter((c) => c.id !== deletedId), total: old.total - 1 }
                : old,
            );
            setParams({ c: null });
          }}
          creating={createThread.isPending}
        />
      }
      workspace={
        <Workspace
          project={p}
          conversations={conversations.data}
          tab={tab}
          onTab={(t) => setParams({ tab: t })}
        />
      }
      statusBar={<StatusBar project={p} />}
    />
  );
}
