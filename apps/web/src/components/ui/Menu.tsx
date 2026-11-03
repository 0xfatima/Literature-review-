"use client";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import Icon, { type IconName } from "./Icon";

export interface MenuItem {
  label: string;
  onSelect: () => void;
  icon?: IconName;
  danger?: boolean;
  disabled?: boolean;
  /** Shortcut hint shown on the right, e.g. "Ctrl K". */
  hint?: string;
}

/** WAI-ARIA menu button: rare actions (rename, delete, sign out) live here instead of as on-screen buttons.
 *  ↑/↓ move, Home/End jump, Enter/Space choose, Escape closes and returns focus to the button. */
export default function Menu({
  label,
  items,
  trigger,
  triggerClassName = "btn-icon",
  align = "end",
}: {
  label: string;
  items: MenuItem[];
  trigger?: ReactNode;
  triggerClassName?: string;
  align?: "start" | "end";
}) {
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLUListElement>(null);
  const id = useId();

  const enabled = () =>
    Array.from(menu.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]:not([disabled])") ?? []);
  // Focus returns to the menu button at once, before the chosen action runs, so an action that moves focus
  // (an inline form, a confirmation) keeps it.
  const close = (focus = true) => {
    setOpen(false);
    if (focus) btn.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => enabled()[0]?.focus());
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!menu.current?.contains(t) && !btn.current?.contains(t)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  return (
    <div className="relative">
      <button
        ref={btn}
        type="button"
        className={triggerClassName}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        {trigger ?? <Icon name="more" size={18} />}
      </button>
      {open && (
        <ul
          ref={menu}
          id={id}
          role="menu"
          aria-label={label}
          className={`surface-float absolute top-full z-50 mt-1 min-w-48 py-1 ${align === "end" ? "right-0" : "left-0"}`}
          onKeyDown={(e) => {
            const list = enabled();
            const i = list.indexOf(document.activeElement as HTMLButtonElement);
            const go = (n: number) => list[(n + list.length) % list.length]?.focus();
            if (e.key === "ArrowDown") go(i + 1);
            else if (e.key === "ArrowUp") go(i - 1);
            else if (e.key === "Home") go(0);
            else if (e.key === "End") go(list.length - 1);
            else if (e.key === "Escape") close();
            else if (e.key === "Tab") close(false);
            else return;
            e.preventDefault();
          }}
        >
          {items.map((it) => (
            <li key={it.label} role="none">
              <button
                type="button"
                role="menuitem"
                tabIndex={-1}
                disabled={it.disabled}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-raised focus:bg-raised focus:outline-none disabled:opacity-50 ${
                  it.danger ? "text-danger" : "text-ink"
                }`}
                onClick={() => {
                  close();
                  it.onSelect();
                }}
              >
                {it.icon && <Icon name={it.icon} className="text-muted" />}
                <span className="flex-1">{it.label}</span>
                {it.hint && <kbd className="font-sans text-2xs text-muted">{it.hint}</kbd>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
