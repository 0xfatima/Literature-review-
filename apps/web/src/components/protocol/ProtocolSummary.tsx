"use client";
import type { ProtocolVersion } from "@/lib/types";

/** The summary text exactly as the server built it from the stored protocol, plus approval issues. */
export default function ProtocolSummary({ version }: { version: ProtocolVersion }) {
  const errors = version.issues.filter((i) => i.severity === "error");
  const warnings = version.issues.filter((i) => i.severity === "warning");
  return (
    <div>
      <pre
        aria-label={`Protocol version ${version.version} summary`}
        className="max-h-80 overflow-auto whitespace-pre-wrap rounded border border-line bg-canvas p-2 font-sans text-sm"
      >
        {version.summary.join("\n")}
      </pre>
      {errors.length > 0 && (
        <ul aria-label="Must fix before approval" className="mt-2 text-sm text-danger">
          {errors.map((i, n) => (
            <li key={n}>Must fix: {i.message}</li>
          ))}
        </ul>
      )}
      {warnings.length > 0 && (
        <ul aria-label="Warnings" className="mt-1 text-sm text-muted">
          {warnings.map((i, n) => (
            <li key={n}>Note: {i.message}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
