"use client";
import { useCallback, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { formatBytes, uploadDocument, validateFileLocally, type UploadHandle } from "@/lib/documents";

export interface UploadItem {
  id: string;
  name: string;
  size: number;
  progress: number;
  state: "waiting" | "uploading" | "done" | "failed";
  error?: string;
}

/** Sequential upload queue. Each file is checked locally for fast feedback and validated again by the server;
 *  a rejected file shows the server's specific message. Files are uploaded one at a time to keep progress honest. */
export function useUploader(
  projectId: string,
  paperId: string | null,
  maxBytes: number,
  onUploaded: () => void,
) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const queue = useRef<{ id: string; file: File }[]>([]);
  const handles = useRef(new Map<string, UploadHandle>());
  const running = useRef(false);

  const patch = useCallback((id: string, p: Partial<UploadItem>) => {
    setItems((all) => all.map((i) => (i.id === id ? { ...i, ...p } : i)));
  }, []);

  const pump = useCallback(async () => {
    if (running.current) return;
    running.current = true;
    try {
      for (let next = queue.current.shift(); next; next = queue.current.shift()) {
        const { id, file } = next;
        patch(id, { state: "uploading", progress: 0 });
        const h = uploadDocument(projectId, file, paperId, (f) => patch(id, { progress: f }));
        handles.current.set(id, h);
        try {
          await h.promise;
          patch(id, { state: "done", progress: 1 });
          onUploaded();
        } catch (e) {
          patch(id, {
            state: "failed",
            error: e instanceof ApiError ? e.message : "The upload failed. Please try again.",
          });
        } finally {
          handles.current.delete(id);
        }
      }
    } finally {
      running.current = false;
    }
  }, [onUploaded, paperId, patch, projectId]);

  const add = useCallback(
    (files: FileList | File[]) => {
      const fresh: UploadItem[] = [];
      for (const file of Array.from(files)) {
        const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const problem = validateFileLocally(file, maxBytes);
        fresh.push({
          id,
          name: file.name,
          size: file.size,
          progress: 0,
          state: problem ? "failed" : "waiting",
          error: problem ?? undefined,
        });
        if (!problem) queue.current.push({ id, file });
      }
      setItems((all) => [...fresh, ...all]);
      void pump();
    },
    [maxBytes, pump],
  );

  const cancel = useCallback(
    (id: string) => {
      const h = handles.current.get(id);
      if (h) return h.abort();
      queue.current = queue.current.filter((q) => q.id !== id);
      patch(id, { state: "failed", error: "Upload cancelled." });
    },
    [patch],
  );

  const dismiss = useCallback((id: string) => setItems((all) => all.filter((i) => i.id !== id)), []);
  return { items, add, cancel, dismiss };
}

export function UploadQueue({
  items,
  onCancel,
  onDismiss,
}: {
  items: UploadItem[];
  onCancel: (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1.5" aria-label="Uploads" data-testid="upload-queue">
      {items.map((i) => (
        <li key={i.id} className="rounded border border-line bg-panel px-3 py-2 text-sm">
          <div className="flex items-center justify-between gap-3">
            <span className="truncate">
              {i.name} <span className="text-xs text-muted">({formatBytes(i.size)})</span>
            </span>
            {(i.state === "waiting" || i.state === "uploading") && (
              <button type="button" className="btn-ghost" onClick={() => onCancel(i.id)}>
                Cancel
              </button>
            )}
            {(i.state === "done" || i.state === "failed") && (
              <button type="button" className="btn-ghost" onClick={() => onDismiss(i.id)}>
                Dismiss
              </button>
            )}
          </div>
          {i.state === "uploading" && (
            <div className="mt-1 flex items-center gap-2">
              <progress
                className="h-1.5 flex-1"
                max={100}
                value={Math.round(i.progress * 100)}
                aria-label={`Uploading ${i.name}`}
              />
              <span className="text-xs text-muted">{Math.round(i.progress * 100)}%</span>
            </div>
          )}
          {i.state === "waiting" && <p className="mt-1 text-xs text-muted">Waiting to upload</p>}
          {i.state === "done" && (
            <p className="mt-1 text-xs text-ok">Uploaded. It is now being read; see its status below.</p>
          )}
          {i.state === "failed" && (
            <p className="mt-1 text-xs text-danger" role="alert">
              Not uploaded: {i.error}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}
