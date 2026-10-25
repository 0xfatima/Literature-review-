"use client";
import { cellText } from "@/lib/extraction";
import type { EvidenceItem, FieldDefinition } from "@/lib/types";

/** One paper's extraction values as a table: value or status in words, page and review state per field. */
export default function ExtractionTable({
  fields,
  cells,
  onSelect,
}: {
  fields: FieldDefinition[];
  cells: Record<string, EvidenceItem>;
  onSelect?: (fieldKey: string) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-xs">
        <thead className="bg-raised">
          <tr>
            <th scope="col" className="p-1.5">
              Field
            </th>
            <th scope="col" className="p-1.5">
              Value or status
            </th>
            <th scope="col" className="p-1.5">
              Source and review
            </th>
          </tr>
        </thead>
        <tbody>
          {fields.map((f) => {
            const t = cellText(cells[f.field_key]);
            return (
              <tr key={f.field_key} className="border-t border-line align-top">
                <th scope="row" className="p-1.5 font-normal">
                  {onSelect ? (
                    <button
                      type="button"
                      className="text-left text-link"
                      onClick={() => onSelect(f.field_key)}
                    >
                      {f.label}
                    </button>
                  ) : (
                    f.label
                  )}
                </th>
                <td className="p-1.5">{t.primary}</td>
                <td className="p-1.5 text-muted">
                  {t.secondary}
                  {(cells[f.field_key]?.validation.length ?? 0) > 0 && (
                    <span className="text-danger"> · {cells[f.field_key]?.validation.length} warning(s)</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
