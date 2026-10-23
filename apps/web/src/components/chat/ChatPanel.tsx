"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ApiError, apiFetch, json } from "@/lib/api";
import { type PendingSend, mergeMessages, newClientId, roleLabel, settlePending } from "@/lib/chat";
import type { Conversation, Message, MessagePage, MessagePosted, Project } from "@/lib/types";
import { useStatus } from "@/components/status/StatusContext";
import QuestionCard from "@/components/protocol/QuestionCard";
import { useProtocol, useWorkflow } from "@/components/protocol/hooks";
import { isBusy } from "@/lib/protocol";

const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Network error. Check your connection.");

function MessageItem({
  role,
  content,
  at,
  note,
}: {
  role: string;
  content: string;
  at?: string;
  note?: React.ReactNode;
}) {
  const own = role === "user";
  return (
    <li className={`rounded px-3 py-2 ${own ? "bg-raised" : "border border-line"}`}>
      <div className="mb-0.5 flex items-baseline justify-between gap-2 text-2xs text-muted">
        <span className="font-semibold text-ink">{roleLabel(role)}</span>
        {at && <time dateTime={at}>{new Date(at).toLocaleString()}</time>}
      </div>
      <p className="whitespace-pre-wrap break-words">{content}</p>
      {note}
    </li>
  );
}

/** Chat for one thread. Messages are persisted server-side; nothing here is simulated. */
export default function ChatPanel({
  project,
  conversation,
  onNewThread,
  onDeleted,
  creating,
}: {
  project: Project;
  conversation: Conversation | null;
  onNewThread: () => void;
  onDeleted: (conversationId: string) => void;
  creating: boolean;
}) {
  const qc = useQueryClient();
  const { setSave, reportError } = useStatus();
  const canWrite = project.archived_at === null;
  const pid = project.id;
  const cid = conversation?.id ?? null;
  const base = `/projects/${pid}/conversations/${cid}`;

  const [draft, setDraft] = useState("");
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

  useEffect(() => {
    // Switching threads resets thread-local UI state (drafts are per thread and not persisted).
    setDraft("");
    setPending([]);
    setOlder([]);
    setHasOlderLoaded(null);
    setRenaming(null);
    setConfirmDelete(false);
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

  function submit() {
    const content = draft.trim();
    if (!content || !cid) return;
    setDraft("");
    send.mutate({ clientId: newClientId(), content, state: "sending" });
  }

  if (!conversation) {
    return (
      <div className="flex flex-1 flex-col items-start justify-center gap-3 p-6">
        <h2 className="text-base font-semibold">No thread selected</h2>
        <p className="text-muted">
          Threads keep the conversation for this review in one place, and you can return to them at any time.
        </p>
        <button type="button" className="btn" onClick={onNewThread} disabled={!canWrite || creating}>
          Start a thread
        </button>
        {!canWrite && (
          <p className="text-xs text-muted">This project is archived. Unarchive it to start a thread.</p>
        )}
      </div>
    );
  }

  return (
    <>
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        {renaming !== null ? (
          <form
            className="flex flex-1 gap-2"
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
            />
            <button className="btn" disabled={!renaming.trim() || rename.isPending}>
              Save
            </button>
            <button type="button" className="btn-ghost" onClick={() => setRenaming(null)}>
              Cancel
            </button>
          </form>
        ) : (
          <>
            <h2 className="min-w-0 flex-1 truncate font-semibold" title={conversation.title}>
              {conversation.title}
            </h2>
            {canWrite && (
              <button
                type="button"
                className="btn-ghost text-xs"
                onClick={() => setRenaming(conversation.title)}
              >
                Rename thread
              </button>
            )}
            {confirmDelete ? (
              <>
                <button type="button" className="btn-danger py-0.5 text-xs" onClick={() => remove.mutate()}>
                  Delete thread and messages
                </button>
                <button type="button" className="btn-ghost text-xs" onClick={() => setConfirmDelete(false)}>
                  Keep
                </button>
              </>
            ) : (
              <button type="button" className="btn-ghost text-xs" onClick={() => setConfirmDelete(true)}>
                Delete thread
              </button>
            )}
          </>
        )}
      </div>

      <p className="border-b border-line bg-canvas/40 px-3 py-2 text-xs text-muted">
        {isProtocolThread
          ? "Protocol setup. The questions come from a fixed protocol workflow (no AI model is used); answer them in the cards. Your progress is saved after every answer."
          : "The research assistant is not connected yet. Your messages are saved in this project and stay available for it later."}
      </p>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
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
        <ol aria-label="Messages" className="flex flex-col gap-2">
          {messages.map((m) => {
            const live =
              isProtocolThread &&
              run?.status === "awaiting_input" &&
              m.payload?.type === "workflow_question" &&
              m.payload.question_id === run.pending_question_id &&
              run.pending_question &&
              protocol.data;
            return (
              <MessageItem
                key={m.id}
                role={m.role}
                content={m.content}
                at={m.created_at}
                note={
                  live && run.pending_question && protocol.data ? (
                    <QuestionCard
                      key={run.pending_question.id}
                      projectId={pid}
                      question={run.pending_question}
                      protocol={protocol.data}
                      disabled={!canWrite || protocol.isFetching}
                    />
                  ) : undefined
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
        </ol>
        {isProtocolThread && isBusy(run) && (
          <p className="mt-2 text-xs text-muted" aria-live="polite">
            Working on the next step…
          </p>
        )}
        {isProtocolThread && (run?.status === "failed" || run?.status === "needs_attention") && (
          <p role="alert" className="mt-2 text-xs text-danger">
            {run.error_summary} Use “Restart the workflow” on the Review status tab.
          </p>
        )}
        <div ref={listEnd} />
      </div>

      <form
        className="border-t border-line p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label htmlFor="composer" className="sr-only">
          Message
        </label>
        <textarea
          id="composer"
          className="input resize-none"
          rows={3}
          maxLength={20000}
          disabled={!canWrite}
          placeholder={
            canWrite
              ? "Write a message. Enter sends, Shift+Enter adds a line."
              : "Archived projects are read-only."
          }
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <div className="mt-2 flex justify-end">
          <button className="btn" disabled={!canWrite || !draft.trim()}>
            Send
          </button>
        </div>
      </form>
    </>
  );
}
