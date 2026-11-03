"use client";
import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
import { THEME_KEY as KEY } from "@/lib/theme";

/** Light, dark, or follow the operating system (the default). */
export function useTheme() {
  const [theme, setThemeState] = useState<ThemeChoice>("system");
  useEffect(() => {
    try {
      const t = window.localStorage.getItem(KEY);
      if (t === "light" || t === "dark") setThemeState(t);
    } catch {
      /* storage unavailable */
    }
  }, []);
  const setTheme = useCallback((t: ThemeChoice) => {
    setThemeState(t);
    const root = document.documentElement;
    if (t === "system") delete root.dataset.theme;
    else root.dataset.theme = t;
    try {
      if (t === "system") window.localStorage.removeItem(KEY);
      else window.localStorage.setItem(KEY, t);
    } catch {
      /* storage unavailable */
    }
  }, []);
  return [theme, setTheme] as const;
}
