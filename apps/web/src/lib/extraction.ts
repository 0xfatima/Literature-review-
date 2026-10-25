import { toApiError } from "@/lib/api";
import type { EvidenceItem, EvidenceStatus, FieldDefinition } from "@/lib/types";

export const EVIDENCE_LABELS: Record<EvidenceStatus, string> = {
  found: "Found",
  not_located: "Not found in processed text",
  ambiguous: "Ambiguous",
  unavailable: "No full text",
  not_applicable: "Not applicable",
  pending: "Pending",
};

/** Human-readable value; never invents one for a missing value. */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.map((v) => formatValue(v)).join("; ");
  if (typeof value === "object")
    return Object.values(value as Record<string, unknown>)
      .map(formatValue)
      .join("; ");
  return String(value);
}

/** Text shown in a table cell. Status is always spelled out so colour never carries meaning alone. */
export function cellText(cell: EvidenceItem | undefined): { primary: string; secondary: string } {
  if (!cell) return { primary: "—", secondary: "Not extracted" };
  const review =
    cell.review_status === "approved"
      ? "Approved"
      : cell.review_status === "edited"
        ? "Edited by reviewer"
        : cell.origin === "ai"
          ? "AI · needs review"
          : cell.origin === "system"
            ? "Automatic"
            : "";
  if (cell.status === "found") {
    const page = cell.page_no ? `p. ${cell.page_no}` : "no page cited";
    return { primary: formatValue(cell.value), secondary: [page, review].filter(Boolean).join(" · ") };
  }
  if (cell.status === "not_located" && cell.pages_inspected.length > 0) {
    return {
      primary: EVIDENCE_LABELS.not_located,
      secondary: [`${cell.pages_inspected.length} page(s) inspected`, review].filter(Boolean).join(" · "),
    };
  }
  return { primary: EVIDENCE_LABELS[cell.status], secondary: review };
}

/** Split page text around the first occurrence of the excerpt (exact match, as stored by the backend). */
export function highlight(text: string, excerpt: string | null): [string, string, string] | null {
  if (!excerpt) return null;
  const i = text.indexOf(excerpt);
  if (i < 0) return null;
  return [text.slice(0, i), excerpt, text.slice(i + excerpt.length)];
}

/** Convert the edit form's text input to the field's type. Returns an error message instead of guessing. */
export function parseInput(field: FieldDefinition, raw: string): { value: unknown } | { error: string } {
  const s = raw.trim();
  if (!s) return { error: "Enter a value." };
  if (field.type === "number") {
    const n = Number(s.replace(/,/g, ""));
    return Number.isFinite(n) ? { value: n } : { error: "Enter a number." };
  }
  if (field.type === "boolean") {
    if (/^(yes|true)$/i.test(s)) return { value: true };
    if (/^(no|false)$/i.test(s)) return { value: false };
    return { error: "Enter yes or no." };
  }
  if (field.type === "multi_choice") {
    const parts = s
      .split(";")
      .map((p) => p.trim())
      .filter(Boolean);
    const bad = parts.find((p) => !(field.options ?? []).includes(p));
    return bad ? { error: `"${bad}" is not one of the options.` } : { value: parts };
  }
  if (field.type === "choice" && !(field.options ?? []).includes(s))
    return { error: "Pick one of the options." };
  return { value: s };
}

/** POST the export and save the CSV the server produced (the export is recorded server-side). */
export async function downloadEvidenceCsv(projectId: string, schemaId: string): Promise<string> {
  const res = await fetch(`/api/v1/projects/${projectId}/extraction/schemas/${schemaId}/exports`, {
    method: "POST",
    headers: { "X-SRS-CSRF": "1" },
    credentials: "same-origin",
  });
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* non-JSON body */
    }
    throw toApiError(res.status, body, res.headers.get("x-correlation-id"));
  }
  const blob = await res.blob();
  const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? "evidence.csv";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
  return res.headers.get("x-evidence-output-id") ?? "";
}
