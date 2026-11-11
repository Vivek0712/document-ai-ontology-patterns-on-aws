/**
 * Keyboard-only runs. SC-0 (the story tour): from Home, Tab to "Start the story", Enter, then the Player's keys
 * (Space, → and ←, R for receipts, Escape) to the last step. SC-5: → to the hop step, Tab to a field node of the hop
 * graph and open it in Trace with Enter. SC-11: step through one example, Tab to the example switcher, switch with
 * Enter (the step is kept), and step on. No mouse event is sent.
 */
import { expect, test } from "@playwright/test";
import { url } from "./routes";

test("SC-0 story tour, keyboard only", async ({ page }) => {
  await page.goto(url("/", { autoplay: "1" }));
  await expect(page.getByText("Start the story")).toBeVisible();

  // Tab until the hero link has focus (the shell's skip link, sidebar and top bar come first)
  const start = page.getByRole("link", { name: /Start the story/ });
  for (let i = 0; i < 60 && !(await start.evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press("Tab");
  await expect(start).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/showcase\/sc-0/);

  const caption = page.getByTestId("caption");
  await expect(caption).toBeVisible();
  // the tour autoplays: Tab to the transport's Pause button and press Enter
  const pause = page.getByRole("button", { name: "Pause" });
  for (let i = 0; i < 60 && !(await pause.evaluate((el) => el === document.activeElement).catch(() => false)); i++) await page.keyboard.press("Tab");
  await expect(pause).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Play" })).toBeVisible();
  const counter = page.getByTestId("step-counter");
  const total = Number((await counter.textContent())!.split("/")[1]);
  const seen = new Set<string>();
  for (let i = 0; i < total; i++) {
    seen.add((await caption.textContent()) ?? "");
    await page.keyboard.press("ArrowRight");
  }
  await expect(counter).toHaveText(`${total}/${total}`);
  expect(seen.size).toBe(total); // every step has its own caption
  await expect(page).toHaveURL(new RegExp(`step=${total}`));

  await page.keyboard.press("ArrowLeft");
  await expect(counter).toHaveText(`${total - 1}/${total}`);

  const receipts = page.getByRole("dialog", { name: "Receipts" });
  await page.keyboard.press("r");
  await expect(receipts).toBeVisible();
  await page.keyboard.press("r"); // R toggles
  await expect(receipts).toBeHidden();
  await page.keyboard.press("r");
  await expect(receipts).toBeVisible();
  // Escape dismisses the top layer: the focused button's tooltip first, then the drawer
  for (let i = 0; i < 3 && (await receipts.count()) > 0; i++) {
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
  }
  await expect(receipts).toBeHidden();
});

test("SC-5 hop graph, keyboard only: a field node is a named button that opens Trace", async ({ page }) => {
  await page.goto(url("/showcase/sc-5"));
  await expect(page.getByTestId("data-mode")).toHaveText(/mock/i);
  const counter = page.getByTestId("step-counter");
  await expect(counter).toHaveText(/^1\//);
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await expect(counter).toHaveText(/^4\//);
  const graph = page.getByRole("group", { name: /graph_hop .*nodes/ });
  await expect(graph).toBeVisible();
  const node = graph.getByRole("button", { name: /^Open field .* in Trace$/ }).first();
  await expect(node).toBeVisible();
  for (let i = 0; i < 80 && !(await node.evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press("Tab");
  await expect(node).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/workbench\/trace\//);
});

test("SC-11 before and after, keyboard only: step, switch example, keep the step", async ({ page }) => {
  await page.goto(url("/showcase/sc-11"));
  await expect(page.getByTestId("data-mode")).toHaveText(/mock/i);
  const counter = page.getByTestId("step-counter");
  const caption = page.getByTestId("caption");
  await expect(counter).toHaveText(/^1\//);
  const total = Number((await counter.textContent())!.split("/")[1]);
  for (let i = 1; i < 3; i++) await page.keyboard.press("ArrowRight");
  await expect(counter).toHaveText(`3/${total}`);
  await expect(caption).toHaveText("What the naive index handed the model.");

  // Tab to the second-lending example in the switcher and open it with Enter
  const target = page.locator('nav[aria-label="Example"] a[data-example="lending-02"]');
  for (let i = 0; i < 80 && !(await target.evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press("Tab");
  await expect(target).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/ex=lending-02/);
  await expect(page.locator('nav[aria-label="Example"] a[aria-current="page"]')).toHaveAttribute("data-example", "lending-02");
  await expect(counter).toHaveText(`3/${total}`);

  // back to the Player's keys: step to the end; the before caption is the payload's (a wrong total in every run)
  await page.keyboard.press("ArrowLeft");
  await expect(counter).toHaveText(`2/${total}`);
  await expect(caption).toHaveText(/^Naive pipeline: wrong in all \d+ logged runs\.$/);
  for (let i = 2; i < total; i++) await page.keyboard.press("ArrowRight");
  await expect(counter).toHaveText(`${total}/${total}`);
  await expect(page.getByTestId("diff-strip")).toBeVisible();
});
