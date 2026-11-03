"use client";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { Toaster } from "@/components/status/Notifications";
import { CHAT, STEPS, type StoredLayout } from "@/lib/layout";
import Splitter from "./Splitter";

/** Research IDE frame:
 *
 *    title bar ── breadcrumb · command search · current step · notifications · account
 *    [ chat | step editor (only while a step is open) ] [ review steps (opt-in) ]
 *    status bar ── save state
 *
 *  The chat is the workspace until the person opens a step; then the step opens beside it. On narrow screens
 *  chat and step share the space through a two-way switch, and the steps panel becomes a drawer. */
export default function IdeShell({
  layout,
  update,
  titleBar,
  chat,
  workspace,
  editorVisible,
  editorKey,
  chatKey,
  editorLabel,
  steps,
  statusBar,
}: {
  layout: StoredLayout;
  update: (patch: Partial<StoredLayout>) => void;
  titleBar: ReactNode;
  chat: ReactNode;
  workspace: ReactNode;
  editorVisible: boolean;
  /** Changes whenever another step is brought to the front (used to switch the mobile view to it). */
  editorKey: string | null;
  /** Changes whenever something asks for the chat (e.g. "Answer in chat"). */
  chatKey: number;
  editorLabel: string;
  steps: ReactNode;
  statusBar: ReactNode;
}) {
  const showChat = !editorVisible || layout.chatOpen;
  const [mobilePane, setMobilePane] = useState<"chat" | "editor">("chat");
  useEffect(() => {
    setMobilePane(editorKey ? "editor" : "chat");
  }, [editorKey]);
  useEffect(() => {
    if (chatKey) setMobilePane("chat");
  }, [chatKey]);

  const stepsRef = useRef<HTMLElement>(null);
  const wasOpen = useRef(layout.stepsOpen);
  useEffect(() => {
    // Move focus into the panel when the person opens it (not when a remembered layout restores it).
    if (layout.stepsOpen && !wasOpen.current) stepsRef.current?.focus({ preventScroll: true });
    wasOpen.current = layout.stepsOpen;
  }, [layout.stepsOpen]);
  const closeSteps = () => update({ stepsOpen: false });

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <a
        href="#composer"
        className="sr-only z-[70] rounded bg-mark px-3 py-1 text-onmark focus:not-sr-only focus:fixed focus:left-2 focus:top-2"
      >
        Skip to the message box
      </a>
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-line bg-panel px-2">
        {titleBar}
      </header>

      {editorVisible && (
        <div
          role="group"
          aria-label="Show"
          className="flex shrink-0 gap-1 border-b border-line bg-panel p-1 md:hidden"
        >
          {(["chat", "editor"] as const).map((p) => (
            <button
              key={p}
              type="button"
              aria-pressed={mobilePane === p}
              className="flex-1 rounded-md px-3 py-1 text-sm text-muted aria-pressed:bg-raised aria-pressed:text-ink"
              onClick={() => setMobilePane(p)}
            >
              {p === "chat" ? "Chat" : editorLabel}
            </button>
          ))}
        </div>
      )}

      <div className="relative flex min-h-0 flex-1">
        <div className="flex min-h-0 min-w-0 flex-1">
          {showChat && (
            <section
              id="ide-chat"
              aria-label="Chat"
              className={`min-h-0 min-w-0 bg-panel ${
                editorVisible
                  ? `${mobilePane === "chat" ? "flex" : "hidden"} w-full md:flex md:w-[var(--w)] md:shrink-0`
                  : "flex flex-1"
              }`}
              style={{ "--w": `${layout.chat}px` } as React.CSSProperties}
            >
              <div className="flex min-h-0 min-w-0 flex-1 flex-col">{chat}</div>
              {/* The splitter belongs to the panel it resizes (and so to its landmark). */}
              {editorVisible && (
                <div className="hidden md:flex">
                  <Splitter
                    label="Resize chat"
                    controls="ide-chat"
                    value={layout.chat}
                    bounds={CHAT}
                    onChange={(v) => update({ chat: v })}
                  />
                </div>
              )}
            </section>
          )}
          <main
            aria-label="Open step"
            hidden={!editorVisible}
            className={`min-h-0 min-w-0 flex-1 bg-canvas ${mobilePane === "editor" ? "" : "max-md:hidden"}`}
          >
            {workspace}
          </main>
        </div>

        {layout.stepsOpen && (
          <>
            {/* Below the large breakpoint the panel floats over the content, with a backdrop to dismiss it. */}
            <button
              type="button"
              tabIndex={-1}
              aria-hidden
              className="fixed inset-0 top-11 z-30 bg-black/40 lg:hidden"
              onClick={closeSteps}
            />
            <aside
              ref={stepsRef}
              id="ide-steps"
              aria-label="Review steps"
              tabIndex={-1}
              className="fixed bottom-0 right-0 top-11 z-40 flex w-[min(22rem,90vw)] border-l border-line bg-panel outline-none lg:static lg:z-auto lg:w-[var(--w)] lg:shrink-0 lg:border-l-0"
              style={{ "--w": `${layout.steps}px` } as React.CSSProperties}
              onKeyDown={(e) => {
                if (e.key === "Escape") closeSteps();
              }}
            >
              <div className="hidden lg:flex">
                <Splitter
                  label="Resize review steps"
                  controls="ide-steps"
                  value={layout.steps}
                  bounds={STEPS}
                  direction={-1}
                  onChange={(v) => update({ steps: v })}
                />
              </div>
              <div className="min-w-0 flex-1">{steps}</div>
            </aside>
          </>
        )}
      </div>

      <footer className="flex h-6 shrink-0 items-center gap-4 border-t border-line bg-panel px-3 text-2xs text-muted">
        {statusBar}
      </footer>
      <Toaster />
    </div>
  );
}
