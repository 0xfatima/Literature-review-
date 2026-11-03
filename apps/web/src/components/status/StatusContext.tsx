"use client";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

export type SaveState =
  { kind: "idle" } | { kind: "saving" } | { kind: "saved"; at: Date } | { kind: "failed"; message: string };

export type NoticeTone = "info" | "success" | "error" | "action";

export interface Notice {
  id: string;
  tone: NoticeTone;
  title: string;
  body?: string;
  at: Date;
  read: boolean;
  /** Shown as a pop-up toast right now (not yet dismissed or timed out). */
  toast: boolean;
  action?: { label: string; run: () => void };
}

export type NoticeInput = Omit<Notice, "id" | "at" | "read" | "toast"> & {
  /** Stable key: a second notice with the same key replaces the first instead of stacking. */
  key?: string;
};

interface StatusApi {
  save: SaveState;
  /** Latest error message, kept for components that show it inline. Errors also become notifications. */
  error: string | null;
  setSave: (s: SaveState) => void;
  reportError: (message: string | null) => void;
  notices: Notice[];
  unread: number;
  notify: (n: NoticeInput) => void;
  dismissToast: (id: string) => void;
  markAllRead: () => void;
  clearNotices: () => void;
}

const Ctx = createContext<StatusApi | null>(null);
const MAX = 40;

/** Workspace-wide status: persistence state, errors, and the notification list behind the bell and toasts. */
export function StatusProvider({ children }: { children: ReactNode }) {
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [error, setError] = useState<string | null>(null);
  const [notices, setNotices] = useState<Notice[]>([]);
  const seq = useRef(0);

  const notify = useCallback((n: NoticeInput) => {
    const { key, ...rest } = n;
    const id = key ?? `n${++seq.current}`;
    setNotices((list) =>
      [{ ...rest, id, at: new Date(), read: false, toast: true }, ...list.filter((x) => x.id !== id)].slice(
        0,
        MAX,
      ),
    );
  }, []);

  const reportError = useCallback(
    (m: string | null) => {
      setError(m);
      if (m) notify({ key: `error:${m}`, tone: "error", title: m });
    },
    [notify],
  );
  const dismissToast = useCallback(
    (id: string) => setNotices((l) => l.map((n) => (n.id === id ? { ...n, toast: false } : n))),
    [],
  );
  const markAllRead = useCallback(
    () => setNotices((l) => l.map((n) => ({ ...n, read: true, toast: false }))),
    [],
  );
  const clearNotices = useCallback(() => {
    setNotices([]);
    setError(null);
  }, []);

  const unread = notices.filter((n) => !n.read).length;
  const value = useMemo(
    () => ({
      save,
      error,
      setSave,
      reportError,
      notices,
      unread,
      notify,
      dismissToast,
      markAllRead,
      clearNotices,
    }),
    [save, error, reportError, notices, unread, notify, dismissToast, markAllRead, clearNotices],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStatus(): StatusApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("useStatus must be used inside <StatusProvider>");
  return v;
}
