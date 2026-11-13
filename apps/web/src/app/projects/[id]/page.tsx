"use client";
import { PrismMark } from "@/components/brand/Logo";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ChatPanel from "@/components/chat/ChatPanel";
import CommandPalette, { type Command } from "@/components/command/CommandPalette";
import IdeShell from "@/components/layout/IdeShell";
import { useLayout } from "@/components/layout/useLayout";
import { ActivityProvider, CurrentActivity } from "@/components/status/Activity";
import { NotificationBell } from "@/components/status/Notifications";
import StatusBar from "@/components/status/StatusBar";
import { StatusProvider, useStatus } from "@/components/status/StatusContext";
import StepsPanel from "@/components/steps/StepsPanel";
import Icon from "@/components/ui/Icon";
import Menu from "@/components/ui/Menu";
import { useTheme } from "@/components/ui/theme";
import Workspace from "@/components/workspace/Workspace";
import {
  type InspectorTab,
  UnsavedChangesProvider,
  WorkspaceProvider,
} from "@/components/workspace/WorkspaceContext";
import { ApiError, apiFetch, json } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { stageLabel } from "@/lib/stages";
import { REVIEW_STEPS, type ViewId, isViewId, nextAfterClose, viewLabel } from "@/lib/steps";
import type { Conversation, ConversationList, Job, Project, WorkflowState } from "@/lib/types";

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <Suspense fallback={<p className="p-6 text-muted">Loading…</p>}>
      <StatusProvider>
        <UnsavedChangesProvider>
          <ProjectWorkspace id={id} />
        </UnsavedChangesProvider>
      </StatusProvider>
    </Suspense>
  );
}

/** Project facts that do not need screen space all the time: shown on request in a modal dialog. */
function ProjectDetails({
  open,
  onClose,
  project,
  conversations,
}: {
  open: boolean;
  onClose: () => void;
  project: Project;
  conversations: ConversationList | undefined;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  const messages = conversations?.items.reduce((n, c) => n + c.message_count, 0);
  return (
    <dialog
      ref={ref}
      aria-labelledby="details-h"
      onClose={onClose}
      className="surface-float w-[min(32rem,92vw)] p-0 backdrop:bg-black/40"
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 id="details-h" className="font-semibold">
          Project details
        </h2>
        <button type="button" className="btn-icon" aria-label="Close" onClick={onClose}>
          <Icon name="x" />
        </button>
      </div>
      <div className="flex flex-col gap-4 px-4 py-4 text-sm">
        <section aria-labelledby="topic-h">
          <h3 id="topic-h" className="section-title mb-1">
            Original topic
          </h3>
          <p className="reading whitespace-pre-wrap">{project.topic_original}</p>
        </section>
        <dl className="grid grid-cols-[8rem_1fr] gap-y-1.5">
          <dt className="text-muted">Current step</dt>
          <dd>{stageLabel(project.status)}</dd>
          <dt className="text-muted">Created</dt>
          <dd>{new Date(project.created_at).toLocaleString()}</dd>
          <dt className="text-muted">Last updated</dt>
          <dd>{new Date(project.updated_at).toLocaleString()}</dd>
          <dt className="text-muted">Chat threads</dt>
          <dd>{conversations ? conversations.total : "…"}</dd>
          <dt className="text-muted">Messages</dt>
          <dd>{messages ?? "…"}</dd>
          {project.archived_at && (
            <>
              <dt className="text-muted">Archived</dt>
              <dd>{new Date(project.archived_at).toLocaleString()} (read-only)</dd>
            </>
          )}
        </dl>
      </div>
    </dialog>
  );
}

function ProjectWorkspace({ id }: { id: string }) {
  const qc = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const me = useRequireAuth();
  const { setSave, reportError, notify } = useStatus();
  const [title, setTitle] = useState<string | null>(null);
  const [layout, update] = useLayout();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [theme, setTheme] = useTheme();
  const [isMac, setIsMac] = useState(false);
  useEffect(() => setIsMac(/Mac|iPhone|iPad/.test(navigator.platform)), []);

  const viewParam = search.get("tab");
  const active: ViewId | null = isViewId(viewParam) ? viewParam : null;
  const [openViews, setOpenViews] = useState<ViewId[]>(() => (active ? [active] : []));
  useEffect(() => {
    if (active) setOpenViews((o) => (o.includes(active) ? o : [...o, active]));
  }, [active]);
  const threadParam = search.get("c");
  const paperParam = search.get("paper");
  const INSPECTOR_TABS: InspectorTab[] = ["overview", "abstract", "fulltext", "evidence", "screening"];
  const ptabParam = search.get("ptab") as InspectorTab | null;
  const inspectorTab: InspectorTab = ptabParam && INSPECTOR_TABS.includes(ptabParam) ? ptabParam : "overview";

  const setParams = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(window.location.search);
      for (const [k, v] of Object.entries(patch)) {
        if (v === null) next.delete(k);
        else next.set(k, v);
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [pathname, router],
  );

  const openView = useCallback(
    (v: ViewId) => {
      setOpenViews((o) => (o.includes(v) ? o : [...o, v]));
      setParams({ tab: v });
    },
    [setParams],
  );
  const closeView = useCallback(
    (v: ViewId) => {
      const next = nextAfterClose(openViews, v, active);
      setOpenViews((o) => o.filter((x) => x !== v));
      setParams({ tab: next });
    },
    [openViews, active, setParams],
  );
  // Bumped whenever something asks for the chat, so a narrow screen switches to it.
  const [chatKey, setChatKey] = useState(0);
  const openChat = useCallback(
    (conversationId?: string | null) => {
      update({ chatOpen: true });
      setChatKey((k) => k + 1);
      if (conversationId) {
        qc.invalidateQueries({ queryKey: ["conversations", id] });
        setParams({ c: conversationId });
      }
      requestAnimationFrame(() => document.getElementById("composer")?.focus());
    },
    [update, qc, id, setParams],
  );
  const openSteps = useCallback(() => update({ stepsOpen: true }), [update]);

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

  // Ctrl/⌘ K opens the command palette from anywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  /** The first thread of a project is the guided setup: it starts the review workflow, whose questions arrive in
   *  that thread. Before this, "Start a thread" made an empty chat that nothing ever answered. Later threads are
   *  free conversations. */
  const createThread = useMutation({
    mutationFn: async (): Promise<{ conversationId: string; guided: boolean }> => {
      const wf = await apiFetch<WorkflowState>(`/projects/${id}/workflow`).catch(() => null);
      if (wf && wf.run === null) {
        const started = await apiFetch<WorkflowState>(`/projects/${id}/workflow`, { method: "POST" });
        if (started.run?.conversation_id)
          return { conversationId: started.run.conversation_id, guided: true };
      }
      const c = await apiFetch<Conversation>(`/projects/${id}/conversations`, {
        method: "POST",
        ...json({}),
      });
      qc.setQueryData<ConversationList>(["conversations", id], (old) =>
        old ? { ...old, items: [c, ...old.items], total: old.total + 1 } : old,
      );
      return { conversationId: c.id, guided: false };
    },
    onSuccess: ({ conversationId, guided }) => {
      if (guided) {
        qc.invalidateQueries({ queryKey: ["conversations", id] });
        qc.invalidateQueries({ queryKey: ["workflow", id] });
      }
      setParams({ c: conversationId });
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

  const workerCheck = useMutation({
    mutationFn: () =>
      apiFetch<Job>("/jobs/ping", { method: "POST", ...json({ project_id: id, delay_s: 3 }) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs", id] });
      notify({
        tone: "info",
        title: "Checking the background worker",
        body: "You will be told when it answers.",
      });
    },
    onError: (e) =>
      reportError(
        e instanceof ApiError && e.code === "job_already_active"
          ? "A worker check is already running."
          : e instanceof ApiError
            ? e.message
            : "Could not start the worker check.",
      ),
  });

  const logout = useMutation({
    mutationFn: () => apiFetch<void>("/auth/logout", { method: "POST" }),
    // Full page load: drops every cached query of the old session from memory, and avoids the auth guard
    // treating the now-missing session as "expired" (which would add ?next= and send the user back here).
    onSettled: () => window.location.assign("/login"),
  });

  const editorVisible = active !== null && openViews.includes(active);
  const mod = isMac ? "⌘" : "Ctrl";

  const commands = useMemo<Command[]>(() => {
    const steps: Command[] = REVIEW_STEPS.flatMap((s, i) =>
      s.views.map((v) => ({
        id: `open:${v}`,
        group: "Open a step",
        label: viewLabel(v),
        hint: `${i + 1}. ${s.label}`,
        keywords: `${s.label} ${s.summary}`,
        run: () => openView(v),
      })),
    );
    return [
      ...steps,
      {
        id: "steps",
        group: "View",
        label: layout.stepsOpen ? "Hide review steps" : "Show review steps",
        keywords: "panel progress stages",
        run: () => update({ stepsOpen: !layout.stepsOpen }),
      },
      {
        id: "chat",
        group: "View",
        label: "Go to the chat",
        keywords: "message composer",
        run: () => openChat(),
      },
      ...(active
        ? [{ id: "close", group: "View", label: `Close ${viewLabel(active)}`, run: () => closeView(active) }]
        : []),
      { id: "thread", group: "Chat", label: "New thread", run: () => createThread.mutate() },
      {
        id: "rename",
        group: "Project",
        label: "Rename project",
        run: () => setTitle(project.data?.title ?? ""),
      },
      { id: "details", group: "Project", label: "Project details", run: () => setDetailsOpen(true) },
      {
        id: "worker",
        group: "Project",
        label: "Check the background worker",
        keywords: "diagnostics jobs ping",
        run: () => workerCheck.mutate(),
      },
      { id: "projects", group: "Project", label: "All projects", run: () => router.push("/projects") },
      ...(["system", "light", "dark"] as const).map((t) => ({
        id: `theme:${t}`,
        group: "Appearance",
        label: t === "system" ? "Theme: match the system" : `Theme: ${t}`,
        hint: theme === t ? "Current" : undefined,
        run: () => setTheme(t),
      })),
      {
        id: "ai-settings",
        group: "Account",
        label: "AI model and API key",
        run: () => router.push("/settings"),
      },
      { id: "signout", group: "Account", label: "Sign out", run: () => logout.mutate() },
    ];
  }, [
    layout.stepsOpen,
    active,
    theme,
    project.data?.title,
    openView,
    closeView,
    openChat,
    update,
    createThread,
    workerCheck,
    router,
    setTheme,
    logout,
  ]);

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

  const titleBar = (
    <>
      <nav aria-label="Breadcrumb" className="flex min-w-0 flex-1 items-center gap-1 lg:flex-initial">
        <Link
          href="/projects"
          aria-label="Prismatic home"
          className="mr-1 shrink-0 no-underline hover:no-underline"
        >
          <PrismMark size={22} />
        </Link>
        <Link
          href="/projects"
          className="hidden shrink-0 rounded-md px-2 py-1 text-sm text-muted no-underline hover:bg-raised hover:text-ink hover:no-underline sm:inline-block"
        >
          Projects
        </Link>
        <Icon name="chevronRight" size={14} className="hidden text-muted sm:block" />
        {title !== null ? (
          <form
            className="flex items-center gap-2"
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
              className="input w-64 py-1"
              value={title}
              maxLength={200}
              autoFocus
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setTitle(null);
              }}
            />
            <button className="btn py-1" disabled={!title.trim() || rename.isPending}>
              Save
            </button>
            <button type="button" className="btn-ghost" onClick={() => setTitle(null)}>
              Cancel
            </button>
          </form>
        ) : (
          <>
            <h1 className="truncate px-1 text-sm font-semibold" title={p.title}>
              {p.title}
            </h1>
            <Menu
              label="Project actions"
              items={[
                {
                  label: "Rename project",
                  icon: "pencil",
                  disabled: !canWrite,
                  onSelect: () => setTitle(p.title),
                },
                { label: "Project details", icon: "info", onSelect: () => setDetailsOpen(true) },
                { label: "All projects", icon: "file", onSelect: () => router.push("/projects") },
              ]}
            />
          </>
        )}
      </nav>

      <div className="hidden flex-1 justify-center px-2 lg:flex">
        <button
          type="button"
          className="flex h-8 w-full max-w-sm items-center gap-2 rounded-md border border-line bg-canvas px-2.5 text-left text-xs text-muted hover:border-muted"
          onClick={() => setPaletteOpen(true)}
          aria-keyshortcuts={isMac ? "Meta+K" : "Control+K"}
        >
          <Icon name="search" size={14} />
          <span className="flex-1">Go to a step or run a command</span>
          <kbd className="font-sans">{mod} K</kbd>
        </button>
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <CurrentActivity />
        <button
          type="button"
          className="btn-icon lg:hidden"
          aria-label="Commands"
          onClick={() => setPaletteOpen(true)}
        >
          <Icon name="search" size={18} />
        </button>
        {editorVisible && !layout.chatOpen && (
          <button
            type="button"
            className="btn-icon hidden md:inline-flex"
            aria-label="Show chat"
            title="Show chat"
            onClick={() => update({ chatOpen: true })}
          >
            <Icon name="chat" size={18} />
          </button>
        )}
        <button
          type="button"
          className="flex h-8 items-center gap-1.5 rounded-md px-2 text-sm text-muted hover:bg-raised hover:text-ink aria-pressed:bg-raised aria-pressed:text-ink"
          aria-pressed={layout.stepsOpen}
          aria-controls="ide-steps"
          onClick={() => update({ stepsOpen: !layout.stepsOpen })}
        >
          <Icon name="steps" size={18} />
          <span className="hidden md:inline">Steps</span>
          <span className="sr-only md:hidden">Review steps</span>
        </button>
        <NotificationBell />
        <Menu
          label={`Account: ${me.data.email}`}
          triggerClassName="btn-icon"
          trigger={
            <span
              aria-hidden
              className="flex h-6 w-6 items-center justify-center rounded-full bg-raised text-xs font-semibold uppercase text-ink"
            >
              {me.data.email.slice(0, 1)}
            </span>
          }
          items={[
            { label: me.data.email, icon: "user", disabled: true, onSelect: () => {} },
            ...(["system", "light", "dark"] as const).map((t) => ({
              label: t === "system" ? "Match system theme" : t === "light" ? "Light theme" : "Dark theme",
              hint: theme === t ? "✓" : undefined,
              onSelect: () => setTheme(t),
            })),
            { label: "AI model and API key", onSelect: () => router.push("/settings") },
            { label: "Sign out", onSelect: () => logout.mutate() },
          ]}
        />
      </div>
    </>
  );

  return (
    <WorkspaceProvider
      paperId={paperParam}
      inspectorTab={inspectorTab}
      onPaper={(paper, ptab) => setParams({ paper, ptab: paper ? ptab : null })}
      onTab={(t) => {
        if (isViewId(t)) openView(t);
      }}
      conversationId={selected?.id ?? null}
      activeView={active}
    >
      <ActivityProvider project={p} openView={openView} openChat={openChat} openSteps={openSteps}>
        <IdeShell
          layout={layout}
          update={update}
          titleBar={titleBar}
          editorVisible={editorVisible}
          editorKey={active}
          chatKey={chatKey}
          editorLabel={active ? viewLabel(active) : "Step"}
          chat={
            <ChatPanel
              project={p}
              conversation={selected}
              conversations={conversations.data}
              onSelectThread={(c) => setParams({ c: c.id })}
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
              wide={!editorVisible}
              onHide={editorVisible ? () => update({ chatOpen: false }) : undefined}
            />
          }
          workspace={
            <Workspace
              project={p}
              open={openViews}
              active={editorVisible ? active : null}
              onActivate={openView}
              onClose={closeView}
              onOpenConversation={(cid) => openChat(cid)}
            />
          }
          steps={
            <StepsPanel
              activeView={editorVisible ? active : null}
              onOpenView={(v) => {
                openView(v);
                // As a drawer (narrow screens) the panel would cover the step it just opened.
                if (window.matchMedia("(max-width: 1023px)").matches) update({ stepsOpen: false });
              }}
              onClose={() => update({ stepsOpen: false })}
            />
          }
          statusBar={<StatusBar project={p} isMac={isMac} />}
        />
        <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} commands={commands} />
        <ProjectDetails
          open={detailsOpen}
          onClose={() => setDetailsOpen(false)}
          project={p}
          conversations={conversations.data}
        />
      </ActivityProvider>
    </WorkspaceProvider>
  );
}
