/**
 * The recorded COA session the governance record replays (`runs/local/coa/<date>/`), read straight from the files
 * so tests can check that the UI's COA values are the recorded ones, never typed (dev only, imported by tests).
 */
import { readFileSync, readdirSync } from "node:fs";

const ROOT = new URL("../../../../../runs/local/coa/", import.meta.url);

export function coaSnapshotFile() {
  const session = readdirSync(ROOT).filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d)).sort().at(-1)!;
  const read = (f: string) => JSON.parse(readFileSync(new URL(`${session}/${f}`, ROOT), "utf8"));
  return {
    session,
    capturedAt: String(read("02_namespace.json").response.namespace.createdAt).slice(0, 10),
    query: read("12_query_rest_tier1.json"),
    metric: read("11_metric_get.json").response.metric,
  };
}
