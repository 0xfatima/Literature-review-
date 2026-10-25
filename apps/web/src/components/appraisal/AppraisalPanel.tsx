"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useStatus } from "@/components/status/StatusContext";
import { useUnsavedChanges } from "@/components/workspace/WorkspaceContext";
import { ApiError, apiFetch, json } from "@/lib/api";
import { humanize, undecidedDomains } from "@/lib/synthesis";
import type {
  AiJobStatus,
  AppraisalAnswer,
  AppraisalFramework,
  AppraisalOverview,
  AppraisalPaper,
  AppraisalRecord,
} from "@/lib/types";

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

interface DomainForm {
  judgement: string;
  justification: string;
  excerpt: string;
  page_no: string;
  document_page_id: string | null;
}

function initialForm(
  answers: Record<string, AppraisalAnswer>,
  fw: AppraisalFramework,
): Record<string, DomainForm> {
  const out: Record<string, DomainForm> = {};
  for (const d of fw.domains) {
    const a = answers[d.key] ?? {};
    out[d.key] = {
      judgement: a.decided_by ? (a.judgement ?? "") : "",
      justification: a.decided_by ? (a.justification ?? "") : "",
      excerpt: a.decided_by ? (a.excerpt ?? "") : "",
      page_no: a.decided_by && a.page_no ? String(a.page_no) : "",
      document_page_id: a.decided_by ? (a.document_page_id ?? null) : null,
    };
  }
  return out;
}

function Editor({
  projectId,
  paper,
  fw,
  record,
  canWrite,
}: {
  projectId: string;
  paper: AppraisalPaper;
  fw: AppraisalFramework;
  record: AppraisalRecord | undefined;
  canWrite: boolean;
}) {
  const qc = useQueryClient();
  const { reportError } = useStatus();
  const answers = record?.answers ?? {};
  const [form, setForm] = useState(() => initialForm(answers, fw));
  const [initial] = useState(() => JSON.stringify(initialForm(answers, fw)));
  const [overall, setOverall] = useState(record?.overall_judgement ?? "");
  const [overallWhy, setOverallWhy] = useState(record?.overall_justification ?? "");
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  useUnsavedChanges(
    `appraisal-${paper.paper.id}-${fw.key}`,
    JSON.stringify(form) !== initial ||
      overall !== (record?.overall_judgement ?? "") ||
      overallWhy !== (record?.overall_justification ?? "") ||
      !!reason,
  );
  const url = `/projects/${projectId}/appraisal/papers/${paper.paper.id}`;
  const done = () => {
    setMsg(null);
    void qc.invalidateQueries({ queryKey: ["appraisal", projectId] });
  };
  const fail = (e: unknown, f: string) => {
    setMsg(errText(e, f));
    reportError(errText(e, f));
  };
  const save = useMutation({
    mutationFn: () => {
      const domains: Record<string, Record<string, unknown>> = {};
      for (const d of fw.domains) {
        const f = form[d.key];
        if (!f) continue;
        const page = Number(f.page_no);
        domains[d.key] = f.judgement
          ? {
              judgement: f.judgement,
              justification: f.justification,
              ...(f.excerpt.trim()
                ? f.document_page_id
                  ? { excerpt: f.excerpt, document_page_id: f.document_page_id }
                  : { excerpt: f.excerpt, page_no: Number.isInteger(page) ? page : null }
                : {}),
            }
          : { judgement: null };
      }
      return apiFetch<AppraisalRecord>(url, {
        method: "PUT",
        ...json({
          framework_key: fw.key,
          domains,
          overall_judgement: overall || null,
          overall_justification: overallWhy || null,
          reason: reason || null,
          expected_row_version: record?.row_version,
        }),
      });
    },
    onSuccess: done,
    onError: (e) => fail(e, "Could not save the appraisal."),
  });
  const approve = useMutation({
    mutationFn: () =>
      apiFetch(`${url}/approve`, {
        method: "POST",
        ...json({ framework_key: fw.key, expected_row_version: record?.row_version }),
      }),
    onSuccess: done,
    onError: (e) => fail(e, "Could not approve the appraisal."),
  });
  const set = (key: string, patch: Partial<DomainForm>) =>
    setForm((f) => ({ ...f, [key]: { ...(f[key] as DomainForm), ...patch } }));
  const undecided = undecidedDomains(fw, answers);
  return (
    <div className="flex flex-col gap-3">
      <div className="text-xs text-muted">
        {fw.based_on}.{" "}
        <a href={fw.reference_url} target="_blank" rel="noreferrer">
          Official guidance
        </a>
        . {fw.overall_rule}
      </div>
      {record && (
        <p className="text-xs" role="status">
          Status: <strong>{record.status === "approved" ? "Approved by reviewer" : "Draft"}</strong> ·{" "}
          {record.domains_judged}/{record.domains_total} domains judged by a reviewer
          {record.suggested_overall && ` · Suggested overall: ${humanize(record.suggested_overall)}`}
        </p>
      )}
      {fw.domains.map((d) => {
        const ai = answers[d.key]?.ai_suggestion;
        const f = form[d.key] as DomainForm;
        const id = `${fw.key}-${d.key}`;
        return (
          <fieldset key={d.key} className="rounded border border-line bg-panel p-3">
            <legend className="px-1 text-sm font-semibold">{d.label}</legend>
            <p className="mb-2 text-xs text-muted">{d.prompt}</p>
            {ai && (
              <div
                className="mb-2 rounded border border-dashed border-line p-2 text-xs"
                aria-label="AI suggestion"
              >
                <span className="font-semibold">AI suggestion (not a judgement):</span>{" "}
                {humanize(ai.judgement)}
                {ai.justification && ` — ${ai.justification}`}
                {ai.excerpt && (
                  <span className="block text-muted">
                    “{ai.excerpt}” (p. {ai.page_no})
                  </span>
                )}
                {!ai.supported && <span className="block text-muted">No supporting quote was verified.</span>}
                {canWrite && (
                  <button
                    type="button"
                    className="btn-ghost mt-1"
                    onClick={() =>
                      set(d.key, {
                        judgement: ai.judgement,
                        justification: ai.justification ?? "",
                        excerpt: ai.excerpt ?? "",
                        page_no: ai.page_no ? String(ai.page_no) : "",
                        document_page_id: ai.document_page_id,
                      })
                    }
                  >
                    Use as starting point
                  </button>
                )}
              </div>
            )}
            <div className="grid gap-2 md:grid-cols-[12rem_1fr]">
              <label className="text-2xs text-muted" htmlFor={`${id}-j`}>
                Reviewer judgement
              </label>
              <select
                id={`${id}-j`}
                className="input"
                value={f.judgement}
                disabled={!canWrite}
                onChange={(e) => set(d.key, { judgement: e.target.value })}
              >
                <option value="">Not judged</option>
                {fw.judgements.map((j) => (
                  <option key={j} value={j}>
                    {humanize(j)}
                  </option>
                ))}
              </select>
              <label className="text-2xs text-muted" htmlFor={`${id}-w`}>
                Justification
              </label>
              <textarea
                id={`${id}-w`}
                className="input"
                rows={2}
                value={f.justification}
                disabled={!canWrite}
                onChange={(e) => set(d.key, { justification: e.target.value })}
              />
              <label className="text-2xs text-muted" htmlFor={`${id}-x`}>
                Source excerpt (verbatim, optional)
              </label>
              <textarea
                id={`${id}-x`}
                className="input"
                rows={2}
                value={f.excerpt}
                disabled={!canWrite}
                onChange={(e) => set(d.key, { excerpt: e.target.value, document_page_id: null })}
              />
              <label className="text-2xs text-muted" htmlFor={`${id}-p`}>
                Page
              </label>
              <input
                id={`${id}-p`}
                className="input !w-28"
                inputMode="numeric"
                value={f.page_no}
                disabled={!canWrite}
                onChange={(e) => set(d.key, { page_no: e.target.value, document_page_id: null })}
              />
            </div>
          </fieldset>
        );
      })}
      <div className="grid gap-2 md:grid-cols-[12rem_1fr]">
        <label className="text-2xs text-muted" htmlFor={`${fw.key}-overall`}>
          Overall judgement
        </label>
        <select
          id={`${fw.key}-overall`}
          className="input"
          value={overall}
          disabled={!canWrite}
          onChange={(e) => setOverall(e.target.value)}
        >
          <option value="">Not decided</option>
          {fw.overall_options.map((o) => (
            <option key={o} value={o}>
              {humanize(o)}
            </option>
          ))}
        </select>
        <label className="text-2xs text-muted" htmlFor={`${fw.key}-why`}>
          Overall justification
        </label>
        <textarea
          id={`${fw.key}-why`}
          className="input"
          rows={2}
          value={overallWhy}
          disabled={!canWrite}
          onChange={(e) => setOverallWhy(e.target.value)}
        />
        {record?.status === "approved" && (
          <>
            <label className="text-2xs text-muted" htmlFor={`${fw.key}-reason`}>
              Reason for changing an approved appraisal
            </label>
            <input
              id={`${fw.key}-reason`}
              className="input"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </>
        )}
      </div>
      {msg && (
        <p role="alert" className="text-xs text-danger">
          {msg}
        </p>
      )}
      {canWrite && (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => save.mutate()}
            disabled={save.isPending}
          >
            Save judgements
          </button>
          {record && record.status !== "approved" && (
            <button
              type="button"
              className="btn"
              onClick={() => approve.mutate()}
              disabled={approve.isPending || undecided.length > 0}
              title={undecided.length ? `Still to judge: ${undecided.join(", ")}` : "Approve this appraisal"}
            >
              Approve appraisal
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function AppraisalPanel({ projectId, canWrite }: { projectId: string; canWrite: boolean }) {
  const qc = useQueryClient();
  const { reportError } = useStatus();
  const [selected, setSelected] = useState<string | null>(null);
  const [fwChoice, setFwChoice] = useState<Record<string, string>>({});
  const { data } = useQuery({
    queryKey: ["appraisal", projectId, "overview"],
    queryFn: () => apiFetch<AppraisalOverview>(`/projects/${projectId}/appraisal`),
  });
  const { data: job } = useQuery({
    queryKey: ["appraisal", projectId, "suggestions"],
    queryFn: () => apiFetch<AiJobStatus>(`/projects/${projectId}/appraisal/suggestions`),
    refetchInterval: (q) => (q.state.data?.active_job_id ? 3000 : false),
  });
  const suggest = useMutation({
    mutationFn: () =>
      apiFetch(`/projects/${projectId}/appraisal/suggestions`, {
        method: "POST",
        ...json({ frameworks: fwChoice }),
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["appraisal", projectId] }),
    onError: (e) => reportError(errText(e, "Could not start appraisal suggestions.")),
  });
  if (!data) return <p className="text-muted">Loading…</p>;
  if (data.papers.length === 0)
    return (
      <div className="max-w-prose py-10">
        <h2 className="mb-2 text-base font-semibold">No studies to appraise yet</h2>
        <p className="text-muted">
          Include papers during screening. Each included study is then appraised with a framework that suits
          its design; there is no single quality score.
        </p>
      </div>
    );
  const paper = data.papers.find((p) => p.paper.id === selected);
  const fwKey = paper
    ? (fwChoice[paper.paper.id] ?? paper.records[0]?.framework_key ?? paper.suggested_framework)
    : null;
  const fw = data.frameworks.find((f) => f.key === fwKey);
  return (
    <div className="flex flex-col gap-4" data-testid="appraisal-panel">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-base font-semibold">Critical appraisal</h2>
        {canWrite && (
          <button
            type="button"
            className="btn-secondary"
            onClick={() => suggest.mutate()}
            disabled={!!job?.active_job_id || suggest.isPending}
          >
            Ask AI for suggestions
          </button>
        )}
      </div>
      {job && !job.consent.can_send_documents && (
        <p className="text-xs text-muted">
          AI suggestions are off: {job.consent.blocked_reason} Consent is managed in the Tables tab.
        </p>
      )}
      {job?.last_result?.stop_reason && (
        <p className="text-xs text-muted">Last run stopped: {humanize(job.last_result.stop_reason)}.</p>
      )}
      <div className="overflow-x-auto rounded border border-line">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-raised">
            <tr>
              <th className="p-2">Study</th>
              <th className="p-2">Design (extracted)</th>
              <th className="p-2">Framework</th>
              <th className="p-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {data.papers.map((p) => {
              const key = fwChoice[p.paper.id] ?? p.records[0]?.framework_key ?? p.suggested_framework ?? "";
              const rec = p.records.find((r) => r.framework_key === key);
              return (
                <tr
                  key={p.paper.id}
                  className={`border-t border-line ${selected === p.paper.id ? "bg-raised" : ""}`}
                >
                  <td className="p-2">
                    <button
                      type="button"
                      className="text-left text-link"
                      onClick={() => setSelected(p.paper.id)}
                    >
                      {p.paper.title}
                    </button>
                    <div className="text-muted">{p.paper.year ?? "n.d."}</div>
                  </td>
                  <td className="p-2">{p.design_hint ?? "Not extracted"}</td>
                  <td className="p-2">
                    <label className="sr-only" htmlFor={`fw-${p.paper.id}`}>
                      Framework for {p.paper.title}
                    </label>
                    <select
                      id={`fw-${p.paper.id}`}
                      className="input !py-1"
                      value={key}
                      onChange={(e) => setFwChoice((m) => ({ ...m, [p.paper.id]: e.target.value }))}
                    >
                      <option value="">Choose…</option>
                      {data.frameworks.map((f) => (
                        <option key={f.key} value={f.key}>
                          {f.label}
                          {f.key === p.suggested_framework ? " (suggested)" : ""}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="p-2">
                    {rec
                      ? rec.status === "approved"
                        ? `Approved · overall ${humanize(rec.overall_judgement)}`
                        : `Draft · ${rec.domains_judged}/${rec.domains_total} judged${rec.ai_suggestions ? " · AI suggestions available" : ""}`
                      : "Not started"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {paper && fw && (
        <section aria-label={`Appraisal of ${paper.paper.title}`}>
          <h3 className="mb-2 text-sm font-semibold">
            {paper.paper.title} — {fw.label}
          </h3>
          <Editor
            key={`${paper.paper.id}-${fw.key}-${paper.records.find((r) => r.framework_key === fw.key)?.row_version ?? 0}`}
            projectId={projectId}
            paper={paper}
            fw={fw}
            record={paper.records.find((r) => r.framework_key === fw.key)}
            canWrite={canWrite}
          />
        </section>
      )}
      {paper && !fw && (
        <p className="text-xs text-muted">Choose a framework for this study to appraise it.</p>
      )}
    </div>
  );
}
