"use client";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import Icon from "@/components/ui/Icon";
import { matchesQuery, rankQuery } from "@/lib/steps";

export interface Command {
  id: string;
  label: string;
  group: string;
  /** Extra words that should find this command (not shown). */
  keywords?: string;
  hint?: string;
  run: () => void;
}

/** Modal command palette (WAI-ARIA combobox + listbox). Typing filters; ↑/↓ choose; Enter runs; Escape closes
 *  and returns focus where it was. Focus stays in the input, options are referenced by aria-activedescendant. */
export default function CommandPalette({
  open,
  onClose,
  commands,
}: {
  open: boolean;
  onClose: () => void;
  commands: Command[];
}) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  const listId = useId();

  useEffect(() => {
    if (!open) return;
    returnTo.current = document.activeElement as HTMLElement | null;
    setQuery("");
    setIndex(0);
    requestAnimationFrame(() => input.current?.focus());
  }, [open]);

  const results = useMemo(
    () =>
      commands
        .filter((c) => matchesQuery(`${c.label} ${c.group} ${c.keywords ?? ""}`, query))
        // Stable sort: best label matches first, original order within each rank.
        .map((c, i) => ({ c, i, r: rankQuery(c.label, query) }))
        .sort((a, b) => a.r - b.r || a.i - b.i)
        .map((x) => x.c)
        // Then grouped (groups in order of their best match), so the arrow keys follow what is shown.
        .reduce<Command[][]>((acc, c) => {
          const g = acc.find((x) => x[0]?.group === c.group);
          if (g) g.push(c);
          else acc.push([c]);
          return acc;
        }, [])
        .flat(),
    [commands, query],
  );
  useEffect(() => setIndex(0), [query]);

  const close = () => {
    onClose();
    requestAnimationFrame(() => returnTo.current?.focus());
  };
  const runAt = (i: number) => {
    const c = results[i];
    if (!c) return;
    onClose();
    // Run after the palette is gone, so the command can move focus where it needs to.
    requestAnimationFrame(() => c.run());
  };

  if (!open) return null;
  const groups = [...new Set(results.map((r) => r.group))];
  const optionId = (i: number) => `${listId}-o${i}`;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-start justify-center bg-black/40 px-3 pt-[12vh]"
      onClick={close}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="surface-float w-full max-w-xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-line px-3">
          <Icon name="search" className="text-muted" />
          <input
            ref={input}
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={results.length ? optionId(index) : undefined}
            aria-label="Type a command or the name of a step"
            placeholder="Type a command or a step, e.g. “screening”"
            className="h-11 flex-1 bg-transparent text-sm text-ink placeholder:text-muted focus:outline-none"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") setIndex((i) => Math.min(i + 1, results.length - 1));
              else if (e.key === "ArrowUp") setIndex((i) => Math.max(i - 1, 0));
              else if (e.key === "Home") setIndex(0);
              else if (e.key === "End") setIndex(results.length - 1);
              else if (e.key === "Enter") runAt(index);
              else if (e.key === "Escape") close();
              else if (e.key === "Tab") {
                /* keep focus inside the dialog */
              } else return;
              e.preventDefault();
            }}
          />
          <kbd className="font-sans text-2xs text-muted">Esc</kbd>
        </div>
        <ul id={listId} role="listbox" aria-label="Commands" className="max-h-[50vh] overflow-y-auto py-1">
          {results.length === 0 && (
            <li role="presentation" className="px-3 py-4 text-center text-sm text-muted">
              No command matches “{query}”.
            </li>
          )}
          {groups.map((g) => (
            <li key={g} role="presentation">
              <p role="presentation" className="px-3 pb-0.5 pt-2 text-2xs font-semibold text-muted">
                {g}
              </p>
              <ul role="group" aria-label={g}>
                {results.map((c, i) =>
                  c.group !== g ? null : (
                    <li
                      key={c.id}
                      id={optionId(i)}
                      role="option"
                      aria-selected={i === index}
                      className={`mx-1 flex cursor-pointer items-center justify-between rounded px-2 py-1.5 text-sm ${
                        i === index ? "bg-raised text-ink" : "text-ink"
                      }`}
                      onMouseMove={() => setIndex(i)}
                      onClick={() => runAt(i)}
                    >
                      <span>{c.label}</span>
                      {c.hint && <span className="text-xs text-muted">{c.hint}</span>}
                    </li>
                  ),
                )}
              </ul>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
