"use client";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export type SaveState =
  { kind: "idle" } | { kind: "saving" } | { kind: "saved"; at: Date } | { kind: "failed"; message: string };

interface StatusApi {
  save: SaveState;
  error: string | null;
  setSave: (s: SaveState) => void;
  reportError: (message: string | null) => void;
}

const Ctx = createContext<StatusApi | null>(null);

/** Workspace-wide status the bottom bar shows: persistence state and the latest error. */
export function StatusProvider({ children }: { children: ReactNode }) {
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [error, setError] = useState<string | null>(null);
  const reportError = useCallback((m: string | null) => setError(m), []);
  const value = useMemo(() => ({ save, error, setSave, reportError }), [save, error, reportError]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStatus(): StatusApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("useStatus must be used inside <StatusProvider>");
  return v;
}
