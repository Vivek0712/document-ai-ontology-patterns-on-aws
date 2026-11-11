/**
 * The governed definition of "total income" as the session build has it (SC-0 step 8, SC-8, W-10):
 * `fin:QualifyingIncome` authored in Git, read from the built TBox that is loaded into Neptune, with the
 * `make ontology-check` result, the last commit and its review record. `GET /governance/definitions/{name}` serves
 * the record the ontology build wrote (`ontology/rpont/governance.py`); mock mode serves the same response from
 * `world.json` `governance`. Its `coa` slot is `off` (the Git record only) or `snapshot`: a recorded COA session
 * (`runs/local/coa/<date>/`), replayed and labelled with its capture date. Nothing here calls COA.
 */
import type { CoaSnapshot, GovernedDefinitionView } from "@contracts/api";

export type GitGovernedDefinition = GovernedDefinitionView;

/** `urn:rp:ont:fin:QualifyingIncome` → `fin:QualifyingIncome`. */
export function curie(iri: string): string {
  const m = iri.match(/^urn:rp:ont:([a-z]+):(.+)$/);
  return m ? `${m[1]}:${m[2]}` : iri;
}

/** The lane config's preprocessing keys that bind (or, on the naive lane, do not bind) the ontology, as YAML lines. */
export const BINDING_KEYS = ["ontology", "enrichment", "classify"] as const;

export function bindingLines(yaml: string): { key: string; line: string; binds: boolean }[] {
  const lines = yaml.split("\n");
  return BINDING_KEYS.flatMap((k) => {
    const line = lines.find((l) => new RegExp(`^\\s+${k}:`).test(l));
    return line ? [{ key: k, line: line.trim(), binds: !/:\s*none\s*$/.test(line) && !/:\s*false\b/.test(line.split("{")[1] ?? "") }] : [];
  });
}

/** The prompt's definition: the heading line that names the concept and the sentence under it. */
export function promptDefinition(prompt: string, curie: string): string[] {
  const lines = prompt.split("\n");
  const i = lines.findIndex((l) => l.includes(curie));
  if (i < 0) return [];
  const out = [lines[i]];
  for (const l of lines.slice(i + 1)) {
    if (!l.trim()) break;
    out.push(l);
  }
  return out;
}

/**
 * The governance state of a definition in words, from the record: "Reviewed in Git" only when a reviewed merge
 * exists, "Committed in Git" when there is a commit, else "Defined in Git · not committed". COA never reviews or
 * approves anything here: with a snapshot it is a recorded cross-check, otherwise it is off.
 */
export function governanceLabel(d: Pick<GovernedDefinitionView, "git" | "review"> & { coa?: GovernedDefinitionView["coa"] }): string {
  const git = d.review ? "Reviewed in Git" : d.git?.commit ? "Committed in Git · not reviewed" : "Defined in Git · not committed";
  return `${git} · ${coaOf(d) ? "COA cross-check (snapshot)" : "COA off"}`;
}

/** The recorded COA session on the record, when the build ran with `coaMode` snapshot; else undefined (Git fallback). */
export function coaOf(d: { coa?: GovernedDefinitionView["coa"] } | undefined): CoaSnapshot | undefined {
  const c = d?.coa;
  return c && c.mode === "snapshot" && c.state === "captured" && c.snapshot ? c.snapshot : undefined;
}

/** The provenance tag text of every COA value: a dated snapshot, never live. */
export function coaTagLabel(s: CoaSnapshot): string {
  return `COA snapshot · captured ${s.capturedAt}`;
}

/** Our own total for the same definition and package (the gate run's template result), to compare COA's with. */
export interface OurTotal {
  packageId: string;
  total: string;
  runId: string;
}

export type CrossCheckState = "match" | "differs" | "not-compared";

/**
 * COA's recomputation against ours: "match" only when both totals are numerically equal and COA's `package_id`
 * dimension names the package our run answered for. Without our total, "not-compared" (no ✓ is drawn).
 */
export function crossCheckState(s: CoaSnapshot, ours: OurTotal | undefined): CrossCheckState {
  if (!ours) return "not-compared";
  const pkg = s.crossCheck.dimensions.package_id;
  if (pkg && pkg !== ours.packageId) return "not-compared";
  return Number(s.crossCheck.value) === Number(ours.total.replace(/,/g, "")) ? "match" : "differs";
}

/** "COA governed metric · Tier 1 · confidence 1.0" (the row's label; the value renders as a Num beside it). */
export function crossCheckLabel(s: CoaSnapshot): string {
  return `COA governed metric · Tier ${s.crossCheck.tier} · confidence ${s.crossCheck.confidence.toFixed(1)}`;
}

/** The trace chips in COA's order: metric_match → authorize (cedar) → firewall → execute (athena). */
export function traceChips(s: CoaSnapshot): string[] {
  return s.crossCheck.trace.map((t) => (t.engine ? `${t.step} (${t.engine})` : t.tool === "cedar" ? `${t.step} (cedar)` : t.step));
}

/** "<EMAIL>" in a snapshot is the creating principal, redacted: never shown as an address. */
export function definedByText(s: CoaSnapshot): string {
  return s.metric.definedByRedacted ? "the creating principal (redacted in the snapshot)" : s.metric.definedBy;
}
