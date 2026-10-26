import { describe, expect, it } from "vitest";
import { actorLabel, auditCsvUrl, auditQuery, changeSummary } from "@/lib/audit";

describe("audit helpers", () => {
  it("labels actors without identifying other users", () => {
    expect(actorLabel({ kind: "user", is_you: true, process: null })).toBe("You");
    expect(actorLabel({ kind: "user", is_you: false, process: null })).toBe("Another user");
    expect(actorLabel({ kind: "system", is_you: false, process: "worker:system.purge_documents" })).toBe(
      "System (worker:system.purge_documents)",
    );
    expect(actorLabel({ kind: "system", is_you: false, process: null })).toBe("System");
  });

  it("summarises changed field names", () => {
    expect(changeSummary({ before: null, after: { changed_fields: ["title"] } })).toBe("Changed: title");
    expect(changeSummary({ before: null, after: { format: "csv", category: null } })).toBe(
      "format, category",
    );
    expect(changeSummary({ before: null, after: null })).toBe("");
  });

  it("builds query strings and export URLs", () => {
    expect(auditQuery({ category: "screening", actor: "", cursor: "abc" })).toBe(
      "category=screening&cursor=abc&limit=50",
    );
    expect(auditCsvUrl("p1")).toBe("/api/v1/projects/p1/audit-events/export.csv");
    expect(auditCsvUrl("p1", "search")).toBe("/api/v1/projects/p1/audit-events/export.csv?category=search");
  });
});
