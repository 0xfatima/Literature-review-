"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import DecisionButtons, { type DecisionInput } from "@/components/common/DecisionButtons";
import DocumentPreview from "@/components/common/DocumentPreview";
import ExtractionTable from "@/components/common/ExtractionTable";
import PaperCard from "@/components/common/PaperCard";
import { PaperFullText } from "@/components/papers/PapersPanel";
import { useStatus } from "@/components/status/StatusContext";
import { type InspectorTab, useUnsavedChanges, useWorkspace } from "@/components/workspace/WorkspaceContext";
import { apiFetch, json } from "@/lib/api";
import { describeError } from "@/lib/workspace";
import type {
  EvidenceTable,
  ExtractionSchema,
  HumanDecision,
  PaperDetail,
  ScreeningDecisionRecord,
  ScreeningReason,
} from "@/lib/types";

const TABS: { id: InspectorTab; label: string }[] = [
  { id: "overview", label: "Metadata" },
  { id: "abstract", label: "Abstract" },
  { id: "fulltext", label: "PDF content" },
  { id: "evidence", label: "Evidence" },
  { id: "screening", label: "Screening" },
];
const LINKED = new Set(["user_linked", "confirmed", "auto_matched"]);

interface Explanation {
  status: string;
  lines: string[];
  source: string;
}
interface EvidenceHit {
  page_no: number;
  excerpt: string;
  matched_terms: string[];
  truncated_start: boolean;
  truncated_end: boolean;
}
interface EvidenceSearch {
  status: "ok" | "no_match" | "no_full_text";
  hits: EvidenceHit[];
  document_id: string | null;
  message: string | null;
  terms: string[];
}

function useApprovedSchema(projectId: string) {
  return useQuery({
    queryKey: ["extraction", projectId, "schemas"],
    queryFn: () => apiFetch<ExtractionSchema[]>(`/projects/${projectId}/extraction/schemas`),
    select: (list) =>
      list.filter((s) => s.status === "approved").sort((a, b) => b.version - a.version)[0] ?? null,
  });
}

function ScreeningTab({
  projectId,
  paperId,
  canWrite,
}: {
  projectId: string;
  paperId: string;
  canWrite: boolean;
}) {
  const qc = useQueryClient();
  const ws = useWorkspace();
  const { reportError } = useStatus();
  const [excluding, setExcluding] = useState(false);
  const history = useQuery({
    queryKey: ["screening", projectId, "history", paperId],
    queryFn: () =>
      apiFetch<{ decisions: ScreeningDecisionRecord[] }>(
        `/projects/${projectId}/screening/papers/${paperId}/history`,
      ),
  });
  const reasons = useQuery({
    queryKey: ["screening", projectId, "reasons"],
    queryFn: () =>
      apiFetch<{ items: ScreeningReason[]; require_reason: boolean }>(
        `/projects/${projectId}/screening/reasons`,
      ),
  });
  const explain = useMutation({
    mutationFn: (toChat: boolean) =>
      apiFetch<Explanation>(`/projects/${projectId}/papers/${paperId}/explain-exclusion`, {
        method: "POST",
        ...json({ conversation_id: toChat ? ws?.conversationId : null }),
      }),
    onSuccess: (_d, toChat) => {
      if (toChat) qc.invalidateQueries({ queryKey: ["messages", projectId] });
    },
    onError: (e) => reportError(describeError(e, "Could not explain this decision.")),
  });
  const decide = useMutation({
    mutationFn: (b: DecisionInput) =>
      apiFetch(`/projects/${projectId}/screening/papers/${paperId}/decision`, { method: "POST", ...json(b) }),
    onSuccess: () => setExcluding(false),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["screening", projectId] });
      qc.invalidateQueries({ queryKey: ["prisma", projectId] });
      qc.invalidateQueries({ queryKey: ["workspace", projectId] });
    },
    onError: (e) => reportError(describeError(e, "Could not save the decision.")),
  });
  const list = history.data?.decisions ?? [];
  const last = list[list.length - 1];
  const current: HumanDecision | null = last && last.decision !== "cleared" ? last.decision : null;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm">
        Title/abstract decision:{" "}
        <span className="text-ink">{last ? last.decision_label : "Not screened yet"}</span>
        {last?.reason_label && <span className="text-muted"> — {last.reason_label}</span>}
      </p>
      {reasons.data && (
        <DecisionButtons
          decision={current}
          reasons={reasons.data.items}
          requireReason={reasons.data.require_reason}
          busy={decide.isPending || !canWrite}
          excluding={excluding}
          setExcluding={setExcluding}
          onDecide={(d) => decide.mutate(d)}
        />
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="btn-secondary"
          onClick={() => explain.mutate(false)}
          disabled={explain.isPending}
        >
          {current === "exclude" ? "Explain this exclusion" : "Explain the decision record"}
        </button>
        {ws?.conversationId && canWrite && (
          <button
            type="button"
            className="btn-ghost"
            onClick={() => explain.mutate(true)}
            disabled={explain.isPending}
          >
            Explain in the chat
          </button>
        )}
      </div>
      {explain.data && (
        <div className="rounded border border-line bg-canvas p-3 text-sm" aria-live="polite">
          <ul className="list-disc pl-5">
            {explain.data.lines.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
          <p className="mt-1 text-2xs text-muted">{explain.data.source}</p>
        </div>
      )}
      {list.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted">Decision history ({list.length})</summary>
          <ol className="mt-1 flex flex-col gap-1">
            {list.map((d) => (
              <li key={d.id}>
                {new Date(d.created_at).toLocaleString()} · {d.decision_label}
                {d.reason_label && ` · ${d.reason_label}`}
                {d.note && ` · “${d.note}”`} · {d.action}
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}

function EvidenceTab({
  projectId,
  paper,
  canWrite,
  onShowPage,
}: {
  projectId: string;
  paper: PaperDetail;
  canWrite: boolean;
  onShowPage: (documentId: string, page: number, highlight: string) => void;
}) {
  const qc = useQueryClient();
  const { reportError } = useStatus();
  const schema = useApprovedSchema(projectId);
  const sid = schema.data?.id;
  const [fieldKey, setFieldKey] = useState("");
  const [query, setQuery] = useState("");
  const [values, setValues] = useState<Record<number, string>>({});
  useUnsavedChanges(
    `inspector-evidence-${paper.id}`,
    Object.values(values).some((v) => v.trim()),
  );
  const table = useQuery({
    queryKey: ["evidence", projectId, "table", sid, false],
    queryFn: () =>
      apiFetch<EvidenceTable>(`/projects/${projectId}/extraction/schemas/${sid}/table?limit=200`),
    enabled: !!sid,
  });
  const row = table.data?.rows.find((r) => r.paper.id === paper.id);
  const find = useMutation({
    mutationFn: () =>
      apiFetch<EvidenceSearch>(`/projects/${projectId}/papers/${paper.id}/find-evidence`, {
        method: "POST",
        ...json({ field_key: fieldKey || null, query: query || null }),
      }),
    onSuccess: () => setValues({}),
    onError: (e) => reportError(describeError(e, "Could not search the full text.")),
  });
  const save = useMutation({
    mutationFn: ({ hit, value }: { hit: EvidenceHit; value: string }) =>
      apiFetch(`/projects/${projectId}/extraction/schemas/${sid}/evidence/${paper.id}/${fieldKey}`, {
        method: "PUT",
        ...json({
          status: "found",
          value,
          excerpt: hit.excerpt,
          document_id: find.data?.document_id,
          page_no: hit.page_no,
          reason: "Saved from 'Find supporting evidence'",
          expected_row_version: row?.cells[fieldKey]?.row_version,
        }),
      }),
    onSuccess: (_d, v) => {
      setValues((m) => ({ ...m, [v.hit.page_no]: "" }));
      qc.invalidateQueries({ queryKey: ["evidence", projectId] });
      qc.invalidateQueries({ queryKey: ["extraction", projectId] });
    },
    onError: (e) => reportError(describeError(e, "Could not save the value.")),
  });
  const extract = useMutation({
    mutationFn: () =>
      apiFetch(`/projects/${projectId}/extraction/schemas/${sid}/runs`, {
        method: "POST",
        ...json({ paper_ids: [paper.id] }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs", projectId] }),
    onError: (e) => reportError(describeError(e, "Could not start AI extraction for this paper.")),
  });
  if (!schema.data)
    return (
      <p className="text-sm text-muted">
        Approve an extraction schema (Extraction form step) to record evidence.
      </p>
    );
  const fields = schema.data.field_definitions;
  return (
    <div className="flex flex-col gap-3">
      {row ? (
        <ExtractionTable fields={fields} cells={row.cells} onSelect={(k) => setFieldKey(k)} />
      ) : (
        <p className="text-xs text-muted">This paper is not included yet, so it has no extraction row.</p>
      )}
      <section
        aria-label="Find supporting evidence"
        className="flex flex-col gap-2 rounded border border-line p-3"
      >
        <h4 className="section-title">Find supporting evidence</h4>
        <p className="text-2xs text-muted">
          Searches this paper&rsquo;s stored full text. Passages are copied word for word with their page, so
          you can check them and save one as a cited value.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col text-2xs text-muted">
            Field
            <select className="input !w-48" value={fieldKey} onChange={(e) => setFieldKey(e.target.value)}>
              <option value="">Any (use the query)</option>
              {fields.map((f) => (
                <option key={f.field_key} value={f.field_key}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-1 flex-col text-2xs text-muted">
            Search terms (optional when a field is chosen)
            <input className="input" value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
          <button
            type="button"
            className="btn-secondary"
            disabled={find.isPending || (!fieldKey && !query.trim())}
            onClick={() => find.mutate()}
          >
            Find supporting evidence
          </button>
          {canWrite && row && (
            <button
              type="button"
              className="btn-ghost"
              onClick={() => extract.mutate()}
              disabled={extract.isPending}
            >
              Extract this paper with AI
            </button>
          )}
        </div>
        {find.data?.message && <p className="text-xs text-muted">{find.data.message}</p>}
        {find.data && find.data.hits.length > 0 && (
          <ol className="flex flex-col gap-2">
            {find.data.hits.map((h) => (
              <li key={h.page_no} className="rounded border border-line bg-canvas p-2 text-xs">
                <p>
                  <span className="font-semibold">Page {h.page_no}</span> · matches:{" "}
                  {h.matched_terms.join(", ")}
                </p>
                <p className="mt-1 whitespace-pre-wrap">
                  {h.truncated_start && "… "}
                  {h.excerpt}
                  {h.truncated_end && " …"}
                </p>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  {find.data?.document_id && (
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={() => onShowPage(find.data!.document_id!, h.page_no, h.excerpt.slice(0, 120))}
                    >
                      View on the page
                    </button>
                  )}
                  {canWrite && fieldKey && row && (
                    <>
                      <label className="sr-only" htmlFor={`val-${h.page_no}`}>
                        Value for this field
                      </label>
                      <input
                        id={`val-${h.page_no}`}
                        className="input !w-40 !py-1"
                        placeholder="Value as stated"
                        value={values[h.page_no] ?? ""}
                        onChange={(e) => setValues((m) => ({ ...m, [h.page_no]: e.target.value }))}
                      />
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={!values[h.page_no]?.trim() || save.isPending}
                        onClick={() => save.mutate({ hit: h, value: values[h.page_no]!.trim() })}
                      >
                        Save as cited value
                      </button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

export default function PaperInspector({ projectId, canWrite }: { projectId: string; canWrite: boolean }) {
  const ws = useWorkspace();
  const qc = useQueryClient();
  const { reportError } = useStatus();
  const heading = useRef<HTMLHeadingElement>(null);
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [preview, setPreview] = useState<{ documentId: string; page: number; highlight: string } | null>(
    null,
  );
  const paperId = ws?.paperId ?? null;
  const tab = ws?.inspectorTab ?? "overview";
  const paper = useQuery({
    queryKey: ["papers", projectId, "detail", paperId],
    queryFn: () => apiFetch<PaperDetail>(`/projects/${projectId}/papers/${paperId}`),
    enabled: !!paperId,
  });
  const reassess = useMutation({
    mutationFn: () =>
      apiFetch(`/projects/${projectId}/screening/relevance`, {
        method: "POST",
        ...json({ paper_ids: [paperId], use_llm: false }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs", projectId] }),
    onError: (e) => reportError(describeError(e, "Could not reassess relevance.")),
  });
  useEffect(() => {
    setPreview(null);
    if (paperId) heading.current?.focus();
  }, [paperId]);
  if (!ws || !paperId) return null;
  const close = () => ws.openPaper(null);
  const p = paper.data;
  const doc = p?.documents.find((d) => d.parse_status === "parsed" && LINKED.has(d.match_status));
  return (
    <aside
      aria-label="Paper inspector"
      className="fixed inset-0 z-30 flex flex-col overflow-y-auto border-l border-line bg-panel p-4 md:static md:inset-auto md:z-auto md:w-[28rem] md:shrink-0"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          close();
        }
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <h2 ref={heading} tabIndex={-1} className="text-sm font-semibold outline-none">
          Paper
        </h2>
        <button type="button" className="btn-ghost" onClick={close} aria-label="Close paper inspector">
          Close
        </button>
      </div>
      {paper.isPending && <p className="text-xs text-muted">Loading…</p>}
      {paper.isError && (
        <p className="text-xs text-danger">{describeError(paper.error, "Could not load this paper.")}</p>
      )}
      {p && (
        <>
          <PaperCard
            title={p.title}
            authors={p.authors}
            authorCount={p.author_count}
            year={p.year}
            venue={p.venue}
            publicationType={p.publication_type}
            headingLevel={3}
            badges={
              <>
                <span className="text-muted">{p.access_label}</span>
                {p.is_retracted && <span className="text-danger">Retracted</span>}
                {p.is_preprint && <span className="text-mark">Preprint</span>}
              </>
            }
          />
          <div className="mt-2 flex flex-wrap gap-2" aria-label="Actions for this paper">
            {canWrite && (
              <button
                type="button"
                className="btn-ghost text-xs"
                onClick={() => reassess.mutate()}
                disabled={reassess.isPending}
              >
                Reassess relevance
              </button>
            )}
            <button
              type="button"
              className="btn-ghost text-xs"
              onClick={() => ws.setInspectorTab("evidence")}
            >
              Find supporting evidence
            </button>
            <button
              type="button"
              className="btn-ghost text-xs"
              onClick={() => ws.setInspectorTab("screening")}
            >
              Explain the decision
            </button>
          </div>
          {reassess.isSuccess && (
            <p className="text-2xs text-muted" role="status">
              Relevance reassessment queued; progress is shown at the top right.
            </p>
          )}
          <div
            role="tablist"
            aria-label="Paper details"
            className="mt-3 flex flex-wrap gap-1 border-b border-line"
          >
            {TABS.map((t, i) => (
              <button
                key={t.id}
                ref={(el) => {
                  tabRefs.current[t.id] = el;
                }}
                role="tab"
                id={`insp-tab-${t.id}`}
                aria-selected={tab === t.id}
                aria-controls={`insp-panel-${t.id}`}
                tabIndex={tab === t.id ? 0 : -1}
                className={`-mb-px border-b-2 px-2 py-1 text-xs ${tab === t.id ? "border-mark text-ink" : "border-transparent text-muted"}`}
                onClick={() => ws.setInspectorTab(t.id)}
                onKeyDown={(e) => {
                  const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
                  if (!step) return;
                  e.preventDefault();
                  const next = TABS[(i + step + TABS.length) % TABS.length]!;
                  ws.setInspectorTab(next.id);
                  tabRefs.current[next.id]?.focus();
                }}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div
            role="tabpanel"
            id={`insp-panel-${tab}`}
            aria-labelledby={`insp-tab-${tab}`}
            className="pt-3 text-sm"
          >
            {tab === "overview" && (
              <dl className="grid grid-cols-[7rem_1fr] gap-y-1 text-xs">
                <dt className="text-muted">DOI</dt>
                <dd>{p.doi ?? "—"}</dd>
                <dt className="text-muted">PMID</dt>
                <dd>{p.pmid ?? "—"}</dd>
                <dt className="text-muted">Language</dt>
                <dd>{p.language ?? "—"}</dd>
                <dt className="text-muted">Keywords</dt>
                <dd>{p.keywords.join(", ") || "—"}</dd>
                <dt className="text-muted">Found in</dt>
                <dd>
                  {p.providers.join(", ")} ({p.source_count} source record{p.source_count === 1 ? "" : "s"})
                </dd>
                <dt className="text-muted">Full text</dt>
                <dd>{p.full_text_label}</dd>
              </dl>
            )}
            {tab === "abstract" && (
              <p className="whitespace-pre-wrap">
                {p.abstract ?? <span className="text-muted">No abstract was supplied by any source.</span>}
              </p>
            )}
            {tab === "fulltext" && (
              <div className="flex flex-col gap-3">
                {(preview ?? doc) && (
                  <DocumentPreview
                    key={`${preview?.documentId ?? doc?.id}-${preview?.page ?? 1}`}
                    projectId={projectId}
                    documentId={preview?.documentId ?? doc!.id}
                    page={preview?.page ?? 1}
                    highlight={preview?.highlight}
                  />
                )}
                <PaperFullText projectId={projectId} paper={p} />
              </div>
            )}
            {tab === "evidence" && (
              <EvidenceTab
                projectId={projectId}
                paper={p}
                canWrite={canWrite}
                onShowPage={(documentId, page, highlight) => {
                  setPreview({ documentId, page, highlight });
                  ws.setInspectorTab("fulltext");
                }}
              />
            )}
            {tab === "screening" && <ScreeningTab projectId={projectId} paperId={p.id} canWrite={canWrite} />}
          </div>
        </>
      )}
    </aside>
  );
}
