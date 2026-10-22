"use client";
import { useCallback, useEffect, useState } from "react";
import { CHAT, DEFAULT_LAYOUT, SIDEBAR, type StoredLayout, clamp, parseLayout } from "@/lib/layout";

const KEY = "srs.layout.v1";

/** Panel sizes and visibility, remembered per browser. Storage failures (private mode, quota) are ignored:
 *  the layout then simply resets on reload, which is harmless. */
export function useLayout() {
  const [layout, setLayout] = useState<StoredLayout>(DEFAULT_LAYOUT);

  useEffect(() => {
    try {
      setLayout(parseLayout(window.localStorage.getItem(KEY)));
    } catch {
      /* storage unavailable */
    }
  }, []);

  const update = useCallback((patch: Partial<StoredLayout>) => {
    setLayout((prev) => {
      const next = {
        ...prev,
        ...patch,
        sidebar: clamp(patch.sidebar ?? prev.sidebar, SIDEBAR),
        chat: clamp(patch.chat ?? prev.chat, CHAT),
      };
      try {
        window.localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable */
      }
      return next;
    });
  }, []);

  return [layout, update] as const;
}
