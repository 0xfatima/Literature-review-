import { expect, test } from "@playwright/test";
import { createProject, openStep, register } from "./helpers";

test("evidence table explains what is needed before extraction and loads from the real API", async ({
  page,
}) => {
  await register(page);
  await createProject(page, "Evidence table journey");
  await openStep(page, "Evidence table");
  // No approved schema yet: the panel says so instead of showing placeholder data.
  await expect(page.getByRole("heading", { name: "No evidence table yet" })).toBeVisible();
  await expect(page.getByText(/Approve an extraction schema in the Extraction tab/)).toBeVisible();
});
