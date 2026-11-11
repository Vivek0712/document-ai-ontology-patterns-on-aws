/**
 * The 15 showcases, one per pattern claim, each with a storyboard in ./stories. Question ids live
 * here (never in a storyboard file): a story's gate question, and SC-11's before/after examples.
 */
export type ShowcaseGroup = "Story" | "The failure" | "The five patterns" | "On AWS";

export interface ShowcaseMeta {
  id: string; // route id: /showcase/:id
  code: string; // SC-n
  title: string;
  claim: string;
  group: ShowcaseGroup;
  status: "live" | "next";
  /** featured: shown in the navigation even before it is live */
  hero?: boolean;
  /** the logged run that will test this showcase's hypothesis (presenter and receipts only) */
  gateRun?: { questionId: string; repeats: number; milestone: string };
  /** URL query keys the story reads when it loads (part of its cache key), e.g. SC-11's `ex` */
  params?: string[];
  /** SC-11: the before/after examples, each one golden question (its package is found from the logged runs) */
  examples?: ShowcaseExample[];
}

export interface ShowcaseExample {
  /** URL value of `?ex=` */
  id: string;
  /** a short name for it (D1, D2, D3) or the package it adds */
  code: string;
  title: string;
  questionId: string;
}

/** The 15 showcases, grouped by theme. */
export const SHOWCASES: ShowcaseMeta[] = [
  { id: "sc-0", code: "SC-0", title: "Story tour", claim: "Same upload, same agent: only one answer can show where its figures come from.", group: "Story", status: "live", hero: true, gateRun: { questionId: "q-lend-01", repeats: 10, milestone: "gate run" } },
  { id: "sc-10", code: "SC-10", title: "A bigger model does not add regions", claim: "Same chunks, a larger model: still no region to cite.", group: "The failure", status: "live", gateRun: { questionId: "q-lend-01", repeats: 10, milestone: "frontier profile × 10" } },
  { id: "sc-11", code: "SC-11", title: "Before and after", claim: "Fix the representation; same retriever, same LLM.", group: "The failure", status: "live",
    params: ["ex"],
    examples: [
      { id: "d3", code: "D3", title: "Lending total", questionId: "q-lend-01" },
      { id: "d1", code: "D1", title: "Q3 revenue", questionId: "q-qr25-01" },
      { id: "d2", code: "D2", title: "Step-up coupon", questionId: "q-tc31-02" },
      { id: "lending-02", code: "Lending 02", title: "Second lending total", questionId: "q-lend2-01" },
    ],
  },
  { id: "sc-1", code: "SC-1", title: "Read the page", claim: "Extraction returns regions with roles and boxes, not a flat text stream.", group: "The five patterns", status: "live", hero: true, gateRun: { questionId: "q-lend-01", repeats: 10, milestone: "gate run" } },
  { id: "sc-2", code: "SC-2", title: "Evidence units", claim: "Retrieve whole units of meaning; two checks.", group: "The five patterns", status: "live" },
  { id: "sc-3", code: "SC-3", title: "The footnote becomes a property", claim: "Schema-first: a footnote turns into a typed coupon property.", group: "The five patterns", status: "live" },
  { id: "sc-4", code: "SC-4", title: "Filters inside the query", claim: "Every unit carries its context, so filters run inside the query.", group: "The five patterns", status: "live", gateRun: { questionId: "q-lend-01", repeats: 10, milestone: "gate run" } },
  { id: "sc-5", code: "SC-5", title: "Resolve, retrieve, hop, cite", claim: "The agent retrieves whole units, hops the graph and cites each figure.", group: "The five patterns", status: "live", gateRun: { questionId: "q-lend-01", repeats: 10, milestone: "gate run" } },
  { id: "sc-12", code: "SC-12", title: "Evaluate", claim: "Measure Recall@1 by question type.", group: "The five patterns", status: "live" },
  { id: "sc-14", code: "SC-14", title: "Three planes", claim: "Ingestion, knowledge and agent planes turn documents into governed answers.", group: "On AWS", status: "live", gateRun: { questionId: "q-lend-01", repeats: 10, milestone: "gate run" } },
  { id: "sc-6", code: "SC-6", title: "PII gate", claim: "Nothing reaches Gold without the PII gate.", group: "On AWS", status: "live", hero: true },
  { id: "sc-7", code: "SC-7", title: "Identity and scoping", claim: "AgentCore keeps the agent small and scoped.", group: "On AWS", status: "live", gateRun: { questionId: "q-lend-01", repeats: 2, milestone: "User B runs" } },
  { id: "sc-8", code: "SC-8", title: "Governed meaning", claim: "Total income is defined once and governed.", group: "On AWS", status: "live", gateRun: { questionId: "q-lend-01", repeats: 10, milestone: "gate run" } },
  { id: "sc-9", code: "SC-9", title: "Follow the number", claim: "Every figure opens its region, down to the uploaded bytes and the pipeline.", group: "On AWS", status: "live", hero: true, gateRun: { questionId: "q-lend-01", repeats: 10, milestone: "gate run" } },
  { id: "sc-13", code: "SC-13", title: "Cost and model tiering", claim: "Governance and cost share the same choices.", group: "On AWS", status: "live", gateRun: { questionId: "q-lend-01", repeats: 10, milestone: "gate run" } },
];

export const SHOWCASE_GROUPS: ShowcaseGroup[] = ["Story", "The failure", "The five patterns", "On AWS"];

export function showcaseMeta(id: string | undefined): ShowcaseMeta | undefined {
  return SHOWCASES.find((s) => s.id === id);
}
