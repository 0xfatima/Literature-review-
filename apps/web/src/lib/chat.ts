import type { Message } from "./types";

/** A send that has not been confirmed by the server yet. The client id is reused on retry so the
 *  backend stores the message exactly once (idempotent POST). */
export interface PendingSend {
  clientId: string;
  content: string;
  state: "sending" | "failed";
  error?: string;
}

/** Merge pages of messages by seq, dropping duplicates (polling and optimistic replies can overlap). */
export function mergeMessages(...lists: Message[][]): Message[] {
  const bySeq = new Map<number, Message>();
  for (const list of lists) for (const m of list) bySeq.set(m.seq, m);
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

/** Drop pending sends the server has confirmed (matched by client_message_id). */
export function settlePending(pending: PendingSend[], confirmed: Message[]): PendingSend[] {
  const done = new Set(confirmed.map((m) => m.client_message_id).filter(Boolean));
  return pending.filter((p) => !done.has(p.clientId));
}

export function roleLabel(role: string): string {
  return role === "user" ? "You" : role === "assistant" ? "Assistant" : "System";
}

export function newClientId(): string {
  return globalThis.crypto.randomUUID();
}
