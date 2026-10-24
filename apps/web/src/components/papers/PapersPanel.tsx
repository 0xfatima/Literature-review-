"use client";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "@/lib/api";
import { MATCH_BASIS_LABELS, doiUrl, fmtDate } from "@/lib/search";
import type { AccessStatus, PaperBrief, PaperDetail, PaperList } from "@/lib/types";
import { MATCH_LABELS, PARSE_LABELS, coverageText } from "@/lib/documents";
import { useDocumentSummary, useRefreshDocuments } from "@/components/documents/DocumentsPanel";
import { UploadQueue, useUploader } from "@/components/documents/Uploader";

const PAGE = 25;
const ACCESS_FILTERS: { value: "" | AccessStatus; label: string }[] = [
  { value: "", label: "Any access" },
  { value: "open_pdf_link", label: "Open PDF link reported" },
  { value: "open_landing_page", label: "Open landing page only" },
  { value: "metadata_only", label: "Metadata only" },
];
const PROVIDERS = [
  { value: "", label: "Any source" },
  { value: "openalex", label: "OpenAlex" },
  { value: "europepmc", label: "Europe PMC" },
  { value: "arxiv", label: "arXiv" },
  { value: "crossref", label: "Crossref" },
];

function AccessBadge({ p }: { p: PaperBrief }) {
  const tone =
    p.access_status === "open_pdf_link"
      ? "text-ok"
      : p.access_status === "open_landing_page"
        ? "text-link"
        : "text-muted";
  const short =
    p.access_status === "open_pdf_link"
      ? "Open PDF link (reported)"
      : p.access_status === "open_landing_page"
        ? "Open access (landing page)"
        : "Metadata only";
  return (
    <span className={`text-xs ${tone}`} title={p.access_label}>
      {short}
    </span>
  );
}

function Identifiers({ p }: { p: PaperBrief }) {
  return (
    <span className="flex flex-wrap gap-x-3 text-xs text-muted">
      {p.doi && (
        <a href={doiUrl(p.doi)} target="_blank" rel="noreferrer noopener">
          DOI {p.doi}
        </a>
      )}
      {p.pmid && (
        <a href={`https://pubmed.ncbi.nlm.nih.gov/${p.pmid}/`} target="_blank" rel="noreferrer noopener">
          PMID {p.pmid}
        </a>
      )}
      {p.arxiv_id && (
        <a href={`https://arxiv.org/abs/${p.arxiv_id}`} target="_blank" rel="noreferrer noopener">
          arXiv {p.arxiv_id}
        </a>
      )}
      {p.openalex_id && <span>OpenAlex {p.openalex_id}</span>}
    </span>
  );
}

function PaperFullText({ projectId, paper }: { projectId: string; paper: PaperDetail }) {
  const refresh = useRefreshDocuments(projectId);
  const summary = useDocumentSummary(projectId);
  const uploader = useUploader(
    projectId,
    paper.id,
    summary.data?.limits.max_bytes ?? 50 * 1024 * 1024,
    refresh,
  );
  return (
    <div data-testid="paper-fulltext">
      <h4 className="section-title mb-1">Full text</h4>
      {paper.documents.length === 0 ? (
        <p className="max-w-prose text-xs text-muted">
          No PDF has been uploaded for this paper, so it can only be judged on its title and abstract.
        </p>
      ) : (
        <ul className="mb-2 flex flex-col gap-1">
          {paper.documents.map((d) => (
            <li key={d.id} className="text-xs">
              <span className="font-medium">{d.original_filename}</span> · {PARSE_LABELS[d.parse_status]} ·{" "}
              {MATCH_LABELS[d.match_status]}
              {coverageText(d) && ` · ${coverageText(d)}`}
              {d.parse_status === "failed" && d.error_message && (
                <span className="block text-danger">{d.error_message}</span>
              )}
            </li>
          ))}
        </ul>
      )}
      <label className="btn-secondary cursor-pointer focus-within:outline focus-within:outline-2 focus-within:outline-mark">
        Upload a PDF for this paper
        <input
          type="file"
          accept=".pdf,application/pdf"
          className="sr-only"
          data-testid="paper-file-input"
          onChange={(e) => {
            if (e.target.files?.length) uploader.add(e.target.files);
            e.target.value = "";
          }}
        />
      </label>
      <p className="mt-1 max-w-prose text-2xs text-muted">
        Only upload a copy you are authorised to use. The file is read in the background; manage it in the
        Files tab.
      </p>
      <div className="mt-2">
        <UploadQueue items={uploader.items} onCancel={uploader.cancel} onDismiss={uploader.dismiss} />
      </div>
    </div>
  );
}

function Detail({ projectId, paperId }: { projectId: string; paperId: string }) {
  const q = useQuery({
    queryKey: ["papers", projectId, "detail", paperId],
    queryFn: () => apiFetch<PaperDetail>(`/projects/${projectId}/papers/${paperId}`),
  });
  const [rawOpen, setRawOpen] = useState<string | null>(null);
  if (q.isPending) return <p className="text-xs text-muted">Loading…</p>;
  if (q.isError || !q.data) return <p className="text-xs text-danger">Could not load this paper.</p>;
  const d = q.data;
  return (
    <div className="mt-3 flex flex-col gap-3 text-sm">
      <p className="text-xs text-muted">{d.full_text_label}.</p>
      {d.access_status === "metadata_only" ? (
        <p className="max-w-prose rounded border border-line px-3 py-2 text-xs">
          No open full text was reported for this record. To review it, obtain a copy you are authorised to
          use (library, author, publisher) and upload it below.
        </p>
      ) : (
        <p className="text-xs text-muted">
          {d.pdf_url && (
            <a href={d.pdf_url} target="_blank" rel="noreferrer noopener">
              Open-access PDF link reported by the provider
            </a>
          )}
          {d.pdf_url && d.oa_url && " · "}
          {d.oa_url && (
            <a href={d.oa_url} target="_blank" rel="noreferrer noopener">
              Open-access landing page
            </a>
          )}
          . Links are as reported and have not been verified or downloaded.
        </p>
      )}
      <PaperFullText projectId={projectId} paper={d} />
      {d.possible_duplicate_of && (
        <p className="text-xs text-mark">
          Possible duplicate of “{d.possible_duplicate_of.title}”
          {d.possible_duplicate_of.year ? ` (${d.possible_duplicate_of.year})` : ""}:{" "}
          {d.possible_duplicate_reason} Not merged automatically.
        </p>
      )}
      <div>
        <h4 className="section-title mb-1">Abstract</h4>
        <p className="max-w-prose whitespace-pre-wrap">
          {d.abstract ?? <span className="text-muted">No abstract was supplied by any source.</span>}
        </p>
        {d.field_sources.abstract && (
          <p className="mt-1 text-xs text-muted">Abstract from {d.field_sources.abstract}.</p>
        )}
      </div>
      {d.keywords.length > 0 && <p className="text-xs text-muted">Keywords: {d.keywords.join(", ")}</p>}
      <div>
        <h4 className="section-title mb-1">Provenance ({d.sources.length} source records)</h4>
        <ul className="flex flex-col gap-2 text-xs">
          {d.sources.map((s) => (
            <li key={s.id} className="rounded border border-line px-3 py-2">
              <p>
                <span className="text-ink">{s.provider}</span> record{" "}
                {s.source_url ? (
                  <a href={s.source_url} target="_blank" rel="noreferrer noopener">
                    {s.provider_record_id}
                  </a>
                ) : (
                  s.provider_record_id
                )}{" "}
                · rank {s.rank} · retrieved {fmtDate(s.retrieved_at)} · {MATCH_BASIS_LABELS[s.match_basis]}
              </p>
              <p className="mt-1 text-muted">
                Search of {fmtDate(s.search_run_created_at)}; query{" "}
                <span className="font-mono">{s.query_text}</span>
              </p>
              <button
                type="button"
                className="btn-ghost mt-1"
                aria-expanded={rawOpen === s.id}
                onClick={() => setRawOpen(rawOpen === s.id ? null : s.id)}
              >
                {rawOpen === s.id ? "Hide original record" : "Show original record"}
              </button>
              {rawOpen === s.id && (
                <pre className="mt-1 max-h-80 overflow-auto rounded bg-canvas p-2 text-2xs">
                  {JSON.stringify(s.raw, null, 2)}
                </pre>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function PapersPanel({
  projectId,
  onOpenSearch,
}: {
  projectId: string;
  onOpenSearch: () => void;
}) {
  const [text, setText] = useState("");
  const [applied, setApplied] = useState("");
  const [access, setAccess] = useState<"" | AccessStatus>("");
  const [provider, setProvider] = useState("");
  const [flagged, setFlagged] = useState(false);
  const [sort, setSort] = useState("recent");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);

  const params = new URLSearchParams({ sort, offset: String(offset), limit: String(PAGE) });
  if (applied) params.set("q", applied);
  if (access) params.set("access", access);
  if (provider) params.set("provider", provider);
  if (flagged) params.set("flagged", "true");
  const papers = useQuery({
    queryKey: ["papers", projectId, params.toString()],
    queryFn: () => apiFetch<PaperList>(`/projects/${projectId}/papers?${params.toString()}`),
    placeholderData: keepPreviousData,
  });

  const total = papers.data?.total ?? 0;
  const filtered = Boolean(applied || access || provider || flagged);
  const reset = (fn: () => void) => {
    fn();
    setOffset(0);
  };

  if (papers.data && total === 0 && !filtered)
    return (
      <div className="max-w-prose py-10">
        <h2 className="mb-2 text-base font-semibold">No papers yet</h2>
        <p className="text-muted">
          Papers appear here once the approved protocol has been searched in scholarly databases. Every record
          shows where it came from.
        </p>
        <button type="button" className="btn-secondary mt-3" onClick={onOpenSearch}>
          Go to search
        </button>
      </div>
    );

  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <form
        className="flex flex-wrap items-end gap-2"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          reset(() => setApplied(text.trim()));
        }}
      >
        <label className="flex flex-col text-xs text-muted">
          Title, abstract or DOI
          <input
            className="input w-64"
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={200}
          />
        </label>
        <label className="flex flex-col text-xs text-muted">
          Access
          <select
            className="input w-52"
            value={access}
            onChange={(e) => reset(() => setAccess(e.target.value as "" | AccessStatus))}
          >
            {ACCESS_FILTERS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs text-muted">
          Found in
          <select
            className="input w-36"
            value={provider}
            onChange={(e) => reset(() => setProvider(e.target.value))}
          >
            {PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs text-muted">
          Sort
          <select className="input w-32" value={sort} onChange={(e) => reset(() => setSort(e.target.value))}>
            <option value="recent">Newest found</option>
            <option value="year">Year</option>
            <option value="title">Title</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5 pb-2 text-xs text-muted">
          <input
            type="checkbox"
            className="accent-mark"
            checked={flagged}
            onChange={(e) => reset(() => setFlagged(e.target.checked))}
          />
          Possible duplicates only
        </label>
        <button type="submit" className="btn-secondary">
          Filter
        </button>
      </form>

      <p className="text-sm text-muted" aria-live="polite" data-testid="paper-count">
        {papers.data
          ? `${total} paper${total === 1 ? "" : "s"}${filtered ? " match" : " discovered"}`
          : "Loading…"}
      </p>
      {papers.isError && <p className="text-danger">Could not load papers.</p>}

      <ul className="flex flex-col gap-2">
        {papers.data?.items.map((p) => (
          <li key={p.id} className="rounded border border-line bg-panel px-4 py-3" data-testid="paper-card">
            <button
              type="button"
              className="text-left text-ink hover:underline"
              aria-expanded={open === p.id}
              onClick={() => setOpen(open === p.id ? null : p.id)}
            >
              {p.title}
            </button>
            <p className="mt-0.5 text-xs text-muted">
              {p.authors.slice(0, 6).join(", ")}
              {p.author_count > 6 && ` and ${p.author_count - 6} more`}
              {p.year && ` · ${p.year}`}
              {p.venue && ` · ${p.venue}`}
              {p.publication_type && ` · ${p.publication_type}`}
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
              <AccessBadge p={p} />
              {p.is_preprint && (
                <span className="text-xs text-mark">Preprint (not necessarily peer reviewed)</span>
              )}
              {!p.is_preprint && p.has_preprint_version && (
                <span className="text-xs text-muted">Preprint version also found</span>
              )}
              {p.is_retracted && <span className="text-xs text-danger">Retracted</span>}
              {p.possible_duplicate_of_id && <span className="text-xs text-mark">Possible duplicate</span>}
              <span className="text-xs text-muted">
                Found in {p.providers.join(", ")} ({p.source_count} record{p.source_count === 1 ? "" : "s"})
              </span>
            </div>
            <div className="mt-1">
              <Identifiers p={p} />
            </div>
            {open === p.id && <Detail projectId={projectId} paperId={p.id} />}
          </li>
        ))}
      </ul>

      {total > PAGE && (
        <nav aria-label="Pages" className="flex items-center gap-3 text-sm">
          <button
            type="button"
            className="btn-secondary"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
          >
            Previous
          </button>
          <span className="text-muted">
            {offset + 1}–{Math.min(offset + PAGE, total)} of {total}
          </span>
          <button
            type="button"
            className="btn-secondary"
            disabled={offset + PAGE >= total}
            onClick={() => setOffset(offset + PAGE)}
          >
            Next
          </button>
        </nav>
      )}
    </div>
  );
}
