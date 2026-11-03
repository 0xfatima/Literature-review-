"use client";
import { type ReactNode, useEffect, useRef, useState } from "react";
import type { Project } from "@/lib/types";
import ProtocolPanel from "@/components/protocol/ProtocolPanel";
import SearchPanel from "@/components/search/SearchPanel";
import PapersPanel from "@/components/papers/PapersPanel";
import DocumentsPanel from "@/components/documents/DocumentsPanel";
import PrismaFlowPanel from "@/components/prisma/PrismaFlowPanel";
import FullTextPanel from "@/components/fulltext/FullTextPanel";
import AppraisalPanel from "@/components/appraisal/AppraisalPanel";
import EvidenceTablePanel from "@/components/extraction/EvidenceTablePanel";
import ExtractionPanel from "@/components/extraction/ExtractionPanel";
import ReportPanel from "@/components/report/ReportPanel";
import SynthesisPanel from "@/components/synthesis/SynthesisPanel";
import ScreeningPanel from "@/components/screening/ScreeningPanel";
import PaperInspector from "@/components/papers/PaperInspector";
import AutopilotPanel from "@/components/pipeline/AutopilotPanel";
import AuditPanel from "@/components/audit/AuditPanel";
import Icon from "@/components/ui/Icon";
import { type ViewId, nextAfterClose, stepOfView, viewLabel } from "@/lib/steps";

/** Editor area: the step views the person opened, as tabs (like editor tabs in VS Code). Opened views stay
 *  mounted while hidden, so switching tabs never loses unsaved edits, filters or scroll position. */
export default function Workspace({
  project,
  open,
  active,
  onActivate,
  onClose,
  onOpenConversation,
}: {
  project: Project;
  open: ViewId[];
  active: ViewId | null;
  onActivate: (v: ViewId) => void;
  onClose: (v: ViewId) => void;
  onOpenConversation: (conversationId: string) => void;
}) {
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [visited, setVisited] = useState<Set<ViewId>>(() => new Set(active ? [active] : []));
  useEffect(() => {
    if (active) setVisited((v) => (v.has(active) ? v : new Set([...v, active])));
  }, [active]);
  const canWrite = project.archived_at === null;
  const pid = project.id;
  const go = (v: ViewId) => onActivate(v);

  const views: Record<ViewId, () => ReactNode> = {
    protocol: () => (
      <ProtocolPanel projectId={pid} canWrite={canWrite} onOpenConversation={onOpenConversation} />
    ),
    autopilot: () => <AutopilotPanel projectId={pid} canWrite={canWrite} />,
    search: () => (
      <SearchPanel
        projectId={pid}
        canWrite={canWrite}
        onOpenProtocol={() => go("protocol")}
        onOpenPapers={() => go("papers")}
        onOpenScreening={() => go("screening")}
      />
    ),
    papers: () => <PapersPanel projectId={pid} onOpenSearch={() => go("search")} />,
    screening: () => <ScreeningPanel projectId={pid} canWrite={canWrite} onOpenSearch={() => go("search")} />,
    files: () => <DocumentsPanel projectId={pid} canWrite={canWrite} />,
    fulltext: () => (
      <FullTextPanel
        projectId={pid}
        canWrite={canWrite}
        onOpenScreening={() => go("screening")}
        onOpenFiles={() => go("files")}
      />
    ),
    extraction: () => <ExtractionPanel projectId={pid} canWrite={canWrite} />,
    tables: () => <EvidenceTablePanel projectId={pid} canWrite={canWrite} />,
    appraisal: () => <AppraisalPanel projectId={pid} canWrite={canWrite} />,
    synthesis: () => <SynthesisPanel projectId={pid} canWrite={canWrite} />,
    flow: () => <PrismaFlowPanel projectId={pid} onOpenFullText={() => go("fulltext")} />,
    report: () => <ReportPanel projectId={pid} canWrite={canWrite} />,
    audit: () => <AuditPanel projectId={pid} />,
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div
        role="tablist"
        aria-label="Open steps"
        className="flex h-10 shrink-0 items-end overflow-x-auto border-b border-line bg-panel"
      >
        {open.map((v, i) => {
          const selected = v === active;
          return (
            <div
              key={v}
              className={`group flex h-full shrink-0 items-center border-r border-line ${
                selected ? "bg-canvas shadow-[inset_0_2px_0_rgb(var(--mark))]" : "hover:bg-raised"
              }`}
            >
              <button
                ref={(el) => {
                  tabRefs.current[v] = el;
                }}
                role="tab"
                id={`tab-${v}`}
                aria-selected={selected}
                aria-controls={`panel-${v}`}
                tabIndex={selected ? 0 : -1}
                title={`${stepOfView(v)?.label} step · Delete closes this tab`}
                aria-keyshortcuts="Delete"
                className={`h-full pl-3 pr-1 text-sm ${selected ? "text-ink" : "text-muted"}`}
                onClick={() => onActivate(v)}
                onKeyDown={(e) => {
                  let next: ViewId | undefined;
                  if (e.key === "ArrowRight") next = open[(i + 1) % open.length];
                  else if (e.key === "ArrowLeft") next = open[(i - 1 + open.length) % open.length];
                  else if (e.key === "Delete") {
                    e.preventDefault();
                    const after = nextAfterClose(open, v, active);
                    onClose(v);
                    // Keep keyboard focus in the tab row (or hand it to the message box when none is left).
                    requestAnimationFrame(() =>
                      (after ? tabRefs.current[after] : document.getElementById("composer"))?.focus(),
                    );
                    return;
                  } else return;
                  e.preventDefault();
                  if (next) {
                    onActivate(next);
                    tabRefs.current[next]?.focus();
                  }
                }}
              >
                {viewLabel(v)}
              </button>
              {/* Pointer shortcut only: inside a tab list only tabs may be focusable, so keyboard and
                  screen-reader users close a tab with Delete (or "Close …" in the command palette). */}
              <button
                type="button"
                tabIndex={-1}
                aria-hidden="true"
                className={`mr-1 inline-flex h-6 w-6 items-center justify-center rounded text-muted hover:bg-raised hover:text-ink ${
                  selected ? "" : "opacity-0 group-hover:opacity-100"
                }`}
                title={`Close ${viewLabel(v)}`}
                onClick={() => onClose(v)}
              >
                <Icon name="x" size={14} />
              </button>
            </div>
          );
        })}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-y-auto">
          {[...visited].map((v) => (
            <div
              key={v}
              role="tabpanel"
              id={`panel-${v}`}
              aria-labelledby={`tab-${v}`}
              hidden={active !== v || !open.includes(v)}
              className="px-6 py-5"
            >
              {views[v]()}
            </div>
          ))}
        </div>
        <PaperInspector projectId={pid} canWrite={canWrite} />
      </div>
    </div>
  );
}
