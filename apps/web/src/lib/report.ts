import { toApiError } from "@/lib/api";
import type { ReportBlock, ReportIssue } from "@/lib/types";

export const EXPORT_FORMATS = [
  { value: "docx", label: "Word (DOCX)" },
  { value: "markdown", label: "Markdown" },
  { value: "supplementary_zip", label: "Supplementary ZIP (CSV tables, PRISMA JSON, manifest)" },
  { value: "prisma_json", label: "PRISMA flow (JSON)" },
] as const;

/** Display text: [@key] citation markers become [key]. */
export function displayText(text: string): string {
  return text.replace(/\[@([A-Za-z0-9_.:-]+)\]/g, "[$1]");
}

/** Issues attached to a block, errors first. */
export function issuesFor(issues: ReportIssue[], blockId: string): ReportIssue[] {
  return issues
    .filter((i) => i.block_id === blockId)
    .sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "error" ? -1 : 1));
}

export function isEditable(b: ReportBlock): b is Extract<ReportBlock, { type: "paragraph" | "statement" }> {
  return b.type === "paragraph" || b.type === "statement";
}

export function isPlaceholder(text: string): boolean {
  return /\[\[[^\]]*\]\]/.test(text);
}

/** POST an export and save the returned file; the server records the export (version, hash, review state). */
export async function downloadExport(projectId: string, draftId: string, format: string): Promise<string> {
  const res = await fetch(`/api/v1/projects/${projectId}/reports/${draftId}/exports`, {
    method: "POST",
    headers: { "X-SRS-CSRF": "1", "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ format }),
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
  const name =
    /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? `export.${format}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
  return res.headers.get("x-report-export-id") ?? "";
}
