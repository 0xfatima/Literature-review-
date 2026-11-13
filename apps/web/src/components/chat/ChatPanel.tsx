"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import Icon from "@/components/ui/Icon";
import Menu from "@/components/ui/Menu";
import { PopoverPanel, usePopover } from "@/components/ui/Popover";
import { ApiError, apiFetch, json } from "@/lib/api";
import { type ChatCommand, COMMAND_EXAMPLES, announceChange } from "@/lib/commands";
import { type PendingSend, mergeMessages, newClientId, roleLabel, settlePending } from "@/lib/chat";
import type {
  Conversation,
  ConversationList,
  Message,
  MessagePage,
  MessagePosted,
  Project,
  WorkflowState,
} from "@/lib/types";
import { useStatus } from "@/components/status/StatusContext";
import QuestionCard from "@/components/protocol/QuestionCard";
import NextStepCard from "@/components/chat/NextStepCard";
import { useProtocol, useRefreshReview, useWorkflow } from "@/components/protocol/hooks";
import { isBusy } from "@/lib/protocol";
import { describeError, loadDraft, saveDraft } from "@/lib/workspace";
import { useUnsavedChanges, useWorkspace } from "@/components/workspace/WorkspaceContext";

/** Label for application-written messages, so AI answers and record-based explanations are never confused. */
function payloadNote(payload: Message["payload"], openPaper: ((id: string) => void) | null): React.ReactNode {
  const type = payload?.type;
  if (type !== "ai_answer" && type !== "assist_result") return undefined;
  const paperId = typeof payload?.paper_id === "string" ? payload.paper_id : null;
  return (
    <p className="mt-1 flex flex-wrap items-center gap-2 text-2xs text-muted">
      <span>
        {type === "ai_answer"
          ? `AI answer — advisory, check it against the protocol and sources${
              payload?.answerable_from_context === false
                ? " (the AI could not answer from the review data)"
                : ""
            }.`
          : "Explanation built from the review record (no AI)."}
      </span>
      {paperId && openPaper && (
        <button type="button" className="text-link underline" onClick={() => openPaper(paperId)}>
          Open paper
        </button>
      )}
    </p>
  );
}

const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Network error. Check your connection.");

/** Short time for the message header; the full date is in the tooltip and the machine-readable attribute. */
function shortTime(iso: string): string {
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Messages the workflow writes on your behalf ("Criteria saved…"): a one-line event, not a full card. */
const EVENT_TYPES = new Set(["workflow_answer", "workflow_revision"]);

function MessageItem({
  role,
  content,
  at,
  note,
  event = false,
}: {
  role: string;
  content: string;
  at?: string;
  note?: React.ReactNode;
  event?: boolean;
}) {
  if (event)
    return (
      <li className="mr-4 flex items-start gap-2 px-3 text-xs text-muted">
        <Icon name="check" size={14} className="mt-0.5 text-ok" />
        <p className="min-w-0 flex-1 break-words">
          <span className="sr-only">{roleLabel(role)}: </span>
          {content}
        </p>
        {at && (
          <time dateTime={at} title={new Date(at).toLocaleString()} className="shrink-0 text-2xs">
            {shortTime(at)}
          </time>
        )}
      </li>
    );
  const own = role === "user";
  return (
    <li className={`rounded-lg px-3 py-2 ${own ? "ml-8 bg-raised" : "mr-4 border border-line bg-canvas"}`}>
      <div className="mb-0.5 flex items-baseline justify-between gap-2 text-2xs text-muted">
        <span className="font-semibold text-ink">{roleLabel(role)}</span>
        {at && (
          <time dateTime={at} title={new Date(at).toLocaleString()}>
            {shortTime(at)}
          </time>
        )}
      </div>
      <p className="whitespace-pre-wrap break-words">{content}</p>
      {note}
    </li>
  );
}

function relTime(iso: string): string {
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { month: "short", day: "numeric" });
}

/** Thread switcher: the thread title is a button that lists every thread of the project. */
function ThreadPicker({
  conversation,
  conversations,
  onSelect,
}: {
  conversation: Conversation;
  conversations: ConversationList | undefined;
  onSelect: (c: Conversation) => void;
}) {
  const pop = usePopover();
  return (
    <div className="relative min-w-0 flex-1">
      <button
        ref={pop.triggerRef}
        type="button"
        aria-expanded={pop.open}
        aria-controls={pop.open ? pop.id : undefined}
        className="flex max-w-full items-center gap-1 rounded-md px-2 py-1 text-left hover:bg-raised"
        onClick={() => (pop.open ? pop.close() : pop.setOpen(true))}
      >
        <span className="sr-only">Thread: </span>
        <span className="truncate font-semibold">{conversation.title}</span>
        <Icon name="chevronDown" size={14} className="text-muted" />
        <span className="sr-only">, switch thread</span>
      </button>
      <PopoverPanel pop={pop} label="Chat threads" align="start" className="w-72">
        <ul aria-label="Chat threads" className="max-h-80 overflow-y-auto py-1">
          {conversations?.items.map((c) => {
            const current = c.id === conversation.id;
            return (
              <li key={c.id}>
                <button
                  type="button"
                  aria-current={current ? "true" : undefined}
                  className={`flex w-full items-baseline justify-between gap-2 px-3 py-1.5 text-left text-sm hover:bg-raised ${
                    current ? "text-ink shadow-[inset_2px_0_0_rgb(var(--mark))]" : "text-muted"
                  }`}
                  onClick={() => {
                    pop.close();
                    onSelect(c);
                  }}
                >
                  <span className="truncate">{c.title}</span>
                  <time className="shrink-0 text-2xs" dateTime={c.updated_at}>
                    {relTime(c.updated_at)}
                  </time>
                </button>
              </li>
            );
          })}
        </ul>
      </PopoverPanel>
    </div>
  );
}

/** Chat for one thread. Messages are persisted server-side; nothing here is simulated. */
export default function ChatPanel({
  project,
  conversation,
  conversations,
  onSelectThread,
  onNewThread,
  onDeleted,
  creating,
  wide,
  onHide,
}: {
  project: Project;
  conversation: Conversation | null;
  conversations: ConversationList | undefined;
  onSelectThread: (c: Conversation) => void;
  onNewThread: () => void;
  onDeleted: (conversationId: string) => void;
  creating: boolean;
  /** The chat fills the window (no step open): keep the text column at a readable width. */
  wide: boolean;
  /** Hide the chat to give an open step the full width. */
  onHide?: () => void;
}) {
  const qc = useQueryClient();
  const { setSave, reportError } = useStatus();
  const canWrite = project.archived_at === null;
  const pid = project.id;
  const cid = conversation?.id ?? null;
  const base = `/projects/${pid}/conversations/${cid}`;

  const [draft, setDraftState] = useState("");
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const ws = useWorkspace();
  const setDraft = (v: string) => {
    setDraftState(v);
    if (cid) saveDraft(`chat:${cid}`, v); // kept per thread for this browser tab, so switching never loses it
  };
  useUnsavedChanges("chat-draft", draft.trim().length > 0);
  const [pending, setPending] = useState<PendingSend[]>([]);
  const [older, setOlder] = useState<Message[]>([]);
  const [hasOlderLoaded, setHasOlderLoaded] = useState<boolean | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const listEnd = useRef<HTMLDivElement>(null);
  const workflow = useWorkflow(pid);
  const run = workflow.data?.run ?? null;
  const isProtocolThread = run !== null && cid !== null && run.conversation_id === cid;
  const protocol = useProtocol(pid, run !== null);
  const refreshReview = useRefreshReview(pid);
  const recover = useMutation({
    mutationFn: () => apiFetch<WorkflowState>(`/projects/${pid}/workflow/recover`, { method: "POST" }),
    onSettled: refreshReview,
    onError: (e) => reportError(describeError(e, "Could not restart the workflow.")),
  });
  // null = not chosen yet: on in free threads when an assistant exists (so a question gets an answer), off in the
  // guided setup thread, where the cards are the conversation.
  const [askAi, setAskAi] = useState<boolean | null>(null);
  const [command, setCommand] = useState<ChatCommand | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    // Switching threads resets thread-local UI state; each thread's unsent draft is restored.
    setDraftState(cid ? loadDraft(`chat:${cid}`) : "");
    setPending([]);
    setOlder([]);
    setHasOlderLoaded(null);
    setRenaming(null);
    setConfirmDelete(false);
    setAskAi(null);
    setNotice(null);
  }, [cid]);

  const page = useQuery({
    queryKey: ["messages", pid, cid],
    queryFn: () => apiFetch<MessagePage>(`${base}/messages?limit=100`),
    enabled: cid !== null,
  });
  const messages = mergeMessages(older, page.data?.items ?? []);

  // Each workflow step writes chat messages server-side: reload the thread whenever the run moves.
  const runVersion = run?.row_version;
  useEffect(() => {
    if (isProtocolThread) qc.invalidateQueries({ queryKey: ["messages", pid, cid] });
  }, [runVersion, isProtocolThread, qc, pid, cid]);
  const hasOlder = hasOlderLoaded ?? page.data?.has_older ?? false;

  useEffect(() => {
    listEnd.current?.scrollIntoView({ block: "end" });
  }, [messages.length, pending.length]);

  const refreshThreads = () => qc.invalidateQueries({ queryKey: ["conversations", pid] });

  useEffect(() => {
    if (!ws) return;
    ws.registerComposer((text) => {
      setDraftState(text);
      if (cid) saveDraft(`chat:${cid}`, text);
      requestAnimationFrame(() => composerRef.current?.focus());
    });
    return () => ws.registerComposer(null);
  }, [ws, cid]);

  const assist = useQuery({
    queryKey: ["assist", pid, "status"],
    queryFn: () =>
      apiFetch<{ assistant_available: boolean; data_sent: string }>(`/projects/${pid}/assist/status`),
    staleTime: 60_000,
  });
  const ask = useMutation({
    mutationFn: (question: string) =>
      apiFetch(`/projects/${pid}/conversations/${cid}/ask`, {
        method: "POST",
        ...json({ question, paper_id: ws?.paperId ?? null }),
      }),
    onSuccess: () => {
      setDraft("");
      qc.invalidateQueries({ queryKey: ["messages", pid, cid] });
      refreshThreads();
    },
    onError: (e) => reportError(describeError(e, "The assistant could not answer.")),
  });

  const loadOlder = useMutation({
    mutationFn: () => apiFetch<MessagePage>(`${base}/messages?limit=100&before_seq=${messages[0]?.seq ?? 1}`),
    onSuccess: (p) => {
      setOlder((o) => mergeMessages(p.items, o));
      setHasOlderLoaded(p.has_older);
    },
    onError: (e) => reportError(errText(e)),
  });

  const send = useMutation({
    mutationFn: (p: PendingSend) =>
      apiFetch<MessagePosted>(`${base}/messages`, {
        method: "POST",
        ...json({ content: p.content, client_message_id: p.clientId }),
      }),
    onMutate: (p) => {
      setSave({ kind: "saving" });
      setPending((list) => [...list.filter((x) => x.clientId !== p.clientId), { ...p, state: "sending" }]);
    },
    onSuccess: (res) => {
      qc.setQueryData<MessagePage>(["messages", pid, cid], (old) =>
        old
          ? { ...old, conversation: res.conversation, items: mergeMessages(old.items, [res.message]) }
          : { conversation: res.conversation, items: [res.message], has_older: false },
      );
      setPending((list) => settlePending(list, [res.message]));
      setSave({ kind: "saved", at: new Date() });
      refreshThreads();
      // A plain message gets no reply unless the assistant is asked; say so instead of leaving silence.
      if (!isProtocolThread || run?.status !== "awaiting_input")
        setNotice(
          aiAvailable
            ? "Saved as a note. Switch on “Ask AI” below to get an answer."
            : "Saved as a note. No AI assistant is connected, so nothing will reply here.",
        );
    },
    onError: (e, p) => {
      const message = errText(e);
      setPending((list) =>
        list.map((x) => (x.clientId === p.clientId ? { ...x, state: "failed", error: message } : x)),
      );
      setSave({ kind: "failed", message: "message not sent" });
    },
  });

  const rename = useMutation({
    mutationFn: (title: string) =>
      apiFetch<Conversation>(base, {
        method: "PATCH",
        ...json({ expected_version: conversation?.row_version, title }),
      }),
    onSuccess: () => {
      setRenaming(null);
      refreshThreads();
      setSave({ kind: "saved", at: new Date() });
    },
    onError: (e) =>
      reportError(
        e instanceof ApiError && e.code === "version_conflict"
          ? "Thread changed elsewhere. Reload to rename."
          : errText(e),
      ),
  });

  const remove = useMutation({
    mutationFn: () => apiFetch<void>(base, { method: "DELETE" }),
    onSuccess: () => {
      qc.removeQueries({ queryKey: ["messages", pid, cid] });
      if (cid) onDeleted(cid);
      refreshThreads();
    },
    onError: (e) => reportError(errText(e)),
  });

  /** Messages are first checked for a command ("run the search", "go to screening", "next step"). Navigation
   *  happens at once; actions that change data wait for the person to confirm. Anything else is sent as usual. */
  const interpret = useMutation({
    mutationFn: (text: string) =>
      apiFetch<ChatCommand>(`/projects/${pid}/assist/command`, {
        method: "POST",
        ...json({
          text,
          current_view: ws?.activeView ?? null,
          conversation_id: cid,
          record: true, // the person's message and the reply always appear in the thread
          strict: isProtocolThread,
          allow_ai: aiMode,
        }),
      }),
  });
  const runCommand = useMutation({
    mutationFn: (c: ChatCommand) =>
      apiFetch(c.request!.path, { method: c.request!.method, ...json(c.request!.body) }),
    onSuccess: (_r, c) => {
      setCommand(null);
      setNotice(
        `Done: ${c.reply?.replace(/^I can /, "").replace(/\.$/, "") ?? c.action}. Opening ${c.view}.`,
      );
      if (c.view) ws?.openTab(c.view);
      announceChange(c.view);
      qc.invalidateQueries({ queryKey: ["workspace", pid] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["project", pid] });
    },
    onError: (e) => reportError(describeError(e, "That action could not be started.")),
  });

  async function dispatch(text: string): Promise<boolean> {
    let c: ChatCommand;
    try {
      c = await interpret.mutateAsync(text);
    } catch {
      return false; // the interpreter is a convenience: on any error the message is sent normally
    }
    if (c.action === "none") return false;
    setDraft("");
    setNotice(c.reply ?? null);
    qc.invalidateQueries({ queryKey: ["messages", pid, cid] });
    refreshThreads();
    if (c.request) setCommand(c);
    else if (c.view) ws?.openTab(c.view);
    return true;
  }

  function submit() {
    const content = draft.trim();
    if (!content || !cid) return;
    setDraft("");
    send.mutate({ clientId: newClientId(), content, state: "sending" });
  }

  const column = wide ? "mx-auto w-full max-w-3xl" : "w-full";
  const aiAvailable = assist.data?.assistant_available === true;
  const aiMode = (askAi ?? !isProtocolThread) && aiAvailable;
  async function primary() {
    const text = draft.trim();
    if (!text || interpret.isPending) return;
    setNotice(null);
    if (await dispatch(text)) return;
    if (aiMode) ask.mutate(text);
    else submit();
  }

  if (!conversation) {
    return (
      <>
        {onHide && (
          // Same control as in a thread's header, so the chat can always step aside for an open step.
          <div className="hidden h-10 shrink-0 items-center justify-end border-b border-line px-2 md:flex">
            <button
              type="button"
              className="btn-icon"
              aria-label="Hide chat"
              title="Hide chat"
              onClick={onHide}
            >
              <Icon name="arrowLeft" size={18} />
            </button>
          </div>
        )}
        <div className={`${column} flex flex-1 flex-col items-start justify-center gap-3 p-6`}>
          <h2 className="text-base font-semibold">
            {run === null ? "Set up your review" : "Start a conversation"}
          </h2>
          <p className="max-w-prose text-muted">
            {run === null
              ? "I'll ask you a few short questions about your review (objective, framework, questions, scope and criteria), one at a time. Your answers are saved as you go."
              : "Threads keep each conversation, and you can come back to them at any time."}
          </p>
          <button type="button" className="btn" onClick={onNewThread} disabled={!canWrite || creating}>
            {creating ? "Starting…" : run === null ? "Start setting up the review" : "Start a thread"}
          </button>
          {!canWrite && (
            <p className="text-xs text-muted">This project is archived. Unarchive it to start a thread.</p>
          )}
        </div>
      </>
    );
  }

  const failed = isProtocolThread && (run?.status === "failed" || run?.status === "needs_attention");

  return (
    <>
      <div className="flex h-10 shrink-0 items-center gap-1 border-b border-line px-2">
        {renaming !== null ? (
          <form
            className="flex flex-1 items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (renaming.trim()) rename.mutate(renaming.trim());
            }}
          >
            <label htmlFor="thread-title" className="sr-only">
              Thread title
            </label>
            <input
              id="thread-title"
              className="input py-1"
              value={renaming}
              maxLength={200}
              autoFocus
              onChange={(e) => setRenaming(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setRenaming(null);
              }}
            />
            <button className="btn py-1" disabled={!renaming.trim() || rename.isPending}>
              Save
            </button>
            <button type="button" className="btn-ghost" onClick={() => setRenaming(null)}>
              Cancel
            </button>
          </form>
        ) : (
          <>
            <ThreadPicker
              conversation={conversation}
              conversations={conversations}
              onSelect={onSelectThread}
            />
            <button
              type="button"
              className="btn-icon"
              aria-label="New thread"
              title="New thread"
              onClick={onNewThread}
              disabled={!canWrite || creating}
            >
              <Icon name="plus" size={18} />
            </button>
            <Menu
              label="Thread actions"
              items={[
                {
                  label: "Rename thread",
                  icon: "pencil",
                  disabled: !canWrite,
                  onSelect: () => setRenaming(conversation.title),
                },
                {
                  label: "Delete thread",
                  icon: "trash",
                  danger: true,
                  disabled: !canWrite,
                  onSelect: () => setConfirmDelete(true),
                },
              ]}
            />
            {onHide && (
              <button
                type="button"
                className="btn-icon hidden md:inline-flex"
                aria-label="Hide chat"
                title="Hide chat"
                onClick={onHide}
              >
                <Icon name="arrowLeft" size={18} />
              </button>
            )}
          </>
        )}
      </div>

      {confirmDelete && (
        <div
          role="alertdialog"
          aria-labelledby="del-h"
          className="flex flex-wrap items-center gap-2 border-b border-danger/40 bg-danger/10 px-3 py-2 text-sm"
        >
          <p id="del-h" className="flex-1">
            Delete “{conversation.title}” and its {conversation.message_count} messages? This cannot be
            undone.
          </p>
          <button
            type="button"
            className="btn-danger py-1"
            autoFocus
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
          >
            Delete thread
          </button>
          <button type="button" className="btn-ghost" onClick={() => setConfirmDelete(false)}>
            Keep it
          </button>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className={`${column} px-3 py-3`}>
          <p className="mb-3 flex items-center gap-1.5 text-xs text-muted">
            <Icon name="info" size={14} />
            {isProtocolThread
              ? "Guided protocol setup. Answer in the cards; every answer is saved as you go."
              : aiAvailable
                ? "Messages are saved to this project. Switch on “Ask AI” to get an answer from the assistant."
                : "Messages are saved to this project. No AI assistant is configured on this server."}
          </p>
          {hasOlder && (
            <button
              type="button"
              className="btn-ghost mb-2 w-full text-xs"
              onClick={() => loadOlder.mutate()}
              disabled={loadOlder.isPending}
            >
              {loadOlder.isPending ? "Loading…" : "Load earlier messages"}
            </button>
          )}
          {page.isPending && <p className="text-muted">Loading messages…</p>}
          {page.isError && (
            <p role="alert" className="text-danger">
              Could not load messages.{" "}
              <button type="button" className="underline" onClick={() => page.refetch()}>
                Try again
              </button>
            </p>
          )}
          {page.isSuccess && messages.length === 0 && pending.length === 0 && (
            <p className="text-muted">No messages yet. Write the first one below.</p>
          )}
          <ol aria-label="Messages" className="flex flex-col gap-2.5">
            {messages.map((m) => {
              const live =
                isProtocolThread &&
                run?.status === "awaiting_input" &&
                m.payload?.type === "workflow_question" &&
                m.payload.question_id === run.pending_question_id &&
                run.pending_question &&
                protocol.data;
              const event =
                m.role === "system" ||
                (typeof m.payload?.type === "string" && EVENT_TYPES.has(m.payload.type));
              return (
                <MessageItem
                  key={m.id}
                  role={m.role}
                  content={m.content}
                  at={m.created_at}
                  event={event && !live}
                  note={
                    live && run.pending_question && protocol.data ? (
                      <div className="mt-2 border-t border-line pt-2">
                        <QuestionCard
                          key={run.pending_question.id}
                          projectId={pid}
                          question={run.pending_question}
                          protocol={protocol.data}
                          disabled={!canWrite || protocol.isFetching}
                          onAsk={() => {
                            setDraft(`About this step (${run.pending_question?.kind ?? "question"}): `);
                            requestAnimationFrame(() => composerRef.current?.focus());
                          }}
                        />
                      </div>
                    ) : (
                      payloadNote(m.payload, ws ? (id) => ws.openPaper(id) : null)
                    )
                  }
                />
              );
            })}
            {pending.map((p) => (
              <MessageItem
                key={p.clientId}
                role="user"
                content={p.content}
                note={
                  p.state === "sending" ? (
                    <p className="mt-1 text-2xs text-muted">Sending…</p>
                  ) : (
                    <p className="mt-1 flex flex-wrap items-center gap-2 text-2xs text-danger" role="alert">
                      <span>Not sent: {p.error}</span>
                      <button type="button" className="underline" onClick={() => send.mutate(p)}>
                        Retry
                      </button>
                      <button
                        type="button"
                        className="underline"
                        onClick={() => {
                          setPending((l) => l.filter((x) => x.clientId !== p.clientId));
                          setDraft(p.content);
                        }}
                      >
                        Edit
                      </button>
                    </p>
                  )
                }
              />
            ))}
            {(ask.isPending || interpret.isPending) && (
              <li
                className="mr-4 flex items-center gap-2 rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-muted"
                aria-live="polite"
              >
                <span className="flex gap-1" aria-hidden>
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-mark" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-mark [animation-delay:150ms]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-mark [animation-delay:300ms]" />
                </span>
                {ask.isPending ? "The assistant is thinking…" : "Reading your message…"}
              </li>
            )}
            {isProtocolThread && run && !isBusy(run) && run.status !== "awaiting_input" && protocol.data && (
              <NextStepCard
                projectId={pid}
                canWrite={canWrite}
                protocol={protocol.data}
                openTab={(v) => ws?.openTab(v)}
              />
            )}
          </ol>
          {isProtocolThread && isBusy(run) && (
            <p
              className="mt-2 flex items-center gap-2 rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-muted"
              aria-live="polite"
            >
              <span
                aria-hidden
                className="spin inline-block h-3.5 w-3.5 rounded-full border-2 border-mark border-t-transparent"
              />
              Preparing the next question…
            </p>
          )}
          {failed && (
            <div role="alert" className="mt-3 rounded-md border border-danger/50 bg-danger/10 p-3 text-sm">
              <p className="font-medium text-danger">The protocol setup stopped.</p>
              {run?.error_summary && <p className="mt-0.5 text-muted">{run.error_summary}</p>}
              <button
                type="button"
                className="btn-secondary mt-2"
                disabled={!canWrite || recover.isPending}
                onClick={() => recover.mutate()}
              >
                Restart from the saved protocol
              </button>
            </div>
          )}
          <div ref={listEnd} />
        </div>
      </div>

      {(command || notice) && (
        <div className={`${column} shrink-0 px-3 pt-2`} aria-live="polite">
          {command ? (
            <div className="rounded-lg border border-ok/50 bg-ok/10 p-3 text-sm">
              <p className="text-ink">{command.reply}</p>
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-md bg-ok px-3 py-1.5 text-sm font-semibold text-white hover:bg-ok/90 disabled:opacity-60"
                  disabled={!canWrite || runCommand.isPending}
                  onClick={() => runCommand.mutate(command)}
                >
                  <Icon name="check" size={14} /> {runCommand.isPending ? "Starting…" : "Yes, do it"}
                </button>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => {
                    setCommand(null);
                    setNotice(null);
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <p className="flex items-center gap-2 text-xs text-ok">
              <Icon name="check" size={14} /> {notice}
              <button type="button" className="ml-auto text-muted underline" onClick={() => setNotice(null)}>
                Dismiss
              </button>
            </p>
          )}
        </div>
      )}
      <form
        className="shrink-0 border-t border-line"
        onSubmit={(e) => {
          e.preventDefault();
          void primary();
        }}
      >
        <div className={`${column} p-3`}>
          <div className="rounded-lg border border-line bg-raised focus-within:border-mark">
            <label htmlFor="composer" className="sr-only">
              {aiMode ? "Question for the AI assistant" : "Message"}
            </label>
            <textarea
              ref={composerRef}
              id="composer"
              className="block w-full resize-none bg-transparent px-3 pt-2 text-sm text-ink placeholder:text-muted focus:outline-none disabled:cursor-not-allowed"
              rows={2}
              maxLength={20000}
              disabled={!canWrite}
              aria-describedby="composer-help"
              placeholder={
                !canWrite
                  ? "Archived projects are read-only."
                  : aiMode
                    ? "Ask the AI, or tell it what to do…"
                    : isProtocolThread
                      ? "Answer here, or say e.g. “open screening”…"
                      : `Write a message, or say what to do: “${COMMAND_EXAMPLES.join("”, “")}”…`
              }
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void primary();
                }
              }}
            />
            <div className="flex items-center gap-2 px-2 pb-2">
              {aiAvailable && (
                <button
                  type="button"
                  role="switch"
                  aria-checked={aiMode}
                  title={assist.data?.data_sent}
                  className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1 text-xs ${
                    aiMode ? "bg-mark/15 text-ink" : "text-muted hover:bg-canvas hover:text-ink"
                  }`}
                  onClick={() => setAskAi(!aiMode)}
                >
                  <Icon name="sparkle" size={14} className={aiMode ? "text-mark" : ""} />
                  Ask AI
                  <span className="sr-only">{aiMode ? " (on)" : " (off)"}</span>
                </button>
              )}
              <span id="composer-help" className="mr-auto truncate text-2xs text-muted">
                {ws?.paperId && aiMode ? "Includes the open paper · " : ""}
                <span className="hidden sm:inline">Enter to send, Shift+Enter for a new line</span>
              </span>
              <button
                className="btn py-1"
                disabled={!canWrite || !draft.trim() || ask.isPending || interpret.isPending}
                aria-label={aiMode ? "Ask the AI assistant" : "Send message"}
              >
                <Icon name={aiMode ? "sparkle" : "send"} size={14} />
                {ask.isPending ? "Asking…" : aiMode ? "Ask" : "Send"}
              </button>
            </div>
          </div>
        </div>
      </form>
    </>
  );
}
