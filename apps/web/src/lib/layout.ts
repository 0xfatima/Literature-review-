/** Panel sizing rules for the IDE shell (pure, unit-tested). */
export interface PanelBounds {
  min: number;
  max: number;
  initial: number;
}

/** Chat column width when a step is open beside it. With no step open the chat fills the window. */
export const CHAT: PanelBounds = { min: 300, max: 760, initial: 420 };
/** Right-hand "Review steps" panel. */
export const STEPS: PanelBounds = { min: 240, max: 440, initial: 300 };
export const KEY_STEP = 16;

export function clamp(n: number, b: PanelBounds): number {
  if (!Number.isFinite(n)) return b.initial;
  return Math.min(b.max, Math.max(b.min, Math.round(n)));
}

/** Parse a stored layout; anything malformed falls back to defaults rather than breaking the page. */
export interface StoredLayout {
  chat: number;
  steps: number;
  /** Chat beside an open step. Ignored while no step is open: the chat is then the whole workspace. */
  chatOpen: boolean;
  /** The steps panel is opt-in: closed until the person opens it. */
  stepsOpen: boolean;
}

export const DEFAULT_LAYOUT: StoredLayout = {
  chat: CHAT.initial,
  steps: STEPS.initial,
  chatOpen: true,
  stepsOpen: false,
};

export function parseLayout(raw: string | null): StoredLayout {
  if (!raw) return DEFAULT_LAYOUT;
  try {
    const v = JSON.parse(raw) as Partial<Record<keyof StoredLayout, unknown>>;
    return {
      chat: clamp(Number(v.chat), CHAT),
      steps: clamp(Number(v.steps), STEPS),
      chatOpen: typeof v.chatOpen === "boolean" ? v.chatOpen : DEFAULT_LAYOUT.chatOpen,
      stepsOpen: typeof v.stepsOpen === "boolean" ? v.stepsOpen : DEFAULT_LAYOUT.stepsOpen,
    };
  } catch {
    return DEFAULT_LAYOUT;
  }
}

/** Keyboard resizing for a separator (WAI-ARIA window splitter pattern). Returns null for unhandled keys.
 *  `direction` is -1 for a panel on the right edge, where moving the splitter left makes the panel wider. */
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
