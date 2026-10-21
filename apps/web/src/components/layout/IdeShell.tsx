import type { ReactNode } from "react";

/** Workspace skeleton (spec §5.1). Panels that have no backend yet are labelled and disabled, never faked. */
export default function IdeShell({ header, statusBar, children }: { header: ReactNode; statusBar: ReactNode; children: ReactNode }) {
  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-slate-200 px-4 py-2 dark:border-slate-800">{header}</header>
      <div className="flex min-h-0 flex-1">
        <aside aria-label="Assistant" className="hidden w-80 shrink-0 flex-col border-r border-slate-200 p-4 md:flex dark:border-slate-800">
          <h2 className="mb-2 text-sm font-semibold">Assistant</h2>
          <p className="mb-3 text-xs text-slate-500">The research assistant arrives in a later phase. Nothing is simulated here.</p>
          <textarea disabled aria-label="Message (not available yet)" className="input mt-auto" rows={3} placeholder="Not available yet" />
        </aside>
        <main className="min-w-0 flex-1 overflow-auto p-6">{children}</main>
      </div>
      <footer className="border-t border-slate-200 px-4 py-1 text-xs text-slate-500 dark:border-slate-800">{statusBar}</footer>
    </div>
  );
}
