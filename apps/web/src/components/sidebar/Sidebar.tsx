"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { PARSE_LABELS } from "@/lib/documents";
import type { Conversation, ConversationList, DocumentList, ProjectList } from "@/lib/types";

function relTime(iso: string): string {
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function Section({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="border-b border-line px-2 py-3">
      <div className="mb-1.5 flex items-center justify-between px-1">
        <h2 className="section-title">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

/** Explorer: switch project, pick a chat thread, see files. Everything listed comes from the API. */
export default function Sidebar({
  projectId,
  conversations,
  selectedId,
  onSelect,
  onNewThread,
  canWrite,
  creating,
}: {
  projectId: string;
  conversations: ConversationList | undefined;
  selectedId: string | null;
  onSelect: (c: Conversation) => void;
  onNewThread: () => void;
  canWrite: boolean;
  creating: boolean;
}) {
  const projects = useQuery({
    queryKey: ["projects", false],
    queryFn: () => apiFetch<ProjectList>("/projects?include_archived=false"),
  });

  const files = useQuery({
    queryKey: ["documents", projectId, "list", "sidebar"],
    queryFn: () => apiFetch<DocumentList>(`/projects/${projectId}/documents?limit=8`),
    refetchInterval: (q) =>
      q.state.data?.items.some((d) => d.parse_status === "queued" || d.parse_status === "processing")
        ? 3000
        : false,
  });

  return (
    <div className="text-sm">
      <Section
        title="Projects"
        action={
          <Link href="/projects" className="text-xs">
            All projects
          </Link>
        }
      >
        {projects.isPending && <p className="px-1 text-muted">Loading…</p>}
        {projects.isError && <p className="px-1 text-danger">Could not load projects.</p>}
        <ul aria-label="Switch project">
          {projects.data?.items.map((p) => {
            const current = p.id === projectId;
            return (
              <li key={p.id}>
                <Link
                  href={`/projects/${p.id}`}
                  aria-current={current ? "page" : undefined}
                  className={`block truncate rounded px-2 py-1 no-underline hover:bg-raised hover:no-underline ${
                    current ? "bg-raised text-ink shadow-[inset_2px_0_0_rgb(var(--mark))]" : "text-muted"
                  }`}
                >
                  {p.title}
                </Link>
              </li>
            );
          })}
        </ul>
      </Section>

      <Section
        title="Chat threads"
        action={
          <button
            type="button"
            className="btn-ghost text-xs"
            onClick={onNewThread}
            disabled={!canWrite || creating}
            title={canWrite ? undefined : "Unarchive the project to start a thread"}
          >
            New thread
          </button>
        }
      >
        {!conversations && <p className="px-1 text-muted">Loading…</p>}
        {conversations?.items.length === 0 && (
          <p className="px-1 text-muted">No threads yet. Start one to talk through this review.</p>
        )}
        <ul aria-label="Chat threads">
          {conversations?.items.map((c) => {
            const current = c.id === selectedId;
            return (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => onSelect(c)}
                  aria-current={current ? "true" : undefined}
                  className={`flex w-full items-baseline justify-between gap-2 rounded px-2 py-1 text-left hover:bg-raised ${
                    current ? "bg-raised text-ink shadow-[inset_2px_0_0_rgb(var(--mark))]" : "text-muted"
                  }`}
                >
                  <span className="truncate">{c.title}</span>
                  <time className="shrink-0 text-2xs" dateTime={c.updated_at}>
                    {relTime(c.updated_at)}
                  </time>
                </button>
              </li>
            );
          })}
        </ul>
      </Section>

      <Section title="Files">
        {files.isError && <p className="px-1 text-danger">Could not load files.</p>}
        {files.data && files.data.items.length === 0 && (
          <p className="px-1 text-muted">
            No files yet. Upload papers you are authorised to access in the Files tab.
          </p>
        )}
        <ul className="flex flex-col gap-0.5" data-testid="sidebar-files">
          {files.data?.items.map((d) => (
            <li key={d.id} className="px-1" title={d.original_filename}>
              <span className="block truncate">{d.original_filename}</span>
              <span className="text-2xs text-muted">{PARSE_LABELS[d.parse_status]}</span>
            </li>
          ))}
        </ul>
        {files.data && files.data.total > files.data.items.length && (
          <p className="px-1 pt-1 text-2xs text-muted">
            and {files.data.total - files.data.items.length} more in the Files tab
          </p>
        )}
      </Section>
    </div>
  );
}
