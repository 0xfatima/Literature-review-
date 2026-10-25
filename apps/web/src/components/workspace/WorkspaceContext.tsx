"use client";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export interface WorkspaceNav {
  /** The paper open in the inspector (kept in the URL, so it survives tab switches and reloads). */
  paperId: string | null;
  openPaper: (id: string | null, tab?: InspectorTab) => void;
  inspectorTab: InspectorTab;
  setInspectorTab: (t: InspectorTab) => void;
  openTab: (tab: string) => void;
  /** The chat thread in view, so contextual actions can post their result there. */
  conversationId: string | null;
  /** Put text into the chat composer and focus it (e.g. "Ask a question" on an approval prompt). */
  composeInChat: (text: string) => void;
  registerComposer: (fn: ((text: string) => void) | null) => void;
}

export type InspectorTab = "overview" | "abstract" | "fulltext" | "evidence" | "screening";

const Ctx = createContext<WorkspaceNav | null>(null);

export function WorkspaceProvider({
  paperId,
  inspectorTab,
  onPaper,
  onTab,
  conversationId,
  children,
}: {
  paperId: string | null;
  inspectorTab: InspectorTab;
  onPaper: (id: string | null, tab: InspectorTab) => void;
  onTab: (tab: string) => void;
  conversationId: string | null;
  children: ReactNode;
}) {
  const composer = useRef<((text: string) => void) | null>(null);
  const openPaper = useCallback(
    (id: string | null, tab?: InspectorTab) =>
      onPaper(id, tab ?? (id === paperId ? inspectorTab : "overview")),
    [onPaper, paperId, inspectorTab],
  );
  const setInspectorTab = useCallback((t: InspectorTab) => onPaper(paperId, t), [onPaper, paperId]);
  const composeInChat = useCallback((text: string) => composer.current?.(text), []);
  const registerComposer = useCallback((fn: ((text: string) => void) | null) => {
    composer.current = fn;
  }, []);
  const value = useMemo(
    () => ({
      paperId,
      openPaper,
      inspectorTab,
      setInspectorTab,
      openTab: onTab,
      conversationId,
      composeInChat,
      registerComposer,
    }),
    [
      paperId,
      openPaper,
      inspectorTab,
      setInspectorTab,
      onTab,
      conversationId,
      composeInChat,
      registerComposer,
    ],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** Null outside a project workspace (components then simply hide their cross-panel links). */
export function useWorkspace(): WorkspaceNav | null {
  return useContext(Ctx);
}

/* ---------- unsaved edits ---------- */

const Dirty = createContext<{ set: (key: string, dirty: boolean) => void; any: boolean } | null>(null);

/** Tracks unsaved edits across panels and warns before the page is closed or reloaded while any exist. */
export function UnsavedChangesProvider({ children }: { children: ReactNode }) {
  const [keys, setKeys] = useState<Set<string>>(() => new Set());
  const set = useCallback((key: string, dirty: boolean) => {
    setKeys((prev) => {
      if (prev.has(key) === dirty) return prev;
      const next = new Set(prev);
      if (dirty) next.add(key);
      else next.delete(key);
      return next;
    });
  }, []);
  const anyDirty = keys.size > 0;
  useEffect(() => {
    if (!anyDirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [anyDirty]);
  const value = useMemo(() => ({ set, any: anyDirty }), [set, anyDirty]);
  return <Dirty.Provider value={value}>{children}</Dirty.Provider>;
}

/** Declare that a component holds unsaved edits. Cleared automatically when the component unmounts. */
export function useUnsavedChanges(key: string, dirty: boolean): void {
  const ctx = useContext(Dirty);
  const set = ctx?.set;
  useEffect(() => {
    set?.(key, dirty);
  }, [set, key, dirty]);
  useEffect(() => () => set?.(key, false), [set, key]);
}

export function useHasUnsavedChanges(): boolean {
  return useContext(Dirty)?.any ?? false;
}
