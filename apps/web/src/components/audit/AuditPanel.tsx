"use client";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "@/lib/api";
import { actorLabel, auditCsvUrl, auditQuery, changeSummary } from "@/lib/audit";
import type { AuditEventView, AuditPage } from "@/lib/types";

function EventRow({ e }: { e: AuditEventView }) {
  const summary = changeSummary(e);
  return (
    <li className="flex flex-col gap-0.5 border-b border-line py-2 text-sm">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <time dateTime={e.at} className="font-mono text-xs text-muted">
          {new Date(e.at).toLocaleString()}
        </time>
        <span className="font-medium">{e.label}</span>
        <span className="rounded border border-line px-1.5 text-xs text-muted">{e.category}</span>
        <span className="text-xs text-muted">{actorLabel(e.actor)}</span>
      </div>
      <div className="text-xs text-muted">
        <span className="font-mono">{e.action}</span>
        {e.entity_id && (
          <>
            {" · "}
            {e.entity_type} <span className="font-mono">{e.entity_id.slice(0, 8)}</span>
          </>
        )}
        {summary && <> · {summary}</>}
        {e.reason && <> · Reason: {e.reason}</>}
      </div>
    </li>
  );
}

/** Read-only, append-only audit trail of this project. Nothing here can edit or delete history. */
export default function AuditPanel({ projectId }: { projectId: string }) {
  const [category, setCategory] = useState("");
  const [actor, setActor] = useState("");
  const cats = useQuery({
    queryKey: ["audit", "categories"],
    queryFn: () => apiFetch<string[]>("/audit/categories"),
    staleTime: Infinity,
  });
  const log = useInfiniteQuery({
    queryKey: ["audit", projectId, category, actor],
    queryFn: ({ pageParam }) =>
      apiFetch<AuditPage>(
        `/projects/${projectId}/audit-events?${auditQuery({ category, actor, cursor: pageParam })}`,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const items = log.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <section aria-labelledby="audit-heading" className="flex max-w-4xl flex-col gap-3">
      <div>
        <h2 id="audit-heading" className="text-lg font-semibold">
          Audit log
        </h2>
        <p className="text-sm text-muted">
          Every significant action in this project, newest first. Entries are append-only: corrections appear
          as new entries and never rewrite earlier ones. Free text you typed is not stored here, only what
          changed.
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col text-xs text-muted">
          Category
          <select
            className="mt-1 rounded border border-line bg-canvas px-2 py-1 text-sm text-ink"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option value="">All</option>
            {(cats.data ?? []).map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs text-muted">
          Actor
          <select
            className="mt-1 rounded border border-line bg-canvas px-2 py-1 text-sm text-ink"
            value={actor}
            onChange={(e) => setActor(e.target.value)}
          >
            <option value="">Anyone</option>
            <option value="user">People</option>
            <option value="system">System processes</option>
          </select>
        </label>
        <a
          className="rounded border border-line px-3 py-1 text-sm hover:border-mark"
          href={auditCsvUrl(projectId, category || undefined)}
          download
        >
          Export CSV
        </a>
      </div>
      {log.isLoading && <p className="text-muted">Loading audit log…</p>}
      {log.isError && (
        <p role="alert" className="text-danger">
          Could not load the audit log.
        </p>
      )}
      {!log.isLoading && !log.isError && items.length === 0 && (
        <p className="text-muted">No events match these filters.</p>
      )}
      <ul aria-label="Audit events">
        {items.map((e) => (
          <EventRow key={e.id} e={e} />
        ))}
      </ul>
      {log.hasNextPage && (
        <button
          type="button"
          className="self-start rounded border border-line px-3 py-1 text-sm hover:border-mark disabled:opacity-50"
          onClick={() => log.fetchNextPage()}
          disabled={log.isFetchingNextPage}
        >
          {log.isFetchingNextPage ? "Loading…" : "Load older events"}
        </button>
      )}
    </section>
  );
}
