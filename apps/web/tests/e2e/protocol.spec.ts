import { expect, test, type Page } from "@playwright/test";
import { createProject, openStep, openStepsPanel, register, signIn, signOut } from "./helpers";

test.setTimeout(120_000);

// Protocol questions are asked only in the chat; the Protocol view links back to them.
const chat = (page: Page) => page.getByRole("list", { name: "Messages" });
const card = (page: Page, kind?: string) =>
  kind
    ? chat(page).locator(`[data-testid="question-card"][data-kind="${kind}"]`)
    : chat(page).getByTestId("question-card");

async function nextCard(page: Page, kind: string) {
  await expect(card(page, kind)).toBeVisible({ timeout: 20_000 });
  return card(page, kind);
}

test("protocol: set up, leave mid-way, return to the same step, approve, revise with impact", async ({
  page,
}) => {
  const email = await register(page);
  await createProject(
    page,
    "Exercise and late-life depression",
    "Effectiveness of aerobic exercise compared with usual care for depression in adults over 60",
  );

  await openStep(page, "Protocol");
  await page.getByRole("button", { name: "Start protocol setup" }).click();
  await expect(page.getByRole("button", { name: /Protocol setup.*switch thread/ })).toBeVisible();
  // While setup runs, the Protocol view points to the chat instead of repeating the questions.
  await expect(page.getByRole("button", { name: "Continue in chat" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("tabpanel").getByTestId("question-card")).toHaveCount(0);

  let c = await nextCard(page, "objective");
  await c.getByLabel("Objective").fill("Estimate the effect of aerobic exercise on depressive symptoms.");
  await c.getByRole("button", { name: "Save and continue" }).click();

  c = await nextCard(page, "framework");
  await expect(c.getByText("Not an AI judgement")).toBeVisible();
  await c.getByRole("button", { name: "Use PICO" }).click();

  c = await nextCard(page, "elements");
  await c.getByLabel("Population").fill("adults over 60 with depression");
  await c.getByLabel("Intervention").fill("aerobic exercise");
  await c.getByLabel("Outcome").fill("depressive symptoms");
  await c.getByRole("button", { name: "Save and continue" }).click();

  c = await nextCard(page, "questions");
  await expect(c.getByLabel("Review question 1")).toHaveValue(/what is the effect of aerobic exercise/);
  await c.getByRole("button", { name: "Save and continue" }).click();

  // Leave and come back: the same step is waiting (state is in the database and the checkpoint, not the page).
  c = await nextCard(page, "scope");
  const url = page.url();
  await signOut(page);
  await expect(page).toHaveURL(/\/login$/);
  await page.goto(url);
  await signIn(page, email);
  await expect(page).toHaveURL(url);
  c = await nextCard(page, "scope");

  await c.getByLabel("From year").fill("2000");
  await c.getByLabel("To year").fill("2025");
  await c.getByLabel("Languages").fill("English");
  await c.getByLabel("Sources to search").fill("OpenAlex, Europe PMC");
  await c.getByRole("button", { name: "Save and continue" }).click();

  c = await nextCard(page, "criteria");
  const criteria = c.getByRole("list", { name: "Criteria" });
  await expect(criteria.getByRole("listitem").first()).toBeVisible();
  const n = await criteria.getByRole("listitem").count();
  for (let i = 0; i < n; i++)
    await criteria.getByRole("listitem").nth(i).getByRole("button", { name: "Accept" }).click();
  await c.getByRole("button", { name: "Save and continue" }).click();

  c = await nextCard(page, "review");
  await expect(c.getByLabel("Protocol version 1 summary")).toContainText(
    "Population: adults over 60 with depression",
  );
  await c.getByRole("button", { name: "Approve protocol version 1" }).click();
  await expect(card(page)).toHaveCount(0, { timeout: 20_000 });

  // Correct stage after approval, also after a reload.
  await page.reload();
  const steps = await openStepsPanel(page);
  await expect(steps.getByRole("button", { name: /^Protocol.*Version 1 approved/ })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByTestId("current-activity")).toContainText("Nothing running");

  // Revise: reason required, new version, approved version untouched, impact shown.
  await steps.getByRole("button", { name: /^Protocol.*Version 1 approved/ }).click();
  await expect(page.getByRole("tab", { name: "Protocol", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("heading", { name: "Approved protocol, version 1" })).toBeVisible();
  await page.getByLabel("Reason for revising the protocol").fill("Peer reviewer asked for adults 65+");
  await page.getByRole("button", { name: "Start a revision" }).click();
  await expect(page.getByRole("heading", { name: "Draft protocol, version 2" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText("Reason for this revision: Peer reviewer asked for adults 65+")).toBeVisible();

  const edit = page.getByRole("form", { name: "Edit protocol version 2" });
  await edit.getByLabel("Criterion 1", { exact: true }).fill("Population: adults aged 65 and over");
  await edit.getByRole("button", { name: "Save protocol draft" }).click();
  await expect(edit.getByText("Saved")).toBeVisible();

  // Approval happens in the chat; the Protocol view says so and links there.
  await expect(page.getByRole("button", { name: "Review in chat" })).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Review in chat" }).click();
  const approve = card(page, "review").getByRole("button", { name: "Approve protocol version 2" });
  await expect(approve).toBeEnabled({ timeout: 20_000 });
  await approve.click();
  await expect(page.getByRole("heading", { name: "Approved protocol, version 2" })).toBeVisible({
    timeout: 20_000,
  });

  const history = page.getByRole("list", { name: "Protocol versions" });
  await expect(history.getByRole("listitem")).toHaveCount(2);
  await history.getByRole("button", { name: /Version 2: approved/ }).click();
  await expect(page.getByTestId("impact")).toContainText("Record screening");
  await expect(page.getByTestId("impact")).toContainText(
    "No search, screening or extraction results exist yet",
  );
  await history.getByRole("button", { name: /Version 1: superseded/ }).click();
  await expect(page.getByLabel("Protocol version 1 summary")).toContainText("adults over 60 with depression");
});
