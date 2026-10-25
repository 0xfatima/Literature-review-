import { expect, test } from "@playwright/test";
import { createProject, register } from "./helpers";

test("appraisal and synthesis tabs explain their prerequisites using the real API", async ({ page }) => {
  await register(page);
  await createProject(page, "Synthesis journey");
  await page.getByRole("tab", { name: "Appraisal" }).click();
  await expect(page).toHaveURL(/tab=appraisal/);
  await expect(page.getByRole("heading", { name: "No studies to appraise yet" })).toBeVisible();
  await page.getByRole("tab", { name: "Synthesis" }).click();
  await expect(page.getByRole("heading", { name: "Nothing to synthesise yet" })).toBeVisible();
  await expect(page.getByText(/reviewed values only/)).toBeVisible();
});
