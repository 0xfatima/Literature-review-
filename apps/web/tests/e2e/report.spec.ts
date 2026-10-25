import { expect, test } from "@playwright/test";
import { createProject, register } from "./helpers";

test("report tab offers generation from the real API and refuses without an approved protocol", async ({
  page,
}) => {
  await register(page);
  await createProject(page, "Report journey");
  await page.getByRole("tab", { name: "Report" }).click();
  await expect(page).toHaveURL(/tab=report/);
  await expect(page.getByRole("heading", { name: "No report draft yet" })).toBeVisible();
  await page.getByRole("button", { name: "Generate draft" }).click();
  // The protocol is not approved, so the server refuses and the status bar shows its message.
  await expect(page.getByText(/Approve the review protocol before generating a report/)).toBeVisible();
});
