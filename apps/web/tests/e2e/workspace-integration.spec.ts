import { expect, test } from "@playwright/test";
import { createProject, register, startThread } from "./helpers";

test("stage navigator, kept context across tabs, preserved chat draft and the jobs menu", async ({
  page,
}) => {
  await register(page);
  await createProject(page, "Integrated workspace");
  await startThread(page);

  // The navigator shows real stage status from the server and jumps to the stage's panel.
  const nav = page.getByRole("navigation", { name: "Review stages" });
  await expect(nav.getByRole("button", { name: /Protocol/ })).toBeVisible();
  await expect(page.getByTestId("stage-detail")).toContainText("Protocol");
  await nav.getByRole("button", { name: /Report/ }).click();
  await expect(page).toHaveURL(/tab=report/);

  // An unsent chat draft survives switching panels and reloading this browser tab.
  const composer = page.getByLabel("Message", { exact: true });
  await composer.fill("Draft question about the criteria");
  await page.getByRole("tab", { name: "Papers" }).click();
  await page.getByRole("tab", { name: "Protocol" }).click();
  await expect(composer).toHaveValue("Draft question about the criteria");
  await expect(page.getByRole("contentinfo").getByText("Unsaved edits")).toBeVisible();
  page.once("dialog", (d) => void d.accept());
  await page.reload();
  await expect(page.getByLabel("Message", { exact: true })).toHaveValue("Draft question about the criteria");

  // Without a configured AI model the assistant button is disabled and says why.
  await expect(page.getByRole("button", { name: "Ask the assistant" })).toBeDisabled();

  // The jobs menu lists recent jobs with their state in words.
  const bar = page.getByRole("contentinfo");
  await bar.getByRole("button", { name: /^Jobs/ }).click();
  await expect(page.getByRole("dialog", { name: "Recent background jobs" })).toBeVisible();
});
