import { expect, test } from "@playwright/test";
import { createProject, register } from "./helpers";

test("audit log tab lists real project events, filters them and exports CSV", async ({ page }) => {
  await register(page);
  await createProject(page, "Audit journey");
  await page.getByRole("tab", { name: "Audit log" }).click();
  await expect(page).toHaveURL(/tab=audit/);
  const events = page.getByRole("list", { name: "Audit events" });
  await expect(events.getByText("project.created")).toBeVisible();
  await expect(events.getByText("You").first()).toBeVisible();

  await page.getByLabel("Category").selectOption("search");
  await expect(page.getByText("No events match these filters.")).toBeVisible();
  await page.getByLabel("Category").selectOption("");
  await page.getByLabel("Actor").selectOption("user");
  await expect(events.getByText("project.created")).toBeVisible();

  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "Export CSV" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^audit-.*\.csv$/);
});
