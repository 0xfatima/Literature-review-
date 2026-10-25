"use client";
import type { ReactNode } from "react";

/** Compact bibliographic card used wherever a paper is listed or inspected. */
export default function PaperCard({
  title,
  authors,
  authorCount,
  year,
  venue,
  publicationType,
  badges,
  onInspect,
  headingLevel = 3,
  children,
}: {
  title: string;
  authors?: string[];
  authorCount?: number;
  year?: number | null;
  venue?: string | null;
  publicationType?: string | null;
  badges?: ReactNode;
  onInspect?: () => void;
  headingLevel?: 2 | 3 | 4;
  children?: ReactNode;
}) {
  const H = `h${headingLevel}` as "h2" | "h3" | "h4";
  const shown = (authors ?? []).slice(0, 6);
  const more = (authorCount ?? shown.length) - shown.length;
  return (
    <article className="min-w-0" aria-label={title}>
      <H className="text-ink">{title}</H>
      <p className="text-xs text-muted">
        {shown.join(", ") || "Authors not reported"}
        {more > 0 && ` and ${more} more`}
        {year && ` · ${year}`}
        {venue && ` · ${venue}`}
        {publicationType && ` · ${publicationType}`}
      </p>
      {(badges || onInspect) && (
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
          {badges}
          {onInspect && (
            <button type="button" className="btn-ghost text-xs" onClick={onInspect}>
              Inspect paper
            </button>
          )}
        </div>
      )}
      {children}
    </article>
  );
}
