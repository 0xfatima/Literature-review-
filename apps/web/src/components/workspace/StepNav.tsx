"use client";
import Icon from "@/components/ui/Icon";
import { stepNav } from "@/lib/stepnav";
import { type ViewId, viewLabel } from "@/lib/steps";
import { useWorkspaceOverview } from "./overview";

/** Back / Next between the main steps, at the foot of every step. Next is green and enabled once the step is
 *  complete (from stored data, never from the UI), and disabled with the reason while it is not. */
export default function StepNav({
  projectId,
  view,
  onGo,
}: {
  projectId: string;
  view: ViewId;
  onGo: (v: ViewId) => void;
}) {
  const overview = useWorkspaceOverview(projectId);
  const s = stepNav(view, overview.data?.stages);
  if (!s.prev && !s.next) return null;
  const done = s.complete;
  return (
    <nav
      aria-label="Step navigation"
      className="sticky bottom-0 -mx-6 mt-8 flex flex-wrap items-center gap-3 border-t border-line bg-canvas/95 px-6 py-3 backdrop-blur"
    >
      {s.prev ? (
        <button type="button" className="btn-secondary" onClick={() => onGo(s.prev as ViewId)}>
          <Icon name="chevronRight" size={14} className="rotate-180" /> Back: {viewLabel(s.prev)}
        </button>
      ) : (
        <span />
      )}
      <p
        className={`mr-auto flex items-center gap-1.5 text-sm ${done ? "font-medium text-ok" : "text-muted"}`}
        role="status"
      >
        {done ? (
          <>
            <Icon name="check" size={16} /> Step complete
          </>
        ) : (
          <>{s.detail ? `In progress: ${s.detail}` : "Finish this step to continue."}</>
        )}
      </p>
      {s.next && (
        <button
          type="button"
          disabled={!done}
          title={done ? `Continue to ${viewLabel(s.next)}` : "Complete this step first"}
          aria-describedby={done ? undefined : `stepnav-why-${view}`}
          className={`inline-flex items-center gap-1.5 rounded-md px-4 py-2 text-sm font-semibold transition-colors ${
            done
              ? "bg-ok text-white shadow-sm hover:bg-ok/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ok"
              : "cursor-not-allowed bg-line text-muted"
          }`}
          onClick={() => onGo(s.next as ViewId)}
        >
          Next: {viewLabel(s.next)} <Icon name="chevronRight" size={14} />
        </button>
      )}
      {!done && s.next && (
        <span id={`stepnav-why-${view}`} className="sr-only">
          Next is disabled until this step is complete.
        </span>
      )}
    </nav>
  );
}
