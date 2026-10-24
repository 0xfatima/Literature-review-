import { expect, test } from "@playwright/test";
import {
  createProject,
  messages,
  newUserPage,
  register,
  sendMessage,
  signIn,
  startThread,
  threads,
} from "./helpers";

test("chat history persists across reload and sign-out, and sign-in returns to the same thread", async ({
  page,
}) => {
  const email = await register(page);
  await createProject(page, "Exercise and depression");
  await startThread(page);

  await page.getByLabel("Message", { exact: true }).fill("Does aerobic exercise reduce depressive symptoms?");
  await page.keyboard.press("Enter"); // Enter sends
  await expect(messages(page).getByText("Does aerobic exercise reduce depressive symptoms?")).toBeVisible();
  await sendMessage(page, "Focus on adults over 60.");

  // The first message names the thread (sidebar and chat header).
  await expect(threads(page).getByRole("button", { name: /Does aerobic exercise reduce/ })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: /Does aerobic exercise reduce/ })).toBeVisible();
  const threadUrl = page.url();

  await page.reload();
  await expect(messages(page).getByRole("listitem")).toHaveCount(2);
  await expect(messages(page).getByText("Focus on adults over 60.")).toBeVisible();
  await expect(page).toHaveURL(threadUrl);

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto(threadUrl); // deep link while signed out
  await expect(page).toHaveURL(/\/login\?next=/);
  await signIn(page, email);
  await expect(page).toHaveURL(threadUrl); // back where we were
  await expect(messages(page).getByRole("listitem")).toHaveCount(2);
});

test("threads can be created, switched, renamed and deleted", async ({ page }) => {
  await register(page);
  await createProject(page, "Thread management");
  await startThread(page);
  await sendMessage(page, "First thread message");

  await page.getByRole("button", { name: "New thread" }).click();
  await expect(messages(page).getByRole("listitem")).toHaveCount(0);
  await sendMessage(page, "Second thread message");
  await expect(threads(page).getByRole("listitem")).toHaveCount(2);

  await threads(page)
    .getByRole("button", { name: /First thread message/ })
    .click();
  await expect(messages(page).getByText("First thread message")).toBeVisible();
  await expect(messages(page).getByText("Second thread message")).toHaveCount(0);

  await page.getByRole("button", { name: "Rename thread" }).click();
  await page.getByLabel("Thread title").fill("Eligibility criteria");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Eligibility criteria" })).toBeVisible();
  await expect(threads(page).getByRole("button", { name: /Eligibility criteria/ })).toBeVisible();

  await page.getByRole("button", { name: "Delete thread", exact: true }).click();
  await page.getByRole("button", { name: "Delete thread and messages" }).click();
  await expect(threads(page).getByRole("listitem")).toHaveCount(1);
  await expect(threads(page).getByRole("button", { name: /Eligibility criteria/ })).toHaveCount(0);
  await expect(messages(page).getByText("Second thread message")).toBeVisible(); // falls back to the remaining one
});

test("the sidebar switches between projects and keeps each project's own threads", async ({ page }) => {
  await register(page);
  await createProject(page, "Project Alpha");
  await startThread(page);
  await sendMessage(page, "Alpha only");
  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await createProject(page, "Project Beta");

  const switcher = page.getByRole("list", { name: "Switch project" });
  await expect(switcher.getByRole("link", { name: "Project Beta" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("button", { name: "Start a thread" })).toBeVisible(); // Beta has no threads

  await switcher.getByRole("link", { name: "Project Alpha" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Project Alpha" })).toBeVisible();
  await expect(messages(page).getByText("Alpha only")).toBeVisible();
});

test("another user cannot read my chat thread, by page or by API", async ({ browser }) => {
  const a = await newUserPage(browser);
  await register(a);
  await createProject(a, "Private review");
  await startThread(a);
  await sendMessage(a, "confidential chat line");
  const url = a.url();
  const [, pid, cid] = url.match(/projects\/([0-9a-f-]{36})\?.*c=([0-9a-f-]{36})/) ?? [];
  expect(pid && cid).toBeTruthy();

  const b = await newUserPage(browser);
  await register(b);
  await b.goto(url);
  await expect(b.getByRole("heading", { name: "Project not found" })).toBeVisible();
  await expect(b.getByText("confidential chat line")).toHaveCount(0);

  const api = await b.request.get(`/api/v1/projects/${pid}/conversations/${cid}/messages`);
  expect(api.status()).toBe(404);
  expect(await api.text()).not.toContain("confidential");
  const post = await b.request.post(`/api/v1/projects/${pid}/conversations/${cid}/messages`, {
    data: { content: "intrusion" },
    headers: { "X-SRS-CSRF": "1" },
  });
  expect(post.status()).toBe(404);

  await a.reload();
  await expect(messages(a).getByRole("listitem")).toHaveCount(1); // nothing was injected
});

test("panels resize from the keyboard, collapse, and remember their layout", async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  await register(page);
  await createProject(page, "Layout");

  const chatSplitter = page.getByRole("separator", { name: "Resize chat" });
  const before = Number(await chatSplitter.getAttribute("aria-valuenow"));
  await chatSplitter.focus();
  for (let i = 0; i < 3; i++) await page.keyboard.press("ArrowRight");
  await expect(chatSplitter).toHaveAttribute("aria-valuenow", String(before + 48));

  await page.getByRole("button", { name: "Hide sidebar" }).click();
  await expect(page.getByRole("navigation", { name: "Project explorer" })).toHaveCount(0);

  await page.reload();
  await expect(page.getByRole("separator", { name: "Resize chat" })).toHaveAttribute(
    "aria-valuenow",
    String(before + 48),
  );
  await expect(page.getByRole("navigation", { name: "Project explorer" })).toHaveCount(0);
  await page.getByRole("button", { name: "Show sidebar" }).click();
  await expect(page.getByRole("navigation", { name: "Project explorer" })).toBeVisible();
});

test("workspace tabs keep their place in the URL and empty tabs say what comes next", async ({ page }) => {
  await register(page);
  await createProject(page, "Tabs");
  await page.getByRole("tab", { name: "Papers" }).click();
  await expect(page).toHaveURL(/tab=papers/);
  await expect(page.getByRole("heading", { name: "No papers yet" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("tab", { name: "Papers" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "Papers" }).press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Files" })).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/tab=files/);
  });

test("a background job shows live progress in the status bar until it finishes", async ({ page }) => {
  await register(page);
  await createProject(page, "Jobs");
  const bar = page.getByRole("contentinfo");
  await expect(bar.getByText("No background jobs running")).toBeVisible();
  await page.getByRole("button", { name: "Check background worker" }).click();
  await expect(bar.getByTestId("active-job")).toContainText("Worker check");
  await expect(bar.getByText("No background jobs running")).toBeVisible({ timeout: 20_000 });
});

test("an archived project keeps its history readable but read-only", async ({ page }) => {
  await register(page);
  await createProject(page, "To archive");
  await startThread(page);
  await sendMessage(page, "kept after archiving");
  const url = page.url();
  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await page.getByRole("button", { name: "Archive" }).click();
  await page.goto(url);
  await expect(messages(page).getByText("kept after archiving")).toBeVisible();
  await expect(page.getByLabel("Message", { exact: true })).toBeDisabled();
  await expect(page.getByRole("contentinfo").getByText("Archived, read-only")).toBeVisible();
});
