"use client";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { STAGE_STATUS_LABELS, type StageStatus } from "@/lib/workspace";

export interface StageInfo {
  key: string;
  label: string;
  tab: string;
  status: StageStatus;
  detail: string;
}
export interface WorkspaceOverview {
  stages: StageInfo[];
  current: string;
  active_jobs: number;
  failed_jobs: number;
}

const MARK: Record<StageStatus, string> = { not_started: "○", in_progress: "◐", done: "●", attention: "!" };
const TONE: Record<StageStatus, string> = {
  not_started: "text-muted",
  in_progress: "text-mark",
  done: "text-ok",
  attention: "text-danger",
};

export function useWorkspaceOverview(projectId: string) {
  return useQuery({
    queryKey: ["workspace", projectId, "overview"],
    queryFn: () => apiFetch<WorkspaceOverview>(`/projects/${projectId}/workspace/overview`),
    refetchInterval: (q) => ((q.state.data?.active_jobs ?? 0) > 0 ? 5000 : 30_000),
  });
}

/** Persistent review-stage navigator: every stage's real status (from stored data) and a jump to its panel. */
export default function StageNavigator({
  projectId,
  activeTab,
  onTab,
}: {
  projectId: string;
  activeTab: string;
  onTab: (tab: string) => void;
}) {
  const q = useWorkspaceOverview(projectId);
  if (q.isError)
    return <p className="px-3 py-1 text-xs text-danger">Review stages are unavailable right now.</p>;
  if (!q.data) return <p className="px-3 py-1 text-xs text-muted">Loading review stages…</p>;
  return (
    <nav aria-label="Review stages" className="overflow-x-auto border-b border-line px-3 py-1.5">
      <ol className="flex min-w-max items-center gap-1">
        {q.data.stages.map((s, i) => {
          const current = s.key === q.data.current;
          return (
            <li key={s.key} className="flex items-center">
              <button
                type="button"
                className={`flex items-center gap-1 rounded px-2 py-0.5 text-xs hover:bg-raised ${
                  activeTab === s.tab ? "bg-raised text-ink" : ""
                } ${current ? "font-semibold" : ""}`}
                aria-current={current ? "step" : undefined}
                title={`${STAGE_STATUS_LABELS[s.status]}: ${s.detail}`}
                onClick={() => onTab(s.tab)}
              >
                <span aria-hidden className={TONE[s.status]}>
                  {MARK[s.status]}
                </span>
                <span>{s.label}</span>
                <span className="sr-only">
                  {" "}
                  ({STAGE_STATUS_LABELS[s.status]}: {s.detail})
                </span>
              </button>
              {i < q.data.stages.length - 1 && <span aria-hidden className="mx-0.5 h-px w-3 bg-line" />}
            </li>
          );
        })}
      </ol>
      {(() => {
        const cur = q.data.stages.find((s) => s.key === q.data.current);
        return cur ? (
          <p className="mt-0.5 text-2xs text-muted" data-testid="stage-detail">
            {cur.label}: {STAGE_STATUS_LABELS[cur.status]} — {cur.detail}
          </p>
        ) : null;
      })()}
    </nav>
  );
}
