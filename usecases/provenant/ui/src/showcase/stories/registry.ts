/** Live storyboards by showcase id. Each loads lazily with its route. */
import type { Storyboard } from "../engine/types";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const STORYBOARDS: Record<string, () => Promise<Storyboard<any>>> = {
  "sc-0": () => import("./sc0").then((m) => m.sc0StoryTour),
  "sc-1": () => import("./sc1-read-the-page").then((m) => m.sc1ReadThePage),
  "sc-2": () => import("./sc2-evidence-units").then((m) => m.sc2EvidenceUnits),
  "sc-3": () => import("./sc3-footnote-property").then((m) => m.sc3FootnoteProperty),
  "sc-4": () => import("./sc4-filters").then((m) => m.sc4Filters),
  "sc-5": () => import("./sc5-resolve-hop-cite").then((m) => m.sc5ResolveHopCite),
  "sc-6": () => import("./sc6-pii-gate").then((m) => m.sc6PiiGate),
  "sc-7": () => import("./sc7-identity").then((m) => m.sc7Identity),
  "sc-8": () => import("./sc8-governed-meaning").then((m) => m.sc8GovernedMeaning),
  "sc-9": () => import("./sc9-follow-the-number").then((m) => m.sc9FollowTheNumber),
  "sc-10": () => import("./sc10-bigger-model").then((m) => m.sc10BiggerModel),
  "sc-12": () => import("./sc12-evaluate").then((m) => m.sc12Evaluate),
  "sc-13": () => import("./sc13-cost").then((m) => m.sc13Cost),
  "sc-14": () => import("./sc14-three-planes").then((m) => m.sc14ThreePlanes),
  "sc-11": () => import("./sc11-before-after").then((m) => m.sc11BeforeAfter),
};
