/**
 * Playwright E2E spec for PDF upload and the Files tab.
 *
 * The compose stack must be running (`docker compose up`) before these run.
 * A Chromium browser is required; install it with `npx playwright install chromium`.
 *
 * Covered journeys:
 *   1. Upload a PDF by clicking the file picker → it appears with status "Waiting to be read"
 *      and is eventually "Text extracted" (the worker processes it).
 *   2. Dropping a non-PDF shows a client-side error immediately (no round-trip).
 *   3. Uploading the same file twice shows the server's duplicate message.
 *   4. An archived project disables the upload control.
 *   5. The extracted text is viewable page by page.
 *   6. A document can be removed; it disappears immediately and the usage counter drops.
 *   7. The sidebar shows newly uploaded files (live polling).
 *   8. Files belonging to one user are not reachable by another user (the server enforces this;
 *      the E2E test confirms the UI shows nothing rather than a crash).
 *
 * Tests skip if the compose stack is unavailable (the backend is not started).
 */
import { expect, test, type Page } from "@playwright/test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { createProject, newUserPage, PASSWORD, register, uniqueEmail } from "./helpers";

/* ---------- helpers ---------- */

async function skipIfStackDown(page: Page) {
  const ok = await page.request
    .get("/api/v1/healthz")
    .then(() => true)
    .catch(() => false);
  if (!ok) test.skip();
}

function makePdf(content = "A study about treatment outcomes in adults.") {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "e2e-pdf-"));
  const p = path.join(dir, "paper.pdf");
  // A genuine minimal PDF that PyMuPDF can parse.
  const body = Buffer.from(
    "%PDF-1.4\n" +
      "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n" +
      "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n" +
      `3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R/Resources<</Font<</F1<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>>>>>>>>\nendobj\n` +
      `4 0 obj<</Length ${content.length + 26}>>\nstream\nBT /F1 12 Tf 72 750 Td (${content}) Tj ET\nendstream\nendobj\n` +
      "xref\n0 5\n0000000000 65535 f\n0000000009 00000 n\n0000000058 00000 n\n0000000115 00000 n\n0000000310 00000 n\n" +
      `trailer<</Size 5/Root 1 0 R>>\nstartxref\n${390 + content.length}\n%%EOF\n`,
  );
  fs.writeFileSync(p, body);
  return { p, dir };
}

function cleanup(dir: string) {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch {
    /* best effort */
  }
}

async function goToFilesTab(page: Page) {
  await page.getByRole("tab", { name: "Files" }).click();
  await expect(page.getByRole("heading", { name: /Full-text PDFs/i })).toBeVisible();
}

/* ---------- tests ---------- */

test("upload a real PDF, wait for it to be read, view its text, then remove it", async ({ page }) => {
  await skipIfStackDown(page);
  await register(page);
  await createProject(page, "Upload test");
  await goToFilesTab(page);

  const { p, dir } = makePdf("Exercise reduces depressive symptoms in randomised participants.");
  try {
    const input = page.getByTestId("file-input");
    await input.setInputFiles(p);
    // The upload queue shows the file immediately.
    await expect(page.getByTestId("upload-queue").getByText("paper.pdf")).toBeVisible();
    await expect(page.getByTestId("upload-queue").getByText(/Uploaded/)).toBeVisible({ timeout: 30_000 });

    // The document card appears once the server has received the file.
    const card = page.getByTestId("document-card").first();
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card.getByTestId("doc-status")).toHaveText("Waiting to be read");

    // The worker reads it: poll until parsed (up to 60 s for a clean stack).
    await expect(card.getByTestId("doc-status")).toHaveText("Text extracted", { timeout: 60_000 });
    await expect(card.getByText(/pages have text/)).toBeVisible();

    // View the extracted text.
    await card.getByRole("button", { name: "View extracted text" }).click();
    await expect(page.getByTestId("page-viewer")).toBeVisible();
    await expect(page.getByTestId("page-viewer")).toContainText("randomised");

    // Check usage increased.
    await expect(page.getByTestId("usage")).toContainText(/1 of \d+ files/);

    // Remove it.
    await card.getByRole("button", { name: "Remove" }).click();
    await card.getByRole("button", { name: "Yes, remove" }).click();
    await expect(card).not.toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("No files uploaded yet")).toBeVisible();
    await expect(page.getByTestId("usage")).toContainText(/0 of \d+ files/);
  } finally {
    cleanup(dir);
  }
});

test("dropping a non-PDF shows an immediate client-side rejection without hitting the server", async ({
  page,
}) => {
  await skipIfStackDown(page);
  await register(page);
  await createProject(page, "Reject test");
  await goToFilesTab(page);

  // Use the file input to simulate a .docx being dropped (browser security prevents real drag-and-drop of local
  // files in Playwright; using the input is equivalent for our client-side check).
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "e2e-bad-"));
  const badFile = path.join(dir, "paper.docx");
  fs.writeFileSync(badFile, "not a pdf");
  try {
    await page.getByTestId("file-input").setInputFiles(badFile);
    const item = page.getByTestId("upload-queue").first();
    await expect(item.getByRole("alert")).toContainText(".pdf", { timeout: 5_000 });
    // Nothing was sent to the server.
    await expect(page.getByTestId("document-card")).not.toBeVisible();
  } finally {
    cleanup(dir);
  }
});

test("uploading the same PDF twice shows the server's duplicate error", async ({ page }) => {
  await skipIfStackDown(page);
  await register(page);
  await createProject(page, "Dupe test");
  await goToFilesTab(page);

  const { p, dir } = makePdf("A longitudinal study of medication adherence outcomes.");
  try {
    const input = page.getByTestId("file-input");
    await input.setInputFiles(p);
    await expect(page.getByTestId("upload-queue").getByText(/Uploaded/)).toBeVisible({ timeout: 30_000 });
    // Upload again.
    await input.setInputFiles(p);
    const items = page.getByTestId("upload-queue").getByRole("listitem");
    // One of the two items must show a "duplicate" or "already in the project" error.
    await expect(items.filter({ has: page.getByRole("alert") })).toHaveCount(1, { timeout: 10_000 });
    await expect(items.getByRole("alert")).toContainText(/already|duplicate/i);
  } finally {
    cleanup(dir);
  }
});

test("archived project disables the upload input", async ({ page }) => {
  await skipIfStackDown(page);
  await register(page);
  await createProject(page, "Archive test");

  // Save the workspace URL, then archive from the Projects list.
  const projectUrl = page.url();
  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await page.getByRole("button", { name: "Archive" }).click();

  // Return to the archived workspace and verify uploads are disabled.
  await page.goto(projectUrl);
  await expect(page.getByText("This project is archived")).toBeVisible();

  await goToFilesTab(page);
  await expect(page.getByTestId("file-input")).toBeDisabled();
  await expect(page.getByText("uploads are disabled")).toBeVisible();
});

test("newly uploaded file appears in the sidebar without reloading the page", async ({ page }) => {
  await skipIfStackDown(page);
  await register(page);
  await createProject(page, "Sidebar test");
  await goToFilesTab(page);

  const { p, dir } = makePdf("Sidebar file for a cohort study on lifestyle interventions.");
  try {
    await page.getByTestId("file-input").setInputFiles(p);
    await expect(page.getByTestId("upload-queue").getByText(/Uploaded/)).toBeVisible({ timeout: 30_000 });
    // The sidebar polls and should list the file without a page reload.
    await expect(page.getByTestId("sidebar-files")).toContainText("paper.pdf", { timeout: 15_000 });
  } finally {
    cleanup(dir);
  }
});

test("another user cannot access documents belonging to the first user", async ({ browser }) => {
  const pageA = await newUserPage(browser);
  const pageB = await newUserPage(browser);
  await skipIfStackDown(pageA);
  const { p, dir } = makePdf("Private study on confidential treatment outcomes.");
  try {
    await register(pageA);
    await createProject(pageA, "Private project");
    await goToFilesTab(pageA);
    await pageA.getByTestId("file-input").setInputFiles(p);
    await expect(pageA.getByTestId("upload-queue").getByText(/Uploaded/)).toBeVisible({ timeout: 30_000 });

    // Get the project URL from user A.
    const url = pageA.url();

    // User B (different browser context, different session) cannot reach the same project.
    await register(pageB);
    await pageB.goto(url);
    // The server returns 404; the middleware redirects to login if there is no session,
    // or to projects if the project is not found. Either way "Private project" is NOT shown.
    await expect(pageB.getByText("Private project")).not.toBeVisible({ timeout: 5_000 });
    // The Files tab API returns 404 too.
    const resp = await pageB.request.get(
      `/api/v1${new URL(url).pathname.replace("/projects/", "/projects/")}/documents`,
    );
    expect(resp.status()).toBe(404);
  } finally {
    cleanup(dir);
    await pageA.close();
    await pageB.close();
  }
});
