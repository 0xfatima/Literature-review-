/** Small line-icon set (24px grid, 1.75 stroke, currentColor). Decorative by default: pair every icon-only
 *  button with an aria-label. Drawn for this app so no icon package is needed. */
const PATHS = {
  chevronDown: "M6 9l6 6 6-6",
  chevronRight: "M9 6l6 6-6 6",
  check: "M5 12.5l4.5 4.5L19 7.5",
  plus: "M12 5v14M5 12h14",
  x: "M6 6l12 12M18 6L6 18",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  bell: "M6 16V11a6 6 0 1112 0v5l1.5 2h-15zM10 20.5a2.2 2.2 0 004 0",
  steps: "M4 5h4v4H4zM4 15h4v4H4zM12 7h8M12 17h8M6 9v6",
  panelRight: "M4 5h16v14H4zM14.5 5v14",
  chat: "M5 5h14v10H10l-4 4v-4H5z",
  search: "M11 18a7 7 0 100-14 7 7 0 000 14zM20 20l-4.2-4.2",
  alert: "M12 4l9 16H3zM12 10v4M12 17h.01",
  info: "M12 21a9 9 0 100-18 9 9 0 000 18zM12 11v5M12 8h.01",
  send: "M4 12l16-8-6 16-2.5-6.5z",
  sparkle:
    "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM18.5 16l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z",
  user: "M12 12a4 4 0 100-8 4 4 0 000 8zM4.5 20a7.5 7.5 0 0115 0",
  pencil: "M15 5l4 4L9 19H5v-4z",
  trash: "M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12",
  arrowLeft: "M19 12H5M11 6l-6 6 6 6",
  clock: "M12 21a9 9 0 100-18 9 9 0 000 18zM12 7v5l3 2",
  pause: "M8 5v14M16 5v14",
  play: "M7 5l12 7-12 7z",
  file: "M7 3h7l4 4v14H7zM14 3v4h4",
} as const;

export type IconName = keyof typeof PATHS;

export default function Icon({
  name,
  size = 16,
  className = "",
}: {
  name: IconName;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`shrink-0 ${className}`}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

/** Indeterminate spinner; frozen (a ring) when reduced motion is requested. */
export function Spinner({ size = 14, className = "" }: { size?: number; className?: string }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" className={`spin ${className}`}>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 00-9-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
