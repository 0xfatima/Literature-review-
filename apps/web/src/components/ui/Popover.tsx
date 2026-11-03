"use client";
import { type ReactNode, type RefObject, useCallback, useEffect, useId, useRef, useState } from "react";

/** Disclosure popover: a trigger button and a floating panel. Escape or a click outside closes it and focus
 *  returns to the trigger, so keyboard users never lose their place. The panel is a labelled dialog. */
export function usePopover() {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const id = useId();
  const close = useCallback((restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (panelRef.current?.contains(t) || triggerRef.current?.contains(t)) return;
      close(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close();
      }
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    // Move focus into the panel: its first focusable element, else the panel itself.
    requestAnimationFrame(() => {
      const panel = panelRef.current;
      const first = panel?.querySelector<HTMLElement>(
        "[data-autofocus], button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex='-1'])",
      );
      (first ?? panel)?.focus();
    });
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  return { open, setOpen, close, triggerRef, panelRef, id };
}

export function PopoverPanel({
  pop,
  label,
  align = "end",
  className = "",
  children,
}: {
  pop: { open: boolean; panelRef: RefObject<HTMLDivElement | null>; id: string };
  label: string;
  align?: "start" | "end";
  className?: string;
  children: ReactNode;
}) {
  if (!pop.open) return null;
  return (
    <div
      ref={pop.panelRef}
      id={pop.id}
      role="dialog"
      aria-label={label}
      tabIndex={-1}
      className={`surface-float absolute top-full z-50 mt-1 outline-none ${
        align === "end" ? "right-0" : "left-0"
      } ${className}`}
    >
      {children}
    </div>
  );
}
