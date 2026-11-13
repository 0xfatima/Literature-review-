/** "Open access" / "Not open access" next to a paper, from what the search providers reported (an arXiv or PubMed
 *  Central copy counts as open). Text is always shown, so colour is never the only signal. */
export default function OpenAccessTag({ open, url }: { open: boolean | undefined; url?: string | null }) {
  if (open === undefined) return null;
  return open ? (
    <span className="inline-flex items-center gap-1 rounded-full border border-ok/50 bg-ok/10 px-2 py-0.5 text-xs font-medium text-ok">
      Open access
      {url && (
        <a href={url} target="_blank" rel="noopener noreferrer" className="underline">
          PDF
        </a>
      )}
    </span>
  ) : (
    <span className="inline-flex items-center rounded-full border border-line px-2 py-0.5 text-xs text-muted">
      Not open access
    </span>
  );
}
