"use client";
import { useState } from "react";
import { DECISION_LABELS } from "@/lib/screening";
import type { HumanDecision, ScreeningReason } from "@/lib/types";
import Icon from "@/components/ui/Icon";
import { decisionButton } from "@/components/common/decisionStyle";

export interface DecisionInput {
  decision: HumanDecision;
  reason_code?: string | null;
  note?: string | null;
}

export function ExcludeForm({
  reasons,
  requireReason,
  onSubmit,
  onCancel,
  busy,
}: {
  reasons: ScreeningReason[];
  requireReason: boolean;
  onSubmit: (reason: string | null, note: string | null) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const needsNote = reason === "other" && !note.trim();
  const blocked = (requireReason && !reason) || needsNote;
  return (
    <div className="mt-2 flex flex-col gap-2 rounded border border-line p-3">
      <label className="flex flex-col gap-1 text-xs text-muted">
        Exclusion reason {requireReason ? "(required)" : "(recommended)"}
        <select className="input" value={reason} onChange={(e) => setReason(e.target.value)} autoFocus>
          <option value="">{requireReason ? "Choose a reason" : "No reason given"}</option>
          {reasons.map((r) => (
            <option key={r.code} value={r.code}>
              {r.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted">
        Note {reason === "other" ? "(required for “other”)" : "(optional)"}
        <textarea
          className="input min-h-[3rem]"
          value={note}
          maxLength={2000}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <div className="flex gap-2">
        <button
          type="button"
          className="btn-reject"
          disabled={busy || blocked}
          onClick={() => onSubmit(reason || null, note.trim() || null)}
        >
          Exclude
        </button>
        <button type="button" className="btn-ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/** Include / Uncertain / Exclude… with a reason form. Keyboard shortcuts (I, U, E) are handled by the container. */
export default function DecisionButtons({
  decision,
  reasons,
  requireReason,
  busy,
  excluding,
  setExcluding,
  onDecide,
  children,
}: {
  decision: HumanDecision | null;
  reasons: ScreeningReason[];
  requireReason: boolean;
  busy: boolean;
  excluding: boolean;
  setExcluding: (v: boolean) => void;
  onDecide: (d: DecisionInput) => void;
  children?: React.ReactNode;
}) {
  return (
    <>
      <div className="mt-3 flex flex-wrap items-center gap-2" role="group" aria-label="Screening decision">
        {children}
        {(["include", "uncertain"] as const).map((d) => (
          <button
            key={d}
            type="button"
            className={decisionButton(d, decision === d)}
            aria-pressed={decision === d}
            aria-keyshortcuts={d === "include" ? "I" : "U"}
            disabled={busy}
            onClick={() => onDecide({ decision: d })}
          >
            {decision === d && <Icon name="check" size={14} />}
            {DECISION_LABELS[d]}
          </button>
        ))}
        <button
          type="button"
          className={decisionButton("exclude", decision === "exclude")}
          aria-pressed={decision === "exclude"}
          aria-keyshortcuts="E"
          disabled={busy}
          onClick={() => setExcluding(true)}
        >
          {decision === "exclude" && <Icon name="check" size={14} />}
          {DECISION_LABELS.exclude}…
        </button>
      </div>
      {excluding && (
        <ExcludeForm
          reasons={reasons}
          requireReason={requireReason}
          busy={busy}
          onCancel={() => setExcluding(false)}
          onSubmit={(reason_code, note) => onDecide({ decision: "exclude", reason_code, note })}
        />
      )}
    </>
  );
}
