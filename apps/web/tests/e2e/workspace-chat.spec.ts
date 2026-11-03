import { expect, test } from "@playwright/test";
import {
  createProject,
  messages,
  newUserPage,
  openStep,
  openStepsPanel,
  openThreads,
  register,
  runCommand,
  sendMessage,
  signIn,
  signOut,
  startThread,
} from "./helpers";

/** The thread title in the chat header doubles as the thread switcher. */
const threadTitle = (page: import("@playwright/test").Page, name: RegExp) =>
  page.getByRole("button", { name: new RegExp(`${name.source}.*switch thread`) });

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

  // The first message names the thread (chat header and thread list).
  await expect(threadTitle(page, /Does aerobic exercise reduce/)).toBeVisible();
  await expect(
    (await openThreads(page)).getByRole("button", { name: /Does aerobic exercise reduce/ }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  const threadUrl = page.url();

  await page.reload();
  await expect(messages(page).getByRole("listitem")).toHaveCount(2);
  await expect(messages(page).getByText("Focus on adults over 60.")).toBeVisible();
  await expect(page).toHaveURL(threadUrl);

  await signOut(page);
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
  const list = await openThreads(page);
  await expect(list.getByRole("listitem")).toHaveCount(2);
  await list.getByRole("button", { name: /First thread message/ }).click();
  await expect(messages(page).getByText("First thread message")).toBeVisible();
  await expect(messages(page).getByText("Second thread message")).toHaveCount(0);

  // Rare actions live in the thread's "⋯" menu.
  await page.getByRole("button", { name: "Thread actions" }).click();
  await page.getByRole("menuitem", { name: "Rename thread" }).click();
  await page.getByLabel("Thread title").fill("Eligibility criteria");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(threadTitle(page, /Eligibility criteria/)).toBeVisible();

  await page.getByRole("button", { name: "Thread actions" }).click();
  await page.getByRole("menuitem", { name: "Delete thread" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete thread" }).click();
  const after = await openThreads(page);
  await expect(after.getByRole("listitem")).toHaveCount(1);
  await expect(after.getByRole("button", { name: /Eligibility criteria/ })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(messages(page).getByText("Second thread message")).toBeVisible(); // falls back to the remaining one
});

test("each project keeps its own threads when switching through the breadcrumb", async ({ page }) => {
  await register(page);
  await createProject(page, "Project Alpha");
  await startThread(page);
  await sendMessage(page, "Alpha only");
  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await createProject(page, "Project Beta");

  await expect(page.getByRole("button", { name: "Start a thread" })).toBeVisible(); // Beta has no threads

  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await page.getByRole("link", { name: "Project Alpha" }).click();
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

test("steps open beside the chat, panels resize from the keyboard, and the layout is remembered", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  await register(page);
  await createProject(page, "Layout");
  const stepsPanel = page.getByRole("complementary", { name: "Review steps" });

  // The steps panel is opt-in: closed until the person opens it.
  await expect(stepsPanel).toHaveCount(0);
  await openStepsPanel(page);
  await expect(
    stepsPanel.getByRole("list", { name: "Review steps" }).getByRole("listitem").first(),
  ).toContainText("Setup");

  // No step open: the chat is the whole workspace, so there is nothing to resize.
  await expect(page.getByRole("separator", { name: "Resize chat" })).toHaveCount(0);
  await openStep(page, "Papers");
  const chatSplitter = page.getByRole("separator", { name: "Resize chat" });
  const before = Number(await chatSplitter.getAttribute("aria-valuenow"));
  await chatSplitter.focus();
  for (let i = 0; i < 3; i++) await page.keyboard.press("ArrowRight");
  await expect(chatSplitter).toHaveAttribute("aria-valuenow", String(before + 48));

  await page.reload();
  await expect(page.getByRole("separator", { name: "Resize chat" })).toHaveAttribute(
    "aria-valuenow",
    String(before + 48),
  );
  await expect(stepsPanel).toBeVisible();

  // The chat can step aside for the open step, and closing the last step brings the chat back full width.
  await page.getByRole("button", { name: "Hide chat" }).click();
  await expect(page.getByRole("region", { name: "Chat" })).toHaveCount(0);
  await page.getByRole("button", { name: "Show chat" }).click();
  await expect(page.getByRole("region", { name: "Chat" })).toBeVisible();
  await page.getByRole("tab", { name: "Papers", exact: true }).press("Delete");
  await expect(page.getByRole("tab")).toHaveCount(0);
  await expect(page).not.toHaveURL(/tab=/);
});

test("open steps keep their place in the URL and empty steps say what comes next", async ({ page }) => {
  await register(page);
  await createProject(page, "Tabs");
  await openStep(page, "Papers");
  await expect(page).toHaveURL(/tab=papers/);
  await expect(page.getByRole("heading", { name: "No papers yet" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("tab", { name: "Papers", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await openStep(page, "Files");
  await expect(page).toHaveURL(/tab=files/);
  await page.getByRole("tab", { name: "Files", exact: true }).press("ArrowLeft");
  await expect(page.getByRole("tab", { name: "Papers", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page).toHaveURL(/tab=papers/);
});

test("a background job shows live progress at the top right and notifies when it finishes", async ({
  page,
}) => {
  await register(page);
  await createProject(page, "Jobs");
  const activity = page.getByTestId("current-activity");
  await expect(activity).toContainText("Nothing running");
  await runCommand(page, "Check the background worker");
  await expect(activity).toContainText("Worker check running");
  await activity.click();
  await expect(page.getByRole("dialog", { name: "Review activity" }).getByTestId("active-job")).toContainText(
    "Worker check",
  );
  await page.keyboard.press("Escape");
  await expect(activity).toContainText("Nothing running", { timeout: 20_000 });
  await page.getByRole("button", { name: /^Notifications, \d+ unread$/ }).click();
  await expect(page.getByRole("dialog", { name: "Notifications" })).toContainText("Worker check completed");
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
