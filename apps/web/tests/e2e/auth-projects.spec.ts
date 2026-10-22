import { expect, test } from "@playwright/test";
import { PASSWORD, register, uniqueEmail } from "./helpers";

test("signed-out visitors are redirected to login", async ({ page }) => {
  await page.goto("/projects");
  await expect(page).toHaveURL(/\/login(\?|$)/);
});

test("register, create project, reload, reopen, rename, archive, sign out, sign in again", async ({
  page,
}) => {
  const email = uniqueEmail();
  await register(page, email);

  await page.getByLabel("Title").fill("E2E review");
  await page.getByLabel("Research topic").fill("Does X improve Y?");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}/);
  await expect(page.getByRole("heading", { level: 1, name: "E2E review" })).toBeVisible();

  await page.reload(); // persistence across reload
  await expect(page.getByText("Does X improve Y?")).toBeVisible();

  await page.getByRole("button", { name: "Rename", exact: true }).click();
  await page.getByLabel("Project title").fill("Renamed review");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Renamed review" })).toBeVisible();

  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await expect(page.getByRole("link", { name: "Renamed review" })).toBeVisible();
  await page.getByRole("button", { name: "Archive" }).click();
  await expect(page.getByText("No projects yet")).toBeVisible();
  await page.getByLabel("Show archived").check();
  await expect(page.getByRole("link", { name: "Renamed review" })).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/projects");
  await expect(page).toHaveURL(/\/login\?next=%2Fprojects$/);

  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/projects$/);
  await page.getByLabel("Show archived").check();
  await expect(page.getByRole("link", { name: "Renamed review" })).toBeVisible();
});

test("wrong password shows a generic error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(uniqueEmail());
  await page.getByLabel("Password").fill("not-the-password-123");
  await page.getByRole("button", { name: "Sign in" }).click();
  // Scoped to <main>: Next.js injects its own role="alert" route announcer, so an unscoped locator matches two elements
  // (strict mode violation). The expected generic message is unchanged.
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Incorrect email or password");
});

test("another user cannot open my project by URL", async ({ browser }) => {
  const a = await (await browser.newContext()).newPage();
  await register(a, uniqueEmail());
  await a.getByLabel("Title").fill("Private A");
  await a.getByLabel("Research topic").fill("secret topic");
  await a.getByRole("button", { name: "Create project" }).click();
  await expect(a).toHaveURL(/\/projects\/[0-9a-f-]{36}/);
  const url = a.url();

  const b = await (await browser.newContext()).newPage();
  await register(b, uniqueEmail());
  await b.goto(url);
  await expect(b.getByRole("heading", { name: "Project not found" })).toBeVisible();
  await expect(b.getByText("secret topic")).toHaveCount(0);
});

test("password recovery is not advertised as working", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByText("Password recovery is not available yet")).toBeVisible();
  await expect(page.getByRole("link", { name: /forgot/i })).toHaveCount(0);
});
