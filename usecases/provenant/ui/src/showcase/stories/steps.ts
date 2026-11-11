/**
 * Step ids and titles of every live storyboard, kept apart from the stories so the command palette can list
 * steps without loading a story. Each storyboard spreads its entries into its steps (same order).
 */
import { SC1_STEPS } from "./sc1-steps";

export const SC0_STEPS = [
  { id: "question", title: "The question" },
  { id: "upload", title: "Upload" },
  { id: "watch", title: "Watch" },
  { id: "before", title: "Ask · before" },
  { id: "why", title: "Why" },
  { id: "after", title: "Ask · after" },
  { id: "boundary", title: "The boundary" },
  { id: "follow", title: "Follow the number" },
  { id: "definition", title: "Governed definition" },
] as const;

export const SC6_STEPS = [
  { id: "tiers", title: "Tiers" },
  { id: "enter", title: "Units enter" },
  { id: "held", title: "Held" },
  { id: "clean", title: "Clean passes" },
  { id: "gold-page", title: "Gold page" },
  { id: "never-indexed", title: "Never indexed" },
  { id: "both-lanes", title: "Both lanes" },
] as const;

export const SC9_STEPS = [
  { id: "figure", title: "The figure" },
  { id: "field", title: "The field" },
  { id: "unit", title: "The unit" },
  { id: "pixels", title: "The pixels" },
  { id: "engine", title: "The engine" },
  { id: "upload", title: "The upload" },
  { id: "pipeline", title: "The pipeline" },
  { id: "contrast", title: "Contrast" },
] as const;

export const SC2_STEPS = [
  { id: "regions", title: "Regions" },
  { id: "pass-a", title: "Pass A · seeds" },
  { id: "marker", title: "Marker link" },
  { id: "pass-b", title: "Pass B · allocation" },
  { id: "pass-c", title: "Pass C · residual" },
  { id: "unit", title: "The unit" },
  { id: "page-break", title: "Across a page break" },
  { id: "checks", title: "The checks" },
] as const;

export const SC3_STEPS = [
  { id: "classify", title: "Classify" },
  { id: "pages", title: "Both pages" },
  { id: "marker-link", title: "Marker link" },
  { id: "properties", title: "Text to properties" },
  { id: "graph", title: "Graph" },
  { id: "naive", title: "Naive contrast" },
] as const;

export const SC4_STEPS = [
  { id: "metadata", title: "Unit metadata" },
  { id: "query", title: "The query" },
  { id: "fusion", title: "Sub-queries, one score" },
  { id: "funnel", title: "Funnel" },
] as const;

export const SC5_STEPS = [
  { id: "question", title: "Question" },
  { id: "resolve", title: "Resolve" },
  { id: "retrieve", title: "Retrieve" },
  { id: "hop", title: "Hop" },
  { id: "assemble", title: "Assemble" },
  { id: "cite", title: "Cite" },
] as const;

export const SC7_STEPS = [
  { id: "users", title: "The users" },
  { id: "same-question", title: "Same question" },
  { id: "injection", title: "Injection" },
  { id: "scoped", title: "Scoped retrieval" },
  { id: "region", title: "Region check" },
] as const;

export const SC8_STEPS = [
  { id: "ontology", title: "The ontology" },
  { id: "review", title: "Checked in Git" },
  { id: "definition", title: "The definition" },
  { id: "bound", title: "Bound at preprocessing" },
  { id: "executor", title: "The executor" },
  { id: "discovery", title: "Discovery" },
] as const;

export const SC10_STEPS = [
  { id: "one-change", title: "One change" },
  { id: "same-evidence", title: "Same evidence" },
  { id: "results", title: "Results" },
  { id: "citations", title: "Citations" },
] as const;

export const SC11_STEPS = [
  { id: "question", title: "Question" },
  { id: "before", title: "Before" },
  { id: "why", title: "Why" },
  { id: "after", title: "After" },
  { id: "only-preprocessing", title: "Only preprocessing" },
  { id: "stability", title: "Stability" },
] as const;

export const SC12_STEPS = [
  { id: "questions", title: "The golden set" },
  { id: "naive", title: "Naive · top-1" },
  { id: "units", title: "Evidence units · top-1" },
  { id: "rates", title: "By type" },
] as const;

export const SC13_STEPS = [
  { id: "per-document", title: "Per document" },
  { id: "digital-scan", title: "Digital and scan" },
  { id: "tiering", title: "Model tiering" },
  { id: "tokens", title: "Tokens per answer" },
  { id: "actuals", title: "Actuals" },
] as const;

export const SC14_STEPS = [
  { id: "layers", title: "Layer map" },
  { id: "upload", title: "Upload event" },
  { id: "ingestion", title: "Ingestion plane" },
  { id: "gate", title: "Gate and promote" },
  { id: "knowledge", title: "Knowledge plane" },
  { id: "agent", title: "Agent plane" },
  { id: "answer", title: "Answer" },
] as const;

export const STORY_STEPS: Record<string, readonly { id: string; title: string }[]> = {
  "sc-0": SC0_STEPS,
  "sc-1": SC1_STEPS,
  "sc-2": SC2_STEPS,
  "sc-3": SC3_STEPS,
  "sc-4": SC4_STEPS,
  "sc-5": SC5_STEPS,
  "sc-6": SC6_STEPS,
  "sc-7": SC7_STEPS,
  "sc-8": SC8_STEPS,
  "sc-9": SC9_STEPS,
  "sc-10": SC10_STEPS,
  "sc-11": SC11_STEPS,
  "sc-12": SC12_STEPS,
  "sc-13": SC13_STEPS,
  "sc-14": SC14_STEPS,
};
