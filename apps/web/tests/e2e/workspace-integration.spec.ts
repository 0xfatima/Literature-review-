import { expect, test } from "@playwright/test";
import { createProject, openStep, openStepsPanel, register, startThread } from "./helpers";

test("steps panel, kept context across open steps, preserved chat draft and the activity panel", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  await register(page);
  await createProject(page, "Integrated workspace");
  await startThread(page);

  // One indicator says where the review is: step 1, Setup, for a new project.
  await expect(page.getByTestId("current-activity")).toContainText("Setup");

  // The steps panel lists the eight main steps vertically, the current one marked, with real status
  // from the server on its sub-steps. Choosing a sub-step opens it beside the chat.
  const steps = await openStepsPanel(page);
  const list = steps.getByRole("list", { name: "Review steps" });
  await expect(list.locator(":scope > li")).toHaveCount(8);
  await expect(list.locator('[aria-current="step"]')).toContainText("Setup");
  await expect(steps.getByRole("button", { name: /^Protocol/ })).toContainText("Set up the review question");
  await steps.getByRole("button", { name: /^Draft ready/ }).click();
  await steps.getByRole("button", { name: /^Report/ }).click();
  await expect(page).toHaveURL(/tab=report/);

  // An unsent chat draft survives switching steps and reloading this browser tab.
  const composer = page.getByLabel("Message", { exact: true });
  await composer.fill("Draft question about the criteria");
  await openStep(page, "Papers");
  await openStep(page, "Protocol");
  await expect(composer).toHaveValue("Draft question about the criteria");
  await expect(page.getByRole("contentinfo").getByText("Unsaved edits")).toBeVisible();
  page.once("dialog", (d) => void d.accept());
  await page.reload();
  await expect(page.getByLabel("Message", { exact: true })).toHaveValue("Draft question about the criteria");

  // Without a configured AI model there is no "Ask AI" switch, and the chat says why. One Send button.
  await expect(page.getByRole("switch", { name: /Ask AI/ })).toHaveCount(0);
  await expect(page.getByText("No AI assistant is configured on this server.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Send message" })).toBeVisible();

  // The activity panel at the top right lists running and recent jobs with their state in words.
  await page.getByTestId("current-activity").click();
  const activity = page.getByRole("dialog", { name: "Review activity" });
  await expect(activity).toBeVisible();
  await expect(activity.getByText("Recent jobs")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(activity).toHaveCount(0);
});
