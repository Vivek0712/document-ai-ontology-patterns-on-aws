/** Every route renders its content in mock mode, with no uncaught page error and no failed app request. */
import { expect, test } from "@playwright/test";
import { OTHER_PACKAGES, ROUTES, SHOWCASE_IDS, url } from "./routes";

for (const r of ROUTES) {
  test(`renders ${r.name}`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("response", (res) => {
      if (res.status() >= 500 && res.url().startsWith("http://localhost:5188")) errors.push(`${res.status()} ${res.url()}`);
    });
    await page.goto(url(r.path));
    await expect(page.locator(r.ready).first()).toBeVisible();
    // the suite runs on the mock world only: a server on 5188 in another mode fails here, not silently
    if (r.name !== "sign-in") await expect(page.getByTestId("data-mode")).toHaveText(/mock/i);
    // a showcase also shows its replay banner on every frame
    if (r.path.startsWith("/showcase/sc-")) await expect(page.getByTestId("replay-banner")).toBeVisible();
    expect(errors).toEqual([]);
  });
}

test("the brand is Provenant: tab titles carry it", async ({ page }) => {
  await page.goto(url("/"));
  await expect(page).toHaveTitle("Provenant");
  await page.goto(url("/knowledge/governed"));
  await expect(page).toHaveTitle("Governed meaning · Provenant");
  await page.goto(url("/showcase/sc-8?step=1"));
  await expect(page).toHaveTitle(/^SC-8 Governed meaning · Provenant$/);
  await expect(page.locator("body")).not.toContainText("Regionproof");
});

test("W-10 shows COA's recorded cross-check, tagged as a dated snapshot, never as pending", async ({ page }) => {
  await page.goto(url("/knowledge/governed?tab=metric"));
  const row = page.getByTestId("coa-crosscheck");
  await expect(row).toBeVisible();
  await expect(row).toHaveAttribute("data-coa-crosscheck", "match");
  await expect(row).toContainText("COA governed metric · Tier 1 · confidence 1.0");
  for (const chip of ["metric_match", "authorize (cedar)", "firewall", "execute (athena)"]) await expect(row).toContainText(chip);
  await expect(page.getByText(/^COA snapshot · captured \d{4}-\d{2}-\d{2}$/).first()).toBeVisible();
  await expect(page.locator("main")).not.toContainText(/pending/i);
  await expect(page.locator("main")).toContainText("uploaded, not steward-reviewed");
});

test("no showcase or screen says it is coming next", async ({ page }) => {
  for (const path of ["/", "/showcase", "/knowledge/ontology", "/governance/access", "/evaluate", "/cost"]) {
    await page.goto(url(path));
    await expect(page.locator("main h1").first()).toBeVisible();
    await expect(page.locator("main")).not.toContainText(/coming next/i);
  }
});

test("a showcase deep link opens at its step, and every showcase opens at its last step", async ({ page }) => {
  for (const id of SHOWCASE_IDS) {
    await page.goto(url(`/showcase/${id}?step=1`));
    const counter = page.getByTestId("step-counter");
    await expect(counter).toHaveText(/^1\//);
    const total = Number((await counter.textContent())!.split("/")[1]);
    await page.goto(url(`/showcase/${id}?step=${total}`));
    await expect(counter).toHaveText(`${total}/${total}`);
  }
});

test("the package switcher loads each package lazily and the screens follow it", async ({ page }) => {
  const shards: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/mock/packages/")) shards.push(req.url().split("/mock/packages/")[1]);
  });
  for (const pkg of OTHER_PACKAGES) {
    await page.goto(url(`/workbench/packages/${pkg}`));
    await expect(page.locator("main h1").first()).toBeVisible();
    // the top bar names the package now in use; the PII screen and the cost screen follow it
    await page.goto(url("/governance/pii"));
    await expect(page.locator("figure[data-body-height]")).toBeVisible();
    await page.goto(url("/cost"));
    await expect(page.getByTestId("cost-ledger")).toBeVisible();
    await expect(page.getByTestId("cost-ledger")).toContainText(pkg.includes("lending") ? "w2-" : pkg.includes("quarterly") ? "quarterly-report" : "trade-confirmation");
  }
  for (const pkg of OTHER_PACKAGES) expect(shards.filter((s) => s === `${pkg}.json`).length, `${pkg} shard fetches`).toBeGreaterThan(0);
});

test("W-14: a review decision is recorded with its history; CORRECT keeps the extracted value", async ({ page }) => {
  await page.goto(url("/governance/review"));
  const row = page.getByTestId("review-queue").locator("tbody tr").first();
  await row.locator("button").first().click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await drawer.getByLabel(/Corrected value/).fill("4,900.00");
  await drawer.getByRole("button", { name: "Correct" }).click();
  await expect(drawer.getByTestId("review-history")).toContainText("override 4,900.00");
  await expect(drawer).toContainText("Extracted");
  await expect(page.getByTestId("review-queue")).toContainText("corrected");
});

test("W-13: Try as B sends the request with User B's session and shows the API's refusal", async ({ page }) => {
  await page.goto(url("/governance/access"));
  await expect(page.getByTestId("access-decisions")).toBeVisible();
  await page.getByRole("button", { name: "Try as B" }).click();
  await expect(page.getByTestId("try-as-b")).toContainText("403");
});
