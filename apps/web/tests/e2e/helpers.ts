import { expect, type Browser, type Page } from "@playwright/test";

export const PASSWORD = "a-long-e2e-password-1";
export const uniqueEmail = () => `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.test`;

export async function register(page: Page, email = uniqueEmail()): Promise<string> {
  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/projects$/);
  return email;
}

export async function signIn(page: Page, email: string) {
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
}

export async function createProject(page: Page, title: string, topic = "Does X improve Y?") {
  await page.getByLabel("Title").fill(title);
  await page.getByLabel("Research topic").fill(topic);
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}/);
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
}

export async function newUserPage(browser: Browser): Promise<Page> {
  return (await browser.newContext()).newPage();
}

export const messages = (page: Page) => page.getByRole("list", { name: "Messages" });
export const threads = (page: Page) => page.getByRole("list", { name: "Chat threads" });

export async function startThread(page: Page) {
  await page.getByRole("button", { name: "Start a thread" }).click();
  await expect(page).toHaveURL(/[?&]c=[0-9a-f-]{36}/);
}

export async function sendMessage(page: Page, text: string) {
  await page.getByLabel("Message", { exact: true }).fill(text);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(messages(page).getByText(text, { exact: true })).toBeVisible();
}
