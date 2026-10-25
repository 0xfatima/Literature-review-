"use client";
import type { ReactNode } from "react";

/** A request for the user's decision in the chat or a panel: context, the editable proposal, and its actions
 *  (confirm / edit are part of `children`); "Ask a question" sends the user to the chat composer. */
export default function ApprovalPrompt({
  label,
  help,
  testId,
  kind,
  onAsk,
  children,
  footer,
}: {
  label: string;
  help?: string | null;
  testId?: string;
  kind?: string;
  onAsk?: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <section
      aria-label={label}
      className="mt-2 rounded border border-mark/50 bg-canvas/40 p-3"
      data-testid={testId}
      data-kind={kind}
    >
      {help && <p className="mb-2 text-xs text-muted">{help}</p>}
      {children}
      {onAsk && (
        <button type="button" className="btn-ghost mt-2 text-xs" onClick={onAsk}>
          Ask a question about this
        </button>
      )}
      {footer}
    </section>
  );
}
