"use client";
import { useEffect, useRef, useState } from "react";
import Icon, { type IconName } from "@/components/ui/Icon";
import { PopoverPanel, usePopover } from "@/components/ui/Popover";
import { type Notice, type NoticeTone, useStatus } from "./StatusContext";

const ICON: Record<NoticeTone, IconName> = { info: "info", success: "check", error: "alert", action: "bell" };
const TONE: Record<NoticeTone, string> = {
  info: "text-link",
  success: "text-ok",
  error: "text-danger",
  action: "text-mark",
};
const TONE_WORD: Record<NoticeTone, string> = {
  info: "Info",
  success: "Done",
  error: "Problem",
  action: "Needs you",
};

function time(d: Date) {
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function NoticeBody({ n, onAction }: { n: Notice; onAction?: () => void }) {
  return (
    <div className="flex min-w-0 flex-1 gap-2.5">
      <Icon name={ICON[n.tone]} className={`mt-0.5 ${TONE[n.tone]}`} />
      <div className="min-w-0 flex-1">
        <p className="text-sm text-ink">
          <span className="sr-only">{TONE_WORD[n.tone]}: </span>
          {n.title}
        </p>
        {n.body && <p className="mt-0.5 text-xs text-muted">{n.body}</p>}
        {n.action && (
          <button
            type="button"
            className="mt-1 text-xs font-medium text-link hover:underline"
            onClick={() => {
              n.action?.run();
              onAction?.();
            }}
          >
            {n.action.label}
          </button>
        )}
      </div>
    </div>
  );
}

/** Bell in the title bar: every notice of this session, newest first, with an unread count. */
export function NotificationBell() {
  const { notices, unread, markAllRead, clearNotices } = useStatus();
  const pop = usePopover();
  return (
    <div className="relative">
      <button
        ref={pop.triggerRef}
        type="button"
        className="btn-icon relative"
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        aria-expanded={pop.open}
        aria-controls={pop.open ? pop.id : undefined}
        onClick={() => {
          if (pop.open) pop.close();
          else pop.setOpen(true);
        }}
      >
        <Icon name="bell" size={18} />
        {unread > 0 && (
          <span
            aria-hidden
            className="absolute right-0.5 top-0.5 min-w-4 rounded-full bg-mark px-1 text-center text-[0.625rem] font-semibold leading-4 text-onmark"
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
      <PopoverPanel pop={pop} label="Notifications" className="w-[min(24rem,92vw)]">
        <div className="flex items-center justify-between border-b border-line px-3 py-2">
          <h2 className="text-sm font-semibold">Notifications</h2>
          <div className="flex gap-1">
            <button type="button" className="btn-ghost text-xs" disabled={!unread} onClick={markAllRead}>
              Mark all read
            </button>
            <button
              type="button"
              className="btn-ghost text-xs"
              disabled={!notices.length}
              onClick={clearNotices}
            >
              Clear
            </button>
          </div>
        </div>
        {notices.length === 0 ? (
          <p className="px-3 py-6 text-center text-sm text-muted">
            Nothing yet. Finished jobs, questions waiting for you and problems will show up here.
          </p>
        ) : (
          <ul className="max-h-[60vh] overflow-y-auto">
            {notices.map((n) => (
              <li
                key={n.id}
                className={`flex gap-2 border-b border-line px-3 py-2.5 last:border-b-0 ${
                  n.read ? "" : "bg-raised/60"
                }`}
              >
                <NoticeBody n={n} onAction={() => pop.close(false)} />
                <time className="shrink-0 text-2xs text-muted" dateTime={n.at.toISOString()}>
                  {time(n.at)}
                  {!n.read && <span className="sr-only"> (unread)</span>}
                </time>
              </li>
            ))}
          </ul>
        )}
      </PopoverPanel>
    </div>
  );
}

/** One toast. Success and info leave after 7s (paused while hovered or focused); problems and questions stay
 *  until dismissed, so nobody has to read against a clock (WCAG 2.2.1). Everything stays in the bell. */
function Toast({ n }: { n: Notice }) {
  const { dismissToast } = useStatus();
  const [paused, setPaused] = useState(false);
  const sticky = n.tone === "error" || n.tone === "action";
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (sticky || paused) return;
    timer.current = setTimeout(() => dismissToast(n.id), 7000);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [sticky, paused, n.id, dismissToast]);
  return (
    <li
      role={n.tone === "error" ? "alert" : undefined}
      className={`surface-float pointer-events-auto flex w-[min(22rem,calc(100vw-2rem))] gap-2 border-l-[3px] p-3 ${
        n.tone === "error"
          ? "border-l-danger"
          : n.tone === "success"
            ? "border-l-ok"
            : n.tone === "action"
              ? "border-l-mark"
              : "border-l-link"
      }`}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <NoticeBody n={n} onAction={() => dismissToast(n.id)} />
      <button
        type="button"
        className="btn-icon -m-1 h-7 w-7"
        aria-label="Dismiss notification"
        onClick={() => dismissToast(n.id)}
      >
        <Icon name="x" size={14} />
      </button>
    </li>
  );
}

/** Live region for new notices, bottom right above the status bar. At most three at once. */
export function Toaster() {
  const { notices } = useStatus();
  const shown = notices.filter((n) => n.toast).slice(0, 3);
  return (
    <ol
      aria-live="polite"
      aria-label="Notifications"
      className="pointer-events-none fixed bottom-9 right-3 z-[60] flex flex-col-reverse gap-2"
    >
      {shown.map((n) => (
        <Toast key={n.id} n={n} />
      ))}
    </ol>
  );
}
