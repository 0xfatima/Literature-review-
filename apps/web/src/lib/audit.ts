import type { AuditActor, AuditEventView } from "@/lib/types";

/** Human wording for who did something. System processes are named; other users are never identified. */
export function actorLabel(a: AuditActor): string {
  if (a.kind === "system") return a.process ? `System (${a.process})` : "System";
  return a.is_you ? "You" : "Another user";
}

/** Field names that changed, from the stored before/after payloads (values are never free text). */
export function changeSummary(e: Pick<AuditEventView, "before" | "after">): string {
  const after = e.after ?? {};
  const fields = after["changed_fields"];
  if (Array.isArray(fields) && fields.length) return `Changed: ${fields.map(String).join(", ")}`;
  const keys = Object.keys(after).filter((k) => k !== "changed_fields");
  return keys.length ? keys.slice(0, 6).join(", ") + (keys.length > 6 ? ", …" : "") : "";
}

export function auditQuery(params: { category?: string; actor?: string; cursor?: string | null }): string {
  const q = new URLSearchParams();
  if (params.category) q.set("category", params.category);
  if (params.actor) q.set("actor", params.actor);
  if (params.cursor) q.set("cursor", params.cursor);
  q.set("limit", "50");
  return q.toString();
}

/** Same-origin download of the CSV export (the server records the export in the audit log). */
export function auditCsvUrl(projectId: string, category?: string): string {
  const q = category ? `?category=${encodeURIComponent(category)}` : "";
  return `/api/v1/projects/${projectId}/audit-events/export.csv${q}`;
}
