"use client";
import { useHasUnsavedChanges } from "@/components/workspace/WorkspaceContext";
import type { Project } from "@/lib/types";
import { useStatus } from "./StatusContext";

/** Bottom bar: only what has no better home. Progress lives in the top-right indicator, jobs and problems in
 *  notifications. What remains is whether your work is saved, and the shortcut to every command. */
export default function StatusBar({ project, isMac }: { project: Project; isMac: boolean }) {
  const { save } = useStatus();
  const unsaved = useHasUnsavedChanges();
  return (
    <>
      <span role="status" className="flex items-center gap-3">
        {unsaved && <span className="text-mark">Unsaved edits</span>}
        {save.kind === "saving" && <span>Saving…</span>}
        {save.kind === "saved" && !unsaved && (
          <span>Saved {save.at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
        )}
        {save.kind === "failed" && <span className="text-danger">Not saved: {save.message}</span>}
        {save.kind === "idle" && !unsaved && <span>All changes saved</span>}
      </span>
      {project.archived_at && <span className="text-mark">Archived, read-only</span>}
      <span className="ml-auto hidden sm:inline">
        <kbd className="font-sans">{isMac ? "⌘" : "Ctrl"} K</kbd> for all commands
      </span>
    </>
  );
}
