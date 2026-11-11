/**
 * Dev only: the X-ray exports written by `make xray-fixtures` (served from mock-public/, never bundled).
 *
 * Every package build has its own export under `/mock/xray/<packageId>/`, with file and image paths relative to that
 * folder, so the merged index keeps the folder as `base`. Indexes are fetched once, on the first X-ray request.
 */
import type { XrayIndex } from "../api/xray";

export type MockXrayDoc = XrayIndex["documents"][number] & { base: string };

export interface MockXrayIndex {
  control: XrayIndex["control"];
  documents: MockXrayDoc[];
}

let cached: Promise<MockXrayIndex> | undefined;

export const xrayBase = (packageId: string) => `/mock/xray/${packageId}/`;

async function readIndex(base: string): Promise<XrayIndex | undefined> {
  const res = await fetch(`${base}index.json`);
  return res.ok ? ((await res.json()) as XrayIndex) : undefined;
}

/** One export per package (a package without an export is skipped). */
export function loadXrayIndex(packageIds: string[]): Promise<MockXrayIndex> {
  cached ??= (async () => {
    const found = await Promise.all(packageIds.map(async (p) => ({ base: xrayBase(p), index: await readIndex(xrayBase(p)) })));
    const first = found.find((f) => f.index);
    if (!first?.index) throw new Error("x-ray index: not found (run make xray-fixtures)");
    const documents: MockXrayDoc[] = found.flatMap(({ base, index }) => (index?.documents ?? []).map((d) => ({ ...d, base })));
    return { control: first.index.control, documents };
  })();
  return cached;
}
