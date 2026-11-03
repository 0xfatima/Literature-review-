import { expect, test } from "@playwright/test";
import { createProject, openStep, register } from "./helpers";

test("pipeline tab shows the autonomy policy and starts a real run that stops for the protocol", async ({
  page,
}) => {
  await register(page);
  await createProject(page, "Pipeline journey");
  await openStep(page, "Automation");
  await expect(page).toHaveURL(/tab=autopilot/);
  const panel = page.getByTestId("autopilot-panel");
  await expect(panel.getByText(/always stops for your judgement/)).toBeVisible();
  // AI extraction is off by default: it is never started without asking.
  await expect(panel.getByLabel(/Start AI evidence extraction/)).not.toBeChecked();
  await panel.getByRole("button", { name: "Start the pipeline" }).click();
  // The worker advances the run to its first human checkpoint: the protocol is not approved yet.
  await expect(panel.getByTestId("pipeline-approval")).toContainText(/Approve the review protocol/, {
    timeout: 30_000,
  });
  await expect(panel.getByRole("button", { name: "Stop the run" })).toBeVisible();
});
