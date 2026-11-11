/**
 * SC-6 · PII gate. Bronze → Silver → the gate → Gold on the bank statement: the account-header unit
 * carries a full account number and is held in Silver; a clean unit from the same page passes into Gold; the Gold
 * page shows the held region masked; held units do not reach the index or the graph (the caption is chosen from
 * their UnitStatus rows); the same gate runs in both lanes, at chunk granularity in the naive one.
 *
 * Every unit keeps its token in Silver; a ghost copy crosses the gate (see GateFlow). A held copy returns to
 * Silver, so the Silver column never empties.
 *
 * Headline steps use only `hold` and `pass`; below-threshold hits live in the receipts and in W-12.
 * Every frame is `render({ payload, step, progress })`: no state, no timers, no typed values.
 */
import { easeCubicInOut } from "d3-ease";
import { Check, Minus } from "lucide-react";
import type { ReactNode } from "react";
import type { DocumentItem } from "@contracts/api";
import { type PiiGoldPage, type PiiUnitDecision, type PiiView, composePii } from "../../api/pii";
import { waitForLanes } from "../../api/runs";
import { Badge, CitationChip, DataList, LaneBadge, Num, type Provenance, cn } from "../../design";
import { GateFlow, type GateToken } from "../../viz/GateFlow";
import { type Rect, seg, toRect } from "../../viz/geom";
import { Boundary, HeldRegions, PageCanvas, Tag } from "../../viz/PageCanvas";
import { showcaseMeta } from "../catalog";
import type { Frame, Loaded, StoryContext, StoryStep, Storyboard } from "../engine/types";
import { PanelSection } from "./panel";
import { SC6_STEPS } from "./steps";

const ease = easeCubicInOut;

/** What the story is about: found by doc type and entity type, never by id. */
export const SC6_SUBJECT = { docType: "doc:BankStatement", entity: "BANK_ACCOUNT_NUMBER", cleanType: "table_panel" } as const;

export interface Sc6Payload {
  documents: DocumentItem[];
  doc: DocumentItem;
  lanes: { "2.3.1": PiiView; "1.0": PiiView };
  /** the doc's units per lane, in unit order */
  units: { "2.3.1": PiiUnitDecision[]; "1.0": PiiUnitDecision[] };
  held: PiiUnitDecision;
  clean: PiiUnitDecision;
  /** the naive lane's held chunk for the same document */
  heldChunk?: PiiUnitDecision;
  page: PiiGoldPage;
  pageSize: { width: number; height: number };
  provenance: Provenance;
}

export async function loadSc6({ api, packageId }: StoryContext): Promise<Loaded<Sc6Payload>> {
  await waitForLanes(api, packageId);
  const [evidence, naive, pkg] = await Promise.all([composePii(api, packageId, "2.3.1"), composePii(api, packageId, "1.0"), api.getPackage(packageId)]);
  const doc = pkg.documents.find((d) => d.docType === SC6_SUBJECT.docType);
  if (!doc) throw new Error(`No ${SC6_SUBJECT.docType} in ${packageId}.`);
  const mine = (v: PiiView) => v.units.filter((u) => u.docId === doc.docId);
  const held = mine(evidence).find((u) => u.action === "hold" && u.hits.some((h) => h.type === SC6_SUBJECT.entity && !h.masked));
  if (!held) throw new Error(`The gate held no ${SC6_SUBJECT.entity} unit in ${doc.fileName}.`);
  const page = held.pages[0];
  const sameP = mine(evidence).filter((u) => u.action === "pass" && u.pages.includes(page));
  const clean = sameP.find((u) => u.unitType === SC6_SUBJECT.cleanType) ?? sameP[0];
  if (!clean) throw new Error(`No unit passes the gate on page ${page} of ${doc.fileName}.`);
  const gold = evidence.goldPages.find((g) => g.docId === doc.docId && g.page === page);
  if (!gold) throw new Error(`No Gold page ${page} for ${doc.fileName}.`);
  const region = await api.getRegion(clean.unitId);
  const live = api.mode === "http";
  return {
    source: live
      ? { mode: "live", label: `Live · ${packageId}`, detail: "Amazon Comprehend decisions and UnitStatus rows from the deployed stack" }
      : {
          mode: "local-run",
          label: "Replay of a local pipeline run",
          detail: "Real Amazon Comprehend DetectPiiEntities responses (recorded) · moto S3 and DynamoDB · not AWS",
        },
    payload: {
      documents: pkg.documents,
      doc,
      lanes: { "2.3.1": evidence, "1.0": naive },
      units: { "2.3.1": mine(evidence), "1.0": mine(naive) },
      held,
      clean,
      heldChunk: mine(naive).find((u) => u.action === "hold"),
      page: gold,
      pageSize: { width: region.pageWidth, height: region.pageHeight },
      provenance: live ? "measured" : "local-run",
    },
  };
}

// --------------------------------------------------------------------------------------------- receipts

const hitOf = (u: PiiUnitDecision) => u.hits.find((h) => !h.masked) ?? u.hits[0];

const steps: StoryStep<Sc6Payload>[] = [
  {
    ...SC6_STEPS[0],
    durationMs: 1800,
    caption: "Bronze, Silver, the gate, Gold.",
    services: ["Amazon S3"],
    receipts: (p) => [
      { label: "Bronze objects", value: p.documents.length, path: "$.documents", provenance: p.provenance },
      { label: "Gate config (preprocessing.piiGate)", value: JSON.stringify(p.lanes["2.3.1"].config), path: "$.lanes['2.3.1'].config", provenance: p.provenance },
    ],
  },
  {
    ...SC6_STEPS[1],
    durationMs: 2200,
    caption: "Every unit passes through Amazon Comprehend.",
    services: ["AWS Lambda · pii-gate", "Amazon Comprehend"],
    receipts: (p) => [
      { label: `${p.doc.fileName} · units`, value: p.units["2.3.1"].length, path: "$.units['2.3.1']", provenance: p.provenance },
      { label: "Gate output", value: p.held.decisions.versionId, source: p.held.decisions.key, provenance: p.provenance },
    ],
  },
  {
    ...SC6_STEPS[2],
    durationMs: 2600,
    caption: "A unit with a full account number is held.",
    services: ["Amazon Comprehend", "AWS Lambda · pii-gate"],
    receipts: (p) => {
      const h = hitOf(p.held);
      return [
        { label: "Unit", value: p.held.unitId, provenance: p.provenance },
        { label: "Entity · score (Comprehend)", value: `${h?.type} · ${h?.score}`, path: "$.held.hits", provenance: p.provenance },
        { label: "Digits in the span (no text kept)", value: h?.digits, provenance: p.provenance },
        { label: "Decision", value: `${p.held.action} · ${p.held.rule}`, source: p.held.decisions.key, provenance: p.provenance },
      ];
    },
  },
  {
    ...SC6_STEPS[3],
    durationMs: 2000,
    caption: "A clean unit from the same page passes into Gold.",
    services: ["AWS Lambda · promote", "Amazon S3"],
    receipts: (p) => [
      { label: "Unit", value: p.clean.unitId, path: "$.clean", provenance: p.provenance },
      { label: "Hits", value: p.clean.hits.length, provenance: p.provenance },
      { label: "Decision", value: `${p.clean.action} · ${p.clean.rule}`, source: p.clean.decisions.key, provenance: p.provenance },
    ],
  },
  {
    ...SC6_STEPS[4],
    durationMs: 2400,
    caption: "The Gold page shows the held region masked.",
    services: ["AWS Lambda · promote", "Amazon S3"],
    receipts: (p) => [
      { label: "Gold page image", value: p.page.versionId, source: p.page.key, provenance: p.provenance },
      { label: "Held regions filled", value: p.page.heldRegions.length, path: "$.page.heldRegions", provenance: p.provenance },
    ],
  },
  {
    ...SC6_STEPS[5],
    durationMs: 2000,
    caption: indexCaption,
    services: ["Amazon OpenSearch Serverless", "Amazon Neptune"],
    receipts: (p) => [
      { label: `${p.held.label} · indexed · in graph`, value: `${p.held.indexed} · ${p.held.inGraph}`, path: "$.held", provenance: p.provenance },
      { label: `${p.clean.label} · indexed · in graph`, value: `${p.clean.indexed} · ${p.clean.inGraph}`, path: "$.clean", provenance: p.provenance },
      { label: "Read from", value: "UnitStatus rows written by the index and graph stages (rp-units)", provenance: "derived" },
      ...belowThreshold(p),
    ],
  },
  {
    ...SC6_STEPS[6],
    durationMs: 2200,
    caption: "The same gate runs in both pipelines.",
    services: ["Amazon Comprehend", "AWS Lambda · pii-gate"],
    receipts: (p) => [
      { label: "Naive · 1.0 held chunk", value: p.heldChunk ? `${p.heldChunk.unitId} · pages ${p.heldChunk.pages.join(", ")}` : "none", path: "$.heldChunk", provenance: p.provenance },
      { label: "Package · 1.0 held of units", value: `${p.lanes["1.0"].counters.held} of ${p.lanes["1.0"].counters.units}`, provenance: p.provenance },
      { label: "Package · 2.3.1 held of units", value: `${p.lanes["2.3.1"].counters.held} of ${p.lanes["2.3.1"].counters.units}`, provenance: p.provenance },
    ],
  },
];

/** `never-indexed`: stated only when the held unit's own UnitStatus rows say so (a test fails if they contradict). */
export function indexCaption(p: Sc6Payload): string {
  return !p.held.indexed && p.held.inGraph !== true ? "Held units never reach the index or the graph." : "This held unit has an index or graph row: the gate did not stop it.";
}

/** Below-threshold hits (e.g. a last-4 SSN) are a detail, never a headline step. */
function belowThreshold(p: Sc6Payload) {
  const rows = [...p.lanes["2.3.1"].units, ...p.lanes["1.0"].units].filter((u) => u.action === "below_threshold");
  return rows.length
    ? rows.map((u) => ({ label: `Below threshold · ${u.label}`, value: u.hits.map((h) => `${h.type} ${h.score} · ${h.digits} digits`).join(", "), provenance: p.provenance }))
    : [{ label: "Below-threshold hits in this package", value: "none in this package", provenance: p.provenance }];
}

// --------------------------------------------------------------------------------------------- frame

const short = (label: string) => label.split(" · ").at(-1) ?? label;
const sha8 = (s?: string) => s?.slice(0, 8);

/**
 * Per step: every unit sits in Silver; at `enter` a ghost copy of each moves into the gate (staggered); at `held`
 * the held unit's copy returns to Silver and the Silver token takes the hold; at `clean` the passing copies move on
 * to Gold (the clean unit first).
 */
function tokensFor(p: Sc6Payload, step: number, t: number): GateToken[] {
  const units = p.units["2.3.1"];
  const n = units.length;
  return units.map((u, i) => {
    const isHeld = u.unitId === p.held.unitId;
    const isClean = u.unitId === p.clean.unitId;
    const enter = step > 1 ? 1 : step === 1 ? ease(seg(t, 0.08 + (0.5 * i) / n, 0.38 + (0.5 * i) / n)) : 0;
    const appear = step === 0 ? seg(t, 0.35 + (0.4 * i) / n, 0.55 + (0.4 * i) / n) : 1;
    const back = isHeld ? (step > 2 ? 1 : step === 2 ? ease(seg(t, 0.15, 0.55)) : 0) : 0;
    const exit = isHeld ? 0 : step > 3 ? 1 : step === 3 ? ease(seg(t, isClean ? 0.1 : 0.45, isClean ? 0.55 : 0.85)) : 0;
    const state: GateToken["state"] = isHeld && back > 0.98 ? "held" : exit > 0.98 ? "passed" : enter > 0.98 ? "scanning" : "idle";
    const h = hitOf(u);
    let note: ReactNode;
    if (isHeld && state === "held" && h) note = <HitNote type={h.type} score={h.score} digits={h.digits} provenance={p.provenance} />;
    if (step === 5 && (isHeld || isClean)) note = <IndexNote indexed={u.indexed} inGraph={u.inGraph} />;
    return {
      id: u.unitId,
      label: short(u.label),
      meta: u.unitType.replace("_", " "),
      travel: 1 + enter - back + exit,
      state,
      opacity: appear,
      dim: (step === 2 && !isHeld) || (step === 3 && !isClean && !isHeld),
      emphasis: (step === 2 && isHeld) || (step === 3 && isClean) || (step === 5 && (isHeld || isClean)),
      note: step >= 2 ? (note ?? <span />) : undefined,
    };
  });
}

/** "5 units · 1 held" once the gate has decided. */
function SilverCount({ units, held, provenance, decided }: { units: number; held: number; provenance: Provenance; decided: boolean }) {
  return (
    <span className="inline-flex items-baseline gap-1">
      <Num value={units} kind="int" provenance={provenance} bare unit={units === 1 ? "unit" : "units"} />
      {decided && (
        <>
          <span className="text-text-3">·</span>
          <Num value={held} kind="int" provenance={provenance} bare unit="held" />
        </>
      )}
    </span>
  );
}

function HitNote({ type, score, digits, provenance }: { type: string; score: number; digits: number; provenance: Provenance }) {
  return (
    <span className="flex min-w-0 flex-col">
      <span className="truncate font-mono text-mono-sm">{type}</span>
      <span className="flex items-baseline gap-1">
        score <Num value={score} kind="ratio" provenance={provenance} bare size="xs" />
        <span className="text-text-3">·</span>
        <Num value={digits} kind="int" provenance={provenance} bare size="xs" unit="digits" />
      </span>
    </span>
  );
}

function IndexNote({ indexed, inGraph }: { indexed: boolean; inGraph: boolean | null }) {
  const one = (ok: boolean | null, label: string) => (
    <span className={cn("inline-flex items-center gap-0.5", ok ? "text-text" : "text-text-2")}>
      {ok ? <Check className="size-3" aria-hidden /> : <Minus className="size-3" aria-hidden />}
      {ok ? label : `not ${label}`}
    </span>
  );
  return (
    <>
      {one(indexed, "indexed")}
      <span className="text-text-3">·</span>
      {one(inGraph, "in graph")}
    </>
  );
}

function Sc6Stage({ payload: p, step, progress: t }: Frame<Sc6Payload>) {
  const docs = p.documents.map((d) => ({ id: d.docId, label: d.fileName, sha: sha8(d.sha256), active: d.docId === p.doc.docId }));
  const page = step === 4;
  const both = step === 6;
  const passed = p.units["2.3.1"].filter((u) => u.action !== "hold").length;
  const heldHere = p.units["2.3.1"].filter((u) => u.action === "hold").length;
  return (
    <div className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)_auto] lg:grid-cols-[minmax(0,1fr)_340px] lg:grid-rows-1">
      <div className="relative min-h-[320px] min-w-0">
        {page ? (
          <GoldPage p={p} t={t} />
        ) : (
          <div className="h-full overflow-y-auto px-5 pb-5">
            <div className="mx-auto flex w-full max-w-[1000px] flex-col gap-6 pt-6">
              <GateFlow
                docs={both ? docs.filter((d) => d.active) : docs}
                compact={both}
                tokens={tokensFor(p, step, t)}
                label={<LaneBadge pv="2.3.1" />}
                counts={{
                  bronze: <Num value={p.documents.length} kind="int" provenance={p.provenance} bare unit="objects" />,
                  silver: <SilverCount units={p.units["2.3.1"].length} held={heldHere} provenance={p.provenance} decided={step >= 2} />,
                  gold: step >= 3 ? <Num value={passed} kind="int" provenance={p.provenance} bare unit="units" /> : undefined,
                }}
              />
              {both && p.heldChunk && (
                <div style={{ opacity: ease(seg(t, 0.1, 0.5)) }}>
                  <GateFlow
                    docs={docs.filter((d) => d.active)}
                    tokens={[naiveToken(p, p.heldChunk, t)]}
                    label={
                      <>
                        <LaneBadge pv="1.0" />
                        <span>the whole chunk is held: the gate cannot hold less than the unit it is given</span>
                      </>
                    }
                    counts={{ silver: <SilverCount units={p.units["1.0"].length} held={p.units["1.0"].filter((u) => u.action === "hold").length} provenance={p.provenance} decided={t >= 0.7} /> }}
                    compact
                  />
                </div>
              )}
            </div>
          </div>
        )}
      </div>
      <aside className="min-h-0 overflow-y-auto border-t border-border bg-bg-subtle lg:border-l lg:border-t-0">
        <StepPanel p={p} step={step} />
      </aside>
    </div>
  );
}

/** The naive lane's chunk: its copy enters the gate and comes back held (the gate holds the whole chunk). */
function naiveToken(p: Sc6Payload, chunk: PiiUnitDecision, t: number): GateToken {
  const travel = 1 + ease(seg(t, 0.25, 0.45)) - ease(seg(t, 0.5, 0.7));
  const h = hitOf(chunk);
  return {
    id: chunk.unitId,
    label: chunk.label,
    meta: `pages ${chunk.pages.join("–")}`,
    travel,
    state: t >= 0.7 ? "held" : "scanning",
    emphasis: true,
    note: t >= 0.7 && h ? <HitNote {...h} provenance={p.provenance} /> : <span />,
  };
}

/**
 * The masked Gold page: the render already filled the held region black. Held reads as withheld (dashed
 * boundary at 1.5 px over a solid wash); the passed unit keeps a 1 px boundary and a green "in Gold" tag.
 */
function GoldPage({ p, t }: { p: Sc6Payload; t: number }) {
  const full: Rect = [0, 0, p.pageSize.width, p.pageSize.height];
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border bg-bg-subtle px-3 text-xs text-text-2">
        <LaneBadge pv="2.3.1" />
        <span className="truncate">
          {p.doc.fileName} · page {p.page.page} · masked Gold render
        </span>
      </div>
      <div className="relative min-h-0 flex-1">
        <PageCanvas ariaLabel={`${p.doc.fileName}, Gold page ${p.page.page}`} image={p.page.url ? { src: p.page.url } : undefined} pageWidth={p.pageSize.width} pageHeight={p.pageSize.height} focus={full} widthFit={0.35} animate={false}>
          <g opacity={ease(seg(t, 0.15, 0.45))}>
            <HeldRegions regions={p.page.heldRegions.map(toRect)} variant="withheld" />
          </g>
          {p.clean.bboxes.map((b, i) => (
            <Boundary key={i} rect={toRect(b)} width={1} tint={0.5} progress={seg(t, 0.45, 0.95)} />
          ))}
          {p.clean.bboxes[0] && <Tag x={p.clean.bboxes[0].x0 - 4} y={p.clean.bboxes[0].y0 - 4} text="✓ in Gold" tone="cited" placement="above" opacity={seg(t, 0.85, 1)} />}
        </PageCanvas>
      </div>
    </div>
  );
}

function StepPanel({ p, step }: { p: Sc6Payload; step: number }) {
  const cfg = p.lanes["2.3.1"].config;
  const h = hitOf(p.held);
  switch (step) {
    case 0:
      return (
        <PanelSection title="The gate" provenance={p.provenance} source="preprocessing.piiGate (lane config)">
          <DataList
            rows={[
              ["Engine", <span>Amazon Comprehend · DetectPiiEntities</span>],
              ["Action", <Badge tone="boundary">{cfg?.action ?? "—"}</Badge>],
              ["Rule", <span className="font-mono text-mono-sm">{cfg?.rule ?? "—"}</span>],
              ["Min score", <Num value={cfg?.minScore} kind="raw" provenance="design-target" source={`${p.lanes["2.3.1"].configKey ?? "lane config"} · preprocessing.piiGate.minScore`} />],
              ["Granularity", <span>the unit</span>],
            ]}
          />
          <p className="mt-3 text-xs text-text-2">Bronze holds the uploads as they arrived. Units are built in Silver. Only units the gate passes are promoted to Gold.</p>
        </PanelSection>
      );
    case 1:
      return (
        <PanelSection title="Silver units" provenance={p.provenance} source={p.held.decisions.key} meta={`${p.doc.fileName} · in build order`}>
          <ul className="flex flex-col gap-1">
            {p.units["2.3.1"].map((u) => (
              <li key={u.unitId} className="flex items-center gap-2 text-sm">
                <span className="min-w-0 flex-1 truncate">{short(u.label)}</span>
                <Badge tone="concept" dot>{u.unitType}</Badge>
              </li>
            ))}
          </ul>
        </PanelSection>
      );
    case 2:
      return (
        <PanelSection title="Held" provenance={p.provenance} source={p.held.decisions.key}>
          <DataList
            rows={[
              ["Unit", <span>{p.held.label}</span>],
              ["Entity", <span className="font-mono text-mono-sm">{h?.type}</span>],
              ["Score", <Num value={h?.score} kind="ratio" />],
              ["Digits", <Num value={h?.digits} kind="int" unit="unmasked" />],
              ["Decision", <CitationChip kind="held" label={`held · ${p.held.rule}`} />],
              ["Stays in", <span>Silver</span>],
            ]}
          />
          <p className="mt-3 text-xs text-text-2">The decision keeps type, score and digit count. The span text never leaves the gate.</p>
        </PanelSection>
      );
    case 3:
      return (
        <PanelSection title="Passes" provenance={p.provenance} source={p.clean.decisions.key}>
          <DataList
            rows={[
              ["Unit", <span>{p.clean.label}</span>],
              ["Page", <Num value={p.clean.pages[0]} kind="int" />],
              ["Hits", <Num value={p.clean.hits.length} kind="int" />],
              ["Decision", <span>pass</span>],
              ["Promoted to", <span>Gold</span>],
            ]}
          />
        </PanelSection>
      );
    case 4:
      return (
        <PanelSection title="Gold page" provenance={p.provenance} source={p.page.key}>
          <DataList
            rows={[
              ["Object", <span className="break-all font-mono text-mono-sm">{p.page.key.split("/").slice(-4).join("/")}</span>],
              ["Held regions", <Num value={p.page.heldRegions.length} kind="int" unit="filled black" />],
              ["Shown", <span>the masked render only</span>],
            ]}
          />
          <p className="mt-3 text-xs text-text-2">The browser only ever receives the masked Gold render, never the Bronze PDF.</p>
        </PanelSection>
      );
    case 5:
      return (
        <PanelSection title="Index and graph" provenance={p.provenance} source="UnitStatus rows (index and graph stages)">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-text-2">
                <th className="pb-1 font-medium">Unit</th>
                <th className="pb-1 font-medium">Indexed</th>
                <th className="pb-1 font-medium">Graph</th>
              </tr>
            </thead>
            <tbody>
              {[p.held, p.clean].map((u) => (
                <tr key={u.unitId} className="border-t border-border">
                  <td className="py-1.5 pr-2">{short(u.label)}</td>
                  <td className="py-1.5">{yesNo(u.indexed)}</td>
                  <td className="py-1.5">{yesNo(u.inGraph)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {p.clean.graphTriples !== undefined && (
            <p className="mt-3 text-xs text-text-2">
              {short(p.clean.label)}: <Num value={p.clean.graphTriples} kind="int" bare unit="triples" /> in the graph.
            </p>
          )}
        </PanelSection>
      );
    default:
      return (
        <>
          <PanelSection title="Per lane · this package" provenance={p.provenance}>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-text-2">
                  <th className="pb-1 font-medium">Lane</th>
                  <th className="pb-1 text-right font-medium">Units</th>
                  <th className="pb-1 text-right font-medium">Held</th>
                  <th className="pb-1 text-right font-medium">Passed</th>
                </tr>
              </thead>
              <tbody>
                {(["1.0", "2.3.1"] as const).map((pv) => (
                  <tr key={pv} className="border-t border-border">
                    <td className="py-1.5">
                      <LaneBadge pv={pv} />
                    </td>
                    <td className="py-1.5 text-right">
                      <Num value={p.lanes[pv].counters.units} kind="int" />
                    </td>
                    <td className="py-1.5 text-right">
                      <Num value={p.lanes[pv].counters.held} kind="int" />
                    </td>
                    <td className="py-1.5 text-right">
                      <Num value={p.lanes[pv].counters.passed} kind="int" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </PanelSection>
          {p.heldChunk && (
            <PanelSection title="Naive · 1.0 on this document" provenance={p.provenance}>
              <DataList
                rows={[
                  ["Unit", <span>{p.heldChunk.label}</span>],
                  ["Pages", <span className="tnum">{p.heldChunk.pages.join(", ")}</span>],
                  ["Passed", <Num value={p.units["1.0"].filter((u) => u.action !== "hold").length} kind="int" unit={`of ${p.units["1.0"].length}`} />],
                ]}
              />
              <p className="mt-3 text-xs text-text-2">
                {p.units["1.0"].length === 1 ? "A single chunk holds the whole document" : `${p.units["1.0"].length} chunks hold the document`}, so nothing held reaches Gold.
              </p>
            </PanelSection>
          )}
        </>
      );
  }
}

function yesNo(v: boolean | null): ReactNode {
  if (v === null) return <span className="text-text-2">no graph</span>;
  return v ? (
    <span className="inline-flex items-center gap-1 text-text">
      <Check className="size-3.5" aria-hidden /> yes
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-text-2">
      <Minus className="size-3.5" aria-hidden /> no
    </span>
  );
}

export const sc6PiiGate: Storyboard<Sc6Payload> = {
  id: "sc-6",
  code: "SC-6",
  title: "PII gate",
  claim: showcaseMeta("sc-6")?.claim ?? "",
  load: loadSc6,
  steps,
  render: (frame) => <Sc6Stage {...frame} />,
  receiptsPayload: (p) => ({ doc: p.doc, held: p.held, clean: p.clean, heldChunk: p.heldChunk, page: p.page, counters: { "2.3.1": p.lanes["2.3.1"].counters, "1.0": p.lanes["1.0"].counters } }),
};
