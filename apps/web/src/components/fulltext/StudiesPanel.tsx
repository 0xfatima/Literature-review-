"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiFetch, json } from "@/lib/api";
import { citeLabel, groupingProblem } from "@/lib/fulltext";
import type { ReportBrief, StudiesList } from "@/lib/types";

function ReportLine({ p }: { p: ReportBrief }) {
  return (
    <span>
      <span className="font-medium">{p.title}</span>
      <span className="text-xs text-muted"> · {citeLabel(p)}</span>
    </span>
  );
}

/**
 * Studies vs reports (PRISMA 2020). One study can be described by several reports, for example an early and a final
 * paper on the same trial. Ungrouped included reports each count as one study; grouping lowers "studies included"
 * without changing any decision.
 */
export default function StudiesPanel({ projectId, canWrite }: { projectId: string; canWrite: boolean }) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string[]>([]);
  const [shortName, setShortName] = useState("");
  const [notes, setNotes] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["studies", projectId],
    queryFn: () => apiFetch<StudiesList>(`/projects/${projectId}/studies`),
  });
  const refresh = () =>
    ["studies", "prisma", "fulltext", "workspace"].forEach((k) =>
      qc.invalidateQueries({ queryKey: [k, projectId] }),
    );

  const group = useMutation({
    mutationFn: () =>
      apiFetch(`/projects/${projectId}/studies`, {
        method: "POST",
        ...json({ paper_ids: selected, short_name: shortName.trim() || null, notes: notes.trim() || null }),
      }),
    onSuccess: () => {
      setSelected([]);
      setShortName("");
      setNotes("");
      setError(null);
      refresh();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not group the reports."),
  });
  const ungroup = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/projects/${projectId}/studies/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setConfirming(null);
      setError(null);
      refresh();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not remove the grouping."),
  });

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const problem = groupingProblem(selected, shortName);

  return (
    <section id="studies" aria-labelledby="studies-h" className="flex flex-col gap-3">
      <div>
        <h2 id="studies-h" className="section-title mb-1">
          Studies and reports
        </h2>
        <p className="text-xs text-muted">
          PRISMA counts studies and reports separately. Each included report counts as its own study until you
          group reports that describe the same study (for example a trial&rsquo;s early and final results).
          Grouping changes only the &ldquo;studies included&rdquo; count; it never changes a decision.
        </p>
      </div>

      {isLoading && <p className="text-muted">Loading studies…</p>}
      {isError && <p className="text-danger">Could not load the studies.</p>}
      {error && (
        <p className="text-sm text-danger" role="alert">
          {error}
        </p>
      )}

      {data && (
        <>
          <p className="text-sm">
            {data.included_reports} included report{data.included_reports === 1 ? "" : "s"} ·{" "}
            {data.included_studies} stud{data.included_studies === 1 ? "y" : "ies"}
          </p>
          {data.included_reports === 0 && (
            <p className="text-sm text-muted">No reports are included at full text yet.</p>
          )}

          {data.studies.length > 0 && (
            <ul className="flex flex-col gap-2">
              {data.studies.map((s) => (
                <li key={s.id} className="rounded border border-line bg-panel p-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <p className="font-medium">{s.short_name || "Unnamed study"}</p>
                      {s.notes && <p className="text-xs text-muted">{s.notes}</p>}
                    </div>
                    {canWrite &&
                      (confirming === s.id ? (
                        <div className="flex gap-2">
                          <button
                            type="button"
                            className="btn-danger"
                            disabled={ungroup.isPending}
                            onClick={() => ungroup.mutate(s.id)}
                          >
                            Yes, remove grouping
                          </button>
                          <button type="button" className="btn-ghost" onClick={() => setConfirming(null)}>
                            Keep
                          </button>
                        </div>
                      ) : (
                        <button type="button" className="btn-ghost" onClick={() => setConfirming(s.id)}>
                          Remove grouping
                        </button>
                      ))}
                  </div>
                  <ul className="mt-2 flex list-inside list-disc flex-col gap-1 text-sm">
                    {s.reports.map((p) => (
                      <li key={p.id}>
                        <ReportLine p={p} />
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}

          {data.ungrouped_reports.length > 0 && (
            <div className="rounded border border-line bg-panel p-3">
              <h3 className="section-title mb-2">Reports counted as one study each</h3>
              <fieldset disabled={!canWrite} className="flex flex-col gap-1">
                <legend className="sr-only">Select reports of the same study</legend>
                {data.ungrouped_reports.map((p) => (
                  <label key={p.id} className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={selected.includes(p.id)}
                      onChange={() => toggle(p.id)}
                    />
                    <ReportLine p={p} />
                  </label>
                ))}
              </fieldset>
              {canWrite && data.ungrouped_reports.length >= 2 && (
                <div className="mt-3 flex flex-col gap-2">
                  <label className="text-xs text-muted" htmlFor="study-name">
                    Study name (optional, for example &ldquo;Smith 2020 trial&rdquo;)
                  </label>
                  <input
                    id="study-name"
                    className="input"
                    maxLength={200}
                    value={shortName}
                    onChange={(e) => setShortName(e.target.value)}
                  />
                  <label className="text-xs text-muted" htmlFor="study-notes">
                    Why these reports are one study (optional)
                  </label>
                  <textarea
                    id="study-notes"
                    className="input"
                    rows={2}
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                  />
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      className="btn self-start"
                      disabled={group.isPending || problem !== null}
                      onClick={() => group.mutate()}
                    >
                      Group {selected.length || ""} selected as one study
                    </button>
                    {problem && <span className="text-xs text-muted">{problem}</span>}
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
