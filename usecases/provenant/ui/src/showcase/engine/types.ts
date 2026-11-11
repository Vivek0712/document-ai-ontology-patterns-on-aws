/**
 * Showcase engine types. A storyboard is data + a pure render function:
 *   render({ payload, step, progress, reduced }) -> ReactNode
 * so playing, stepping, scrubbing, deep links and screenshots all produce the same frame for the same input.
 * Storyboards never type a value: every number on screen is read from the payload.
 */
import type { ReactNode } from "react";
import type { RpApi } from "../../api/types";
import type { Provenance } from "../../design";

export interface StoryContext {
  api: RpApi;
  packageId: string;
  /**
   * The API as another signed-in identity (SC-7: the same request with User B's token). Undefined when that
   * identity has no session; a story then shows the response the real handler returned when it was exported.
   */
  peer?: (user: "a" | "b") => Promise<RpApi | undefined>;
  /** The story's own URL parameters (`ShowcaseMeta.params`), e.g. SC-11's `ex` example switch. */
  params?: Record<string, string>;
}

/** Where the payload came from; the Player always shows it. */
export interface DataSource {
  mode: "live" | "replay" | "local-run";
  /** e.g. "Replay of a local run" */
  label: string;
  /** e.g. "build/e2e · make xray-fixtures · not AWS" */
  detail?: string;
  recordedAt?: string;
  runId?: string;
}

export interface Loaded<P> {
  payload: P;
  source: DataSource;
  /**
   * The package the payload was read from, when the story found its subject by document type in another package
   * than the workspace's: the top bar then shows it (read-only) for the story's duration.
   */
  subjectPackageId?: string;
}

/** One line in the receipts drawer: what a step reads, from which artifact, with which provenance. */
export interface Receipt {
  label: string;
  value?: string | number | boolean | null;
  /** payload path, highlighted in the JSON tab, e.g. "$.control.pairs[3]" */
  path?: string;
  /** artifact: S3 key, local file, route */
  source?: string;
  provenance?: Provenance;
}

export interface StoryStep<P> {
  id: string;
  title: string;
  /** ≤ 90 characters, plain words; a function when it quotes payload values */
  caption: string | ((p: P) => string);
  /** design target, ms of playback for this step (pacing, not a measurement) */
  durationMs: number;
  receipts?: (p: P) => Receipt[];
  /** AWS services doing the work in this step (chips) */
  services?: string[];
}

export interface Frame<P> {
  payload: P;
  /** 0-based step index */
  step: number;
  /** 0..1 within the step; 1 = the step's end state */
  progress: number;
  /** reduced motion: progress is always 1 (steps cut instantly) */
  reduced: boolean;
}

export interface Storyboard<P> {
  id: string;
  code: string;
  title: string;
  claim: string;
  load(ctx: StoryContext): Promise<Loaded<P>>;
  steps: StoryStep<P>[];
  render(frame: Frame<P>): ReactNode;
  /** the payload subtree shown in the receipts JSON tab */
  receiptsPayload?(p: P): unknown;
}

export function captionOf<P>(step: StoryStep<P>, payload: P): string {
  return typeof step.caption === "function" ? step.caption(payload) : step.caption;
}
