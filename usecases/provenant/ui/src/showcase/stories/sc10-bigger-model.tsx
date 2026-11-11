/**
 * SC-10 · A bigger model does not fix it. The naive lane asked the same question with its own model
 * and with a frontier model; the lane config is identical and only the model id changes. The frame shows the one
 * change, the shared retrieved text, every logged run as a dot (eval scores only) and the citations both models
 * could make. Captions that state an outcome are chosen from the scores; nothing here is typed.
 */
import { easeCubicOut } from "d3-ease";
import { ArrowRight } from "lucide-react";
import { CitationChip, LaneBadge, Num, ProvenanceScope, ProvenanceTag, ScopeTag, Tooltip, cn } from "../../design";
import { seg } from "../../viz/geom";
import { showcaseMeta } from "../catalog";
import { StoryStage } from "../engine/StoryStage";
import type { Frame, StoryStep, Storyboard } from "../engine/types";
import { tally } from "./compare";
import { DotRow, OfN } from "./parts";
import { PROFILES, type ProfileKey, type Sc10Payload, type Sc10Question, citationsCaption, evidenceCaption, loadSc10, oneChangeCaption, resultsCaption, scoreLine } from "./sc10-payload";
import { SC10_STEPS } from "./steps";

const STEP = Object.fromEntries(SC10_STEPS.map((s, i) => [s.id, i])) as Record<(typeof SC10_STEPS)[number]["id"], number>;

const PROFILE_NAME: Record<ProfileKey, string> = { default: "Lane model · default", frontier: "Bigger model · frontier" };
const sha12 = (s?: string) => (s ? s.slice(0, 12) : "—");
/** "<doc slug>-<hash6>:<lane>:<unit>" → "<hash6>:<unit>" (the full id is the tooltip). */
const shortUnit = (id: string) => {
  const [doc, , u] = id.split(":");
  return `${doc.split("-").at(-1)}:${u}`;
};

const runsReceipt = (q: Sc10Question, k: ProfileKey) => ({
  label: `${PROFILE_NAME[k]} · ${q.profiles[k].modelId ?? "no run"}`,
  value: q.profiles[k].dots.map(scoreLine).join("; "),
  source: `GET /showcases/sc-10/payload?packageId=${q.packageId}&lane=${q.lane} · eval scores`,
  path: `$.lead.profiles.${k}.dots`,
});

const steps: StoryStep<Sc10Payload>[] = [
  {
    ...SC10_STEPS[0],
    durationMs: 2400,
    caption: oneChangeCaption,
    services: ["Amazon Bedrock"],
    receipts: (p) => [
      { label: "Config diff of the representative records", value: p.lead.configDiff ? `${p.lead.configDiff.a} vs ${p.lead.configDiff.b}` : "none", path: "$.lead.configDiff", provenance: p.provenance },
      ...(p.lead.configDiff?.differing ?? []).map((d) => ({ label: `differs · ${d.key}`, value: `${d.a} → ${d.b}`, provenance: p.provenance })),
      { label: "Identical", value: p.lead.configDiff?.identical.join(", "), provenance: p.provenance },
      ...(p.lead.configDiff?.unverified ?? []).map((u) => ({ label: `unverified · ${u.key}`, value: `default ${u.a ?? "not recorded"} · frontier ${u.b ?? "not recorded"}`, provenance: p.provenance })),
    ],
  },
  {
    ...SC10_STEPS[1],
    durationMs: 2200,
    caption: evidenceCaption,
    services: ["Amazon OpenSearch Serverless", "AWS Lambda · search_units"],
    receipts: (p) => [
      ...PROFILES.map((k) => ({ label: `retrieved text sha256 · ${k}`, value: p.lead.profiles[k].retrievedShas.join(", "), path: `$.lead.profiles.${k}.retrievedShas`, provenance: p.provenance })),
      { label: "Same retrieved text (representative runs)", value: p.lead.sameRetrievedText, path: "$.lead.sameRetrievedText", provenance: "derived" as const },
    ],
  },
  {
    ...SC10_STEPS[2],
    durationMs: 2800,
    caption: resultsCaption,
    services: ["Amazon Bedrock"],
    receipts: (p) => [
      ...PROFILES.map((k) => ({ ...runsReceipt(p.lead, k), provenance: p.provenance })),
      ...p.others.map((q) => ({ label: `Also asked · ${q.question}`, value: PROFILES.map((k) => `${k} ${tally(q.profiles[k].dots).correct}/${tally(q.profiles[k].dots).n} (+${tally(q.profiles[k].dots).errors} errors)`).join(" · "), source: q.packageId, provenance: p.provenance })),
    ],
  },
  {
    ...SC10_STEPS[3],
    durationMs: 2000,
    caption: citationsCaption,
    services: ["Amazon Bedrock AgentCore"],
    receipts: (p) => PROFILES.map((k) => ({ label: `Chips · ${k} · ${p.lead.profiles[k].representative?.runId ?? "no run"}`, value: p.lead.profiles[k].representative?.chips.map((c) => `${c.text} → ${c.label}${c.resolvable ? "" : " (no region)"}`).join("; "), path: `$.lead.profiles.${k}.representative.chips`, provenance: p.provenance })),
  },
];

// --------------------------------------------------------------------------------------------- frame

function Sc10Stage({ payload: p, step, progress: t }: Frame<Sc10Payload>) {
  const q = p.lead;
  return (
    <ProvenanceScope provenance={p.provenance} source={`GET /showcases/sc-10/payload · ${q.payloadSha.slice(0, 12)}`}>
      <StoryStage
        maxWidth={1120}
        header={
          <header className="flex flex-wrap items-center gap-2">
            <p className="text-md text-text">“{q.question}”</p>
            {q.qid && <span className="font-mono text-mono-sm text-text-2">{q.qid}</span>}
            <span className="ml-auto flex items-center gap-2">
              <span className="text-xs text-text-2">{q.packageName}</span>
              <LaneBadge pv={q.lane} />
              <ScopeTag />
            </span>
          </header>
        }
      >
        {step === STEP["one-change"] && <OneChange q={q} t={t} />}
        {step === STEP["same-evidence"] && <SameEvidence q={q} t={t} />}
        {step === STEP.results && <Results p={p} t={t} />}
        {step === STEP.citations && <Citations q={q} t={t} />}
      </StoryStage>
    </ProvenanceScope>
  );
}

function ProfileHead({ q, k }: { q: Sc10Question; k: ProfileKey }) {
  return (
    <div className="flex min-w-0 flex-col border-b border-border px-4 py-2">
      <span className="text-sm text-text">{PROFILE_NAME[k]}</span>
      <span className="truncate font-mono text-mono-sm text-text-2" title={q.profiles[k].modelId}>
        {q.profiles[k].modelId ?? "no run"}
      </span>
    </div>
  );
}

/** Step 1: the two run records side by side. Every identical field checks in, then the one field that differs. */
function OneChange({ q, t }: { q: Sc10Question; t: number }) {
  const d = q.configDiff;
  if (!d) return <p className="text-sm text-text-2">The bigger model has no completed run on this question, so there is no record pair to diff.</p>;
  const change = easeCubicOut(seg(t, 0.55, 0.95));
  return (
    <div className="flex flex-col gap-3">
      <section className="rounded-md border border-border bg-surface">
        <h3 className="flex items-center gap-2 border-b border-border px-4 py-2 text-caps uppercase text-text-2">
          Identical in both run records
          <span className="ml-auto font-mono text-mono-sm normal-case tracking-normal">
            {d.a} · {d.b}
          </span>
        </h3>
        <ul className="flex flex-wrap gap-1.5 p-4">
          {d.identical.map((key, i) => (
            <li
              key={key}
              className="inline-flex h-6 items-center gap-1.5 rounded-xs border border-border px-2 font-mono text-mono-sm text-text-2"
              style={{ opacity: 0.15 + 0.85 * easeCubicOut(seg(t, (i / d.identical.length) * 0.45, (i / d.identical.length) * 0.45 + 0.15)) }}
            >
              <span aria-hidden>=</span>
              {key}
            </li>
          ))}
        </ul>
        {(d.unverified ?? []).length > 0 && (
          <p className="flex flex-wrap items-center gap-1.5 border-t border-border px-4 py-2 text-xs text-text-2">
            Not comparable:
            {d.unverified!.map((u) => (
              <span key={u.key} className="inline-flex h-6 items-center gap-1.5 rounded-xs border border-dashed border-border-strong px-2 font-mono text-mono-sm text-text-2" title={`default ${u.a ?? "not recorded"} · frontier ${u.b ?? "not recorded"}`} data-unverified={u.key}>
                ? {u.key}
              </span>
            ))}
            <span>{d.unverified!.map((u) => `${u.a ? "frontier" : "default"} runs did not record ${u.key}`).join("; ")}</span>
          </p>
        )}
      </section>
      {d.differing.map((x) => (
        <section key={x.key} className="rounded-md border border-dashed border-text-2 bg-surface" style={{ opacity: 0.15 + 0.85 * change }} data-differs={x.key}>
          <h3 className="border-b border-border px-4 py-2 text-caps uppercase text-text-2">The one change · {x.key}</h3>
          <div className="grid grid-cols-[minmax(0,1fr)_32px_minmax(0,1fr)] items-center gap-3 px-4 py-3">
            <span className="flex flex-col gap-0.5">
              <span className="text-xs text-text-2">default</span>
              <span className="truncate font-mono text-mono-md text-text" title={x.a}>{x.a}</span>
            </span>
            <ArrowRight className="mx-auto size-4 text-text" style={{ transform: `translateX(${(change - 1) * 8}px)` }} aria-label="changes to" />
            <span className="flex flex-col gap-0.5">
              <span className="text-xs text-text-2">frontier</span>
              <span className="truncate font-mono text-mono-md text-text" title={x.b}>{x.b}</span>
            </span>
          </div>
        </section>
      ))}
    </div>
  );
}

/** Step 2: the retrieved chunks once, and each model's retrieved-text sha meeting at "=". */
function SameEvidence({ q, t }: { q: Sc10Question; t: number }) {
  const units = [...new Set(PROFILES.flatMap((k) => q.profiles[k].representative?.retrievedUnits ?? []))];
  const shaOf = (k: ProfileKey) => q.profiles[k].representative?.retrievedSha;
  const equal = q.sameRetrievedText;
  const a = easeCubicOut(seg(t, 0, 0.45));
  const b = easeCubicOut(seg(t, 0.45, 0.85));
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_minmax(260px,0.9fr)_1fr]">
      {PROFILES.map((k, i) => (
        <section key={k} className={cn("rounded-md border border-border bg-surface", i === 1 && "lg:order-3")} style={{ opacity: 0.3 + 0.7 * a }}>
          <ProfileHead q={q} k={k} />
          <div className="flex flex-col gap-1 p-4">
            <span className="text-xs text-text-2">retrieved text sha256 · representative run</span>
            <Tooltip content={shaOf(k)}>
              <span className="w-fit font-mono text-mono-md text-text" tabIndex={0}>
                {sha12(shaOf(k))}
              </span>
            </Tooltip>
            <span className="text-xs text-text-3">
              {q.profiles[k].retrievedShas.length} distinct across this cohort's runs
            </span>
          </div>
        </section>
      ))}
      <section className="flex flex-col items-center justify-center gap-3 rounded-md border border-border bg-bg-subtle p-4 lg:order-2">
        <span className="text-caps uppercase text-text-2">{equal ? "retrieved for both" : "retrieved by either"}</span>
        <ul className="flex flex-wrap justify-center gap-1.5" style={{ opacity: 0.2 + 0.8 * a }}>
          {units.map((u) => (
            <li key={u}>
              <Tooltip content={u}>
                <span className="inline-flex h-6 items-center rounded-xs border border-border-strong px-2 font-mono text-mono-sm text-text" tabIndex={0}>
                  {shortUnit(u)}
                </span>
              </Tooltip>
            </li>
          ))}
        </ul>
        <span className={cn("flex size-9 items-center justify-center rounded-full border text-md", equal ? "border-text-2 text-text" : "border-dashed border-loss text-loss")} style={{ opacity: b, transform: `scale(${0.7 + 0.3 * b})` }} aria-label={equal ? "equal" : "different"}>
          {equal ? "=" : "≠"}
        </span>
        <span className="text-center text-xs text-text-2" style={{ opacity: b }}>
          {equal ? "same chunks, same text (representative runs)" : "different text (representative runs)"} <ProvenanceTag provenance="derived" source="sha256 of the retrieved text, compared" className="align-middle" />
        </span>
      </section>
    </div>
  );
}

/** Step 3: every logged run as a dot, the totals they gave, and every other question asked both ways. */
function Results({ p, t }: { p: Sc10Payload; t: number }) {
  const q = p.lead;
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {PROFILES.map((k, i) => {
          const pr = q.profiles[k];
          const n = tally(pr.dots);
          return (
            <section key={k} className="rounded-md border border-border bg-surface" data-profile={k}>
              <ProfileHead q={q} k={k} />
              <div className="flex flex-col gap-3 p-4">
                <DotRow dots={pr.dots} t={seg(t, i * 0.25, 0.6 + i * 0.25)} cited />
                <p className="text-sm text-text-2">
                  <OfN k={n.correct} n={n.n} /> correct
                  {n.errors > 0 && (
                    <span>
                      {" "}
                      · <Num value={n.errors} kind="int" bare /> agent {n.errors === 1 ? "error" : "errors"}
                    </span>
                  )}
                </p>
                <ul className="flex flex-col gap-1 text-sm">
                  {pr.totals.map(([total, count]) => (
                    <li key={total} className="flex items-baseline gap-2">
                      <Num value={total} kind={/\d/.test(total) ? "money" : "raw"} />
                      <span className="text-xs text-text-2">
                        × <Num value={count} kind="int" size="xs" bare />
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            </section>
          );
        })}
      </div>
      {p.others.length > 0 && (
        <section className="rounded-md border border-border bg-surface" style={{ opacity: 0.3 + 0.7 * seg(t, 0.6, 1) }}>
          <h3 className="border-b border-border px-4 py-2 text-caps uppercase text-text-2">Every other question asked both ways</h3>
          <table className="w-full text-sm">
            <thead className="text-left text-caps uppercase text-text-2">
              <tr className="border-b border-border">
                <th className="px-4 py-1.5 font-medium">Question</th>
                {PROFILES.map((k) => (
                  <th key={k} className="px-4 py-1.5 font-medium">
                    {k}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {p.others.map((o) => (
                <tr key={`${o.packageId}|${o.question}`} className="border-b border-border last:border-0">
                  <td className="px-4 py-2">
                    <span className="text-text">{o.question}</span>
                    <span className="ml-2 text-xs text-text-2">{o.packageName}</span>
                  </td>
                  {PROFILES.map((k) => {
                    const n = tally(o.profiles[k].dots);
                    return (
                      <td key={k} className="px-4 py-2">
                        <span className="flex flex-wrap items-center gap-2">
                          <DotRow dots={o.profiles[k].dots} />
                          <span className="text-xs text-text-2">
                            <OfN k={n.correct} n={n.n} />
                            {n.errors > 0 && <span> · {n.errors} {n.errors === 1 ? "error" : "errors"}</span>}
                          </span>
                        </span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {p.others.some((o) => o.profiles.frontier.dots.some((d) => d.status === "error")) && (
            <p className="border-t border-border px-4 py-2 text-xs text-text-2">
              Errored runs stopped inside the lane's own limits; their logged error is in each dot's tooltip. Raising a limit for one model would change more than the model.
            </p>
          )}
        </section>
      )}
    </div>
  );
}

/** Step 4: each model's chips as the representative run logged them; a chunk claim has no region. */
function Citations({ q, t }: { q: Sc10Question; t: number }) {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      {PROFILES.map((k, i) => {
        const r = q.profiles[k].representative;
        const n = tally(q.profiles[k].dots);
        const on = easeCubicOut(seg(t, i * 0.3, 0.5 + i * 0.3));
        return (
          <section key={k} className="rounded-md border border-border bg-surface">
            <ProfileHead q={q} k={k} />
            <div className="flex flex-col gap-3 p-4" style={{ opacity: 0.25 + 0.75 * on }}>
              {r?.total && <Num value={r.total} kind="money" size="lg" />}
              <div className="flex flex-wrap gap-1.5">
                {(r?.chips ?? []).map((c, j) =>
                  c.resolvable ? (
                    <CitationChip key={j} kind="cited" label={c.label} />
                  ) : (
                    <CitationChip key={j} kind="noregion" label={c.label} value={c.text} title={c.reason ?? undefined} />
                  ),
                )}
                {!r?.chips.length && <span className="text-xs text-text-2">no claims</span>}
              </div>
              <p className="text-xs text-text-2">
                <OfN k={n.cited} n={n.n} /> runs cite a region
              </p>
            </div>
          </section>
        );
      })}
    </div>
  );
}

export const sc10BiggerModel: Storyboard<Sc10Payload> = {
  id: "sc-10",
  code: "SC-10",
  title: showcaseMeta("sc-10")?.title ?? "",
  claim: showcaseMeta("sc-10")?.claim ?? "",
  load: loadSc10,
  steps,
  render: (frame) => <Sc10Stage {...frame} />,
  receiptsPayload: (p) => ({ lead: { ...p.lead, profiles: Object.fromEntries(PROFILES.map((k) => [k, { ...p.lead.profiles[k], representative: p.lead.profiles[k].representative?.runId }])) }, others: p.others.map((o) => ({ question: o.question, packageId: o.packageId, dots: PROFILES.map((k) => o.profiles[k].dots) })) }),
};
