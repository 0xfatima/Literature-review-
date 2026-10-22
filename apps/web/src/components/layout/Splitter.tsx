"use client";
import { useRef } from "react";
import { type PanelBounds, keyResize } from "@/lib/layout";

/** Vertical splitter (WAI-ARIA "window splitter"): drag with pointer, or focus and use ←/→, Home, End.
 *  Double-click restores the default width. */
export default function Splitter({
  label,
  controls,
  value,
  bounds,
  onChange,
}: {
  label: string;
  controls: string;
  value: number;
  bounds: PanelBounds;
  onChange: (next: number) => void;
}) {
  const start = useRef<{ x: number; size: number } | null>(null);

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-controls={controls}
      aria-valuemin={bounds.min}
      aria-valuemax={bounds.max}
      aria-valuenow={value}
      tabIndex={0}
      className="group relative w-1.5 shrink-0 cursor-col-resize touch-none bg-line/40 outline-none hover:bg-mark/50 focus-visible:bg-mark"
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        start.current = { x: e.clientX, size: value };
      }}
      onPointerMove={(e) => {
        if (!start.current) return;
        onChange(start.current.size + (e.clientX - start.current.x));
      }}
      onPointerUp={(e) => {
        e.currentTarget.releasePointerCapture(e.pointerId);
        start.current = null;
      }}
      onDoubleClick={() => onChange(bounds.initial)}
      onKeyDown={(e) => {
        const next = keyResize(e.key, value, bounds);
        if (next !== null) {
          e.preventDefault();
          onChange(next);
        }
      }}
    />
  );
}
