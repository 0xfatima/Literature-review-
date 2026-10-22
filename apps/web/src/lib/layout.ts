/** Panel sizing rules for the IDE shell (pure, unit-tested). */
export interface PanelBounds {
  min: number;
  max: number;
  initial: number;
}

export const SIDEBAR: PanelBounds = { min: 180, max: 420, initial: 248 };
export const CHAT: PanelBounds = { min: 280, max: 720, initial: 400 };
export const KEY_STEP = 16;

export function clamp(n: number, b: PanelBounds): number {
  if (!Number.isFinite(n)) return b.initial;
  return Math.min(b.max, Math.max(b.min, Math.round(n)));
}

/** Parse a stored layout; anything malformed falls back to defaults rather than breaking the page. */
export interface StoredLayout {
  sidebar: number;
  chat: number;
  sidebarOpen: boolean;
  chatOpen: boolean;
}

export const DEFAULT_LAYOUT: StoredLayout = {
  sidebar: SIDEBAR.initial,
  chat: CHAT.initial,
  sidebarOpen: true,
  chatOpen: true,
};

export function parseLayout(raw: string | null): StoredLayout {
  if (!raw) return DEFAULT_LAYOUT;
  try {
    const v = JSON.parse(raw) as Partial<Record<keyof StoredLayout, unknown>>;
    return {
      sidebar: clamp(Number(v.sidebar), SIDEBAR),
      chat: clamp(Number(v.chat), CHAT),
      sidebarOpen: typeof v.sidebarOpen === "boolean" ? v.sidebarOpen : true,
      chatOpen: typeof v.chatOpen === "boolean" ? v.chatOpen : true,
    };
  } catch {
    return DEFAULT_LAYOUT;
  }
}

/** Keyboard resizing for a separator (WAI-ARIA window splitter pattern). Returns null for unhandled keys. */
export function keyResize(
  key: string,
  current: number,
  b: PanelBounds,
  direction: 1 | -1 = 1,
): number | null {
  switch (key) {
    case "ArrowRight":
      return clamp(current + KEY_STEP * direction, b);
    case "ArrowLeft":
      return clamp(current - KEY_STEP * direction, b);
    case "Home":
      return b.min;
    case "End":
      return b.max;
    default:
      return null;
  }
}
