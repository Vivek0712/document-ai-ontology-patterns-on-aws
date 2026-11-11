/**
 * axe-core on every page in both themes: zero serious or critical violations (WCAG 2.1 A/AA rules). Showcases are
 * scanned at a middle step and at their last step, with the receipts drawer closed.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { ROUTES, SC11_EXAMPLES, SHOWCASE_IDS, url } from "./routes";

const PAGES = [
  ...ROUTES.filter((r) => !r.path.startsWith("/showcase/sc-")),
  ...SC11_EXAMPLES.slice(1).map((ex) => ({ name: `sc-11-${ex}-why`, path: `/showcase/sc-11?step=3&ex=${ex}`, ready: "[data-testid=caption]" })),
  ...SHOWCASE_IDS.flatMap((id) => [
    { name: `${id}-mid`, path: `/showcase/${id}?step=3`, ready: "[data-testid=caption]" },
    { name: `${id}-end`, path: `/showcase/${id}?step=99`, ready: "[data-testid=caption]" },
  ]),
];

for (const theme of ["dark", "light"] as const) {
  for (const r of PAGES) {
    test(`axe ${theme} ${r.name}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      await page.goto(url(r.path, { theme }));
      await expect(page.locator(r.ready).first()).toBeVisible();
      await page.waitForTimeout(300);
      const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      const serious = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      const report = serious.map((v) => `${v.id} (${v.impact}): ${v.help}\n${v.nodes.slice(0, 5).map((n) => `  ${n.target.join(" ")} :: ${n.failureSummary?.split("\n")[1] ?? ""}`).join("\n")}`);
      expect(report, report.join("\n")).toEqual([]);
    });
  }
}

for (const theme of ["dark", "light"] as const) {
  for (const kind of ["up", "down"] as const) {
    test(`axe ${theme} coa-environment confirm ${kind}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      await page.goto(url("/settings/environment", { theme, coa: kind === "up" ? "off" : "running" }));
      await page.getByRole("button", { name: kind === "up" ? "Spin up" : "Tear down" }).click();
      await expect(page.getByTestId(`coa-confirm-${kind}`)).toBeVisible();
      await page.waitForTimeout(300);
      const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      const serious = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(serious.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
    });
  }
}
