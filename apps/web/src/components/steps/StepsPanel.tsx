"use client";
import { useEffect, useState } from "react";
import { useActivity } from "@/components/status/Activity";
import Icon from "@/components/ui/Icon";
import type { StageInfo } from "@/components/workspace/overview";
import {
  REVIEW_STEPS,
  STEP_STATE_LABELS,
  type StepId,
  type StepState,
  VIEWS,
  type ViewId,
  stepStates,
} from "@/lib/steps";
import { STAGE_STATUS_LABELS, type StageStatus } from "@/lib/workspace";

const DOT: Record<StageStatus, string> = {
  not_started: "border border-muted",
  in_progress: "bg-mark",
  done: "bg-ok",
  attention: "bg-danger",
};

/** Step marker on the rail. Shape and icon carry the state, colour only reinforces it. */
function Marker({ state, n }: { state: StepState; n: number }) {
  const base =
    "relative z-10 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-2xs font-semibold";
  if (state === "done")
    return (
      <span aria-hidden className={`${base} bg-ok text-canvas`}>
        <Icon name="check" size={14} />
      </span>
    );
  if (state === "current")
    return (
      <span aria-hidden className={`${base} border-2 border-mark bg-canvas text-mark`}>
        {n}
      </span>
    );
  if (state === "attention")
    return (
      <span aria-hidden className={`${base} border-2 border-danger bg-canvas text-danger`}>
        !
      </span>
    );
  return (
    <span aria-hidden className={`${base} border border-line bg-canvas text-muted`}>
      {n}
    </span>
  );
}

/** Right-hand panel: the eight main steps as a vertical list. Each opens to show the views that belong to it;
 *  choosing one opens it next to the chat, where it can be checked or edited. */
export default function StepsPanel({
  activeView,
  onOpenView,
  onClose,
}: {
  activeView: ViewId | null;
  onOpenView: (v: ViewId) => void;
  onClose: () => void;
}) {
  const a = useActivity();
  const states = stepStates(a.project.status, a.attentionView);
  const currentId = REVIEW_STEPS[states.findIndex((s) => s === "current" || s === "attention")]?.id;
  const activeStep = REVIEW_STEPS.find((s) => activeView && s.views.includes(activeView))?.id;
  const [expanded, setExpanded] = useState<Set<StepId>>(
    () => new Set([currentId, activeStep].filter(Boolean) as StepId[]),
  );
  // Keep the step of whatever is open in the editor expanded, so its row is visible.
  useEffect(() => {
    if (activeStep) setExpanded((e) => (e.has(activeStep) ? e : new Set([...e, activeStep])));
  }, [activeStep]);
  const info = (view: ViewId): StageInfo | undefined => a.overview?.stages.find((s) => s.tab === view);
  const toggle = (id: StepId) =>
    setExpanded((e) => {
      const n = new Set(e);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-10 shrink-0 items-center justify-between border-b border-line pl-3 pr-1">
        <h2 className="text-sm font-semibold">Review steps</h2>
        <div className="flex items-center">
          <button
            type="button"
            className="btn-ghost text-xs"
            onClick={() => setExpanded((e) => (e.size ? new Set() : new Set(REVIEW_STEPS.map((s) => s.id))))}
          >
            {expanded.size ? "Collapse all" : "Expand all"}
          </button>
          <button type="button" className="btn-icon" aria-label="Close review steps" onClick={onClose}>
            <Icon name="x" />
          </button>
        </div>
      </div>

      <ol aria-label="Review steps" className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
        {REVIEW_STEPS.map((step, i) => {
          const state = states[i]!;
          const open = expanded.has(step.id);
          const last = i === REVIEW_STEPS.length - 1;
          return (
            <li
              key={step.id}
              className="relative"
              aria-current={state === "current" || state === "attention" ? "step" : undefined}
            >
              {/* Rail between markers: solid once the step is done. */}
              {!last && (
                <span
                  aria-hidden
                  className={`absolute bottom-0 left-5 top-8 w-px ${
                    state === "done" ? "bg-ok/60" : "bg-line"
                  }`}
                />
              )}
              <h3>
                <button
                  type="button"
                  id={`step-${step.id}`}
                  aria-expanded={open}
                  aria-controls={`step-${step.id}-views`}
                  className={`flex w-full items-start gap-3 rounded-md px-2 py-1.5 text-left hover:bg-raised ${
                    state === "current" ? "bg-mark/10" : state === "attention" ? "bg-danger/10" : ""
                  }`}
                  onClick={() => toggle(step.id)}
                >
                  <Marker state={state} n={i + 1} />
                  <span className="min-w-0 flex-1">
                    <span
                      className={`block text-sm ${
                        state === "todo" ? "text-muted" : "font-semibold text-ink"
                      }`}
                    >
                      {step.label}
                    </span>
                    <span className="block text-xs text-muted">
                      {state === "current" || state === "attention"
                        ? STEP_STATE_LABELS[state]
                        : state === "done"
                          ? "Done"
                          : step.summary}
                    </span>
                  </span>
                  <Icon
                    name={open ? "chevronDown" : "chevronRight"}
                    size={14}
                    className="mt-1.5 text-muted"
                  />
                </button>
              </h3>
              <div
                id={`step-${step.id}-views`}
                role="group"
                aria-labelledby={`step-${step.id}`}
                hidden={!open}
                className="pb-2 pl-10 pr-1"
              >
                {(state === "current" || state === "attention") && (
                  <p className="mb-1 px-2 text-xs text-muted">{step.summary}</p>
                )}
                {step.id === "setup" && (
                  <p className="mb-1 line-clamp-3 px-2 text-xs text-muted" title={a.project.topic_original}>
                    Topic: {a.project.topic_original}
                  </p>
                )}
                <ul className="flex flex-col gap-0.5">
                  {step.views.map((v) => {
                    const meta = VIEWS.find((x) => x.id === v)!;
                    const st = info(v);
                    const isActive = activeView === v;
                    return (
                      <li key={v}>
                        <button
                          type="button"
                          aria-current={isActive ? "page" : undefined}
                          className={`group flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left hover:bg-raised ${
                            isActive ? "bg-raised shadow-[inset_2px_0_0_rgb(var(--mark))]" : ""
                          }`}
                          onClick={() => onOpenView(v)}
                        >
                          <span
                            aria-hidden
                            className={`mt-[0.4rem] h-2 w-2 shrink-0 rounded-full ${
                              st ? DOT[st.status] : "border border-line"
                            }`}
                          />
                          <span className="min-w-0 flex-1">
                            <span className="block text-sm text-ink">{meta.label}</span>
                            <span className="block text-xs text-muted">
                              {st ? (
                                <>
                                  <span className="sr-only">{STAGE_STATUS_LABELS[st.status]}: </span>
                                  {st.detail}
                                </>
                              ) : (
                                meta.hint
                              )}
                            </span>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            </li>
          );
        })}
      </ol>
      <p className="shrink-0 border-t border-line px-3 py-2 text-2xs text-muted">
        Steps are set up in the chat. Open one here to check or change it later.
      </p>
    </div>
  );
}
