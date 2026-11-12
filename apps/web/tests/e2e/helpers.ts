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
  // A project's first thread starts the guided setup; later ones are free conversations.
  await page.getByRole("button", { name: /^(Start setting up the review|Start a thread)$/ }).click();
  await expect(page).toHaveURL(/[?&]c=[0-9a-f-]{36}/);
}

export async function sendMessage(page: Page, text: string) {
  await page.getByLabel("Message", { exact: true }).fill(text);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(messages(page).getByText(text, { exact: true })).toBeVisible();
}

/** Open a step's view (e.g. "Papers", "Evidence table") the way a keyboard user would: the command palette. */
export async function openStep(page: Page, label: string) {
  await page.keyboard.press("Control+k");
  const input = page.getByRole("combobox", { name: "Type a command or the name of a step" });
  await input.fill(label);
  // Scoped to the palette: hidden views can contain <select> options too.
  const options = page.getByRole("listbox", { name: "Commands" }).getByRole("option");
  await expect(options.first()).toHaveText(new RegExp(`^${label}`));
  await input.press("Enter");
  await expect(page.getByRole("tab", { name: label, exact: true })).toHaveAttribute("aria-selected", "true");
}

/** Open the right-hand "Review steps" panel (it is closed by default). */
export async function openStepsPanel(page: Page) {
  const toggle = page.getByRole("button", { name: /^(Steps|Review steps)$/ });
  if ((await toggle.getAttribute("aria-pressed")) !== "true") await toggle.click();
  return page.getByRole("complementary", { name: "Review steps" });
}

/** Run a command by its exact label from the command palette. */
export async function runCommand(page: Page, label: string) {
  await page.keyboard.press("Control+k");
  await page.getByRole("combobox", { name: "Type a command or the name of a step" }).fill(label);
  await page
    .getByRole("listbox", { name: "Commands" })
    .getByRole("option", { name: label, exact: true })
    .click();
}

export async function signOut(page: Page) {
  await page.getByRole("button", { name: /^Account:/ }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
}

/** The thread list lives behind the thread title in the chat header. */
export async function openThreads(page: Page) {
  await page.getByRole("button", { name: /switch thread/ }).click();
  return threads(page);
}
