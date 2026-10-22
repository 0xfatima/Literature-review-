"use client";
import type { ReactNode } from "react";
import { CHAT, SIDEBAR } from "@/lib/layout";
import Splitter from "./Splitter";
import { useLayout } from "./useLayout";

/** Research IDE frame: top bar / [sidebar | chat | workspace] / status bar.
 *  Sidebar and chat are resizable and collapsible; sizes persist per browser. On narrow screens the
 *  three regions stack vertically instead. */
export default function IdeShell({
  topBar,
  sidebar,
  chat,
  workspace,
  statusBar,
}: {
  topBar: ReactNode;
  sidebar: ReactNode;
  chat: ReactNode;
  workspace: ReactNode;
  statusBar: ReactNode;
}) {
  const [layout, update] = useLayout();

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-line bg-panel px-2">
        <button
          type="button"
          className="btn-ghost"
          aria-pressed={layout.sidebarOpen}
          aria-controls="ide-sidebar"
          onClick={() => update({ sidebarOpen: !layout.sidebarOpen })}
        >
          {layout.sidebarOpen ? "Hide sidebar" : "Show sidebar"}
        </button>
        <div className="flex min-w-0 flex-1 items-center justify-between gap-3">{topBar}</div>
        <button
          type="button"
          className="btn-ghost"
          aria-pressed={layout.chatOpen}
          aria-controls="ide-chat"
          onClick={() => update({ chatOpen: !layout.chatOpen })}
        >
          {layout.chatOpen ? "Hide chat" : "Show chat"}
        </button>
      </header>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        {layout.sidebarOpen && (
          <>
            <nav
              id="ide-sidebar"
              aria-label="Project explorer"
              className="max-h-[40vh] shrink-0 overflow-y-auto border-b border-line bg-panel md:max-h-none md:w-[var(--w)] md:border-b-0"
              style={{ "--w": `${layout.sidebar}px` } as React.CSSProperties}
            >
              {sidebar}
            </nav>
            <div className="hidden md:flex">
              <Splitter
                label="Resize sidebar"
                controls="ide-sidebar"
                value={layout.sidebar}
                bounds={SIDEBAR}
                onChange={(v) => update({ sidebar: v })}
              />
            </div>
          </>
        )}
        {layout.chatOpen && (
          <>
            <section
              id="ide-chat"
              aria-label="Chat"
              className="flex min-h-[50vh] shrink-0 flex-col border-b border-line bg-panel md:min-h-0 md:w-[var(--w)] md:border-b-0"
              style={{ "--w": `${layout.chat}px` } as React.CSSProperties}
            >
              {chat}
            </section>
            <div className="hidden md:flex">
              <Splitter
                label="Resize chat"
                controls="ide-chat"
                value={layout.chat}
                bounds={CHAT}
                onChange={(v) => update({ chat: v })}
              />
            </div>
          </>
        )}
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">{workspace}</main>
      </div>

      <footer className="flex min-h-7 shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-t border-line bg-panel px-3 py-1 text-xs text-muted">
        {statusBar}
      </footer>
    </div>
  );
}
