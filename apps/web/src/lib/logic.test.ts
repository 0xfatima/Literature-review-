import { describe, expect, it } from "vitest";
import { mergeMessages, roleLabel, settlePending } from "./chat";
import { CHAT, DEFAULT_LAYOUT, SIDEBAR, clamp, keyResize, parseLayout } from "./layout";
import { safeNext } from "./nav";
import { STAGES, stageIndex, stageLabel } from "./stages";
import type { Message } from "./types";

describe("safeNext", () => {
  it("keeps same-origin app paths", () => {
    expect(safeNext("/projects/abc?c=1")).toBe("/projects/abc?c=1");
  });
  it.each([
    null,
    "",
    "https://evil.example",
    "//evil.example",
    "/\\evil.example",
    "javascript:alert(1)",
    "/login",
    "/register?x",
    "/x\u0000y",
  ])("rejects %j", (raw) => expect(safeNext(raw)).toBe("/projects"));
});

describe("layout", () => {
  it("clamps sizes into bounds and survives NaN", () => {
    expect(clamp(10, SIDEBAR)).toBe(SIDEBAR.min);
    expect(clamp(9999, CHAT)).toBe(CHAT.max);
    expect(clamp(Number.NaN, CHAT)).toBe(CHAT.initial);
  });
  it("parses stored layout defensively", () => {
    expect(parseLayout(null)).toEqual(DEFAULT_LAYOUT);
    expect(parseLayout("{not json")).toEqual(DEFAULT_LAYOUT);
    expect(parseLayout(JSON.stringify({ sidebar: 5000, chat: "x", sidebarOpen: false }))).toEqual({
      sidebar: SIDEBAR.max,
      chat: CHAT.initial,
      sidebarOpen: false,
      chatOpen: true,
    });
  });
  it("resizes by keyboard and ignores other keys", () => {
    expect(keyResize("ArrowRight", 300, CHAT)).toBe(316);
    expect(keyResize("ArrowLeft", CHAT.min, CHAT)).toBe(CHAT.min);
    expect(keyResize("Home", 500, CHAT)).toBe(CHAT.min);
    expect(keyResize("End", 300, CHAT)).toBe(CHAT.max);
    expect(keyResize("a", 300, CHAT)).toBeNull();
  });
});

const msg = (seq: number, client: string | null = null): Message => ({
  id: `m${seq}`,
  conversation_id: "c",
  seq,
  role: "user",
  author_id: null,
  content: `m${seq}`,
  payload: null,
  client_message_id: client,
  created_at: "2026-01-01T00:00:00Z",
});

describe("chat helpers", () => {
  it("merges pages by seq without duplicates, in order", () => {
    expect(mergeMessages([msg(3), msg(1)], [msg(2), msg(3)]).map((m) => m.seq)).toEqual([1, 2, 3]);
  });
  it("settles only the pending sends the server confirmed", () => {
    const pending = [
      { clientId: "a", content: "x", state: "sending" as const },
      { clientId: "b", content: "y", state: "failed" as const },
    ];
    expect(settlePending(pending, [msg(1, "a")]).map((p) => p.clientId)).toEqual(["b"]);
  });
  it("labels roles in words, never colour alone", () => {
    expect([roleLabel("user"), roleLabel("assistant"), roleLabel("system")]).toEqual([
      "You",
      "Assistant",
      "System",
    ]);
  });
});

describe("stages", () => {
  it("covers every backend pipeline status and keeps needs_attention outside the track", () => {
    expect(STAGES.map((s) => s.id)).toEqual([
      "setup",
      "searching",
      "screening",
      "awaiting_pdfs",
      "extracting",
      "synthesizing",
      "draft_ready",
      "completed",
    ]);
    expect(stageIndex("needs_attention")).toBe(-1);
    expect(stageLabel("needs_attention")).toBe("Needs attention");
    expect(stageLabel("awaiting_pdfs")).toBe("Awaiting PDFs");
  });
});
