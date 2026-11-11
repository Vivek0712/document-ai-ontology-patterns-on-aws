/** W-00 Home: the thesis, the three planes (schematic, no values) and the showcases. */
import { ArrowRight, ChevronRight, Play } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { APP_NAME, Page, TAGLINE, useDocumentTitle } from "../../app/Page";
import { useWorkspace } from "../../app/workspace";
import { Badge, Button, ProvenanceTag, cn } from "../../design";
import { SHOWCASES, showcaseMeta } from "../../showcase/catalog";
import { ShowcaseCard } from "../showcases/ShowcaseCard";

interface Plane {
  id: string;
  title: string;
  question: string;
  steps: { label: string; service: string; boundary?: boolean }[];
  showcases: string[];
  patterns: string;
}

const PLANES: Plane[] = [
  {
    id: "ingestion",
    title: "Ingestion",
    question: "What is on the page, and where?",
    steps: [
      { label: "Upload to Bronze", service: "Amazon S3" },
      { label: "Two lanes per object", service: "EventBridge · Step Functions" },
      { label: "Layout extraction", service: "Fargate (Docling) · Textract" },
      { label: "Evidence units", service: "AWS Lambda" },
      { label: "PII gate", service: "Amazon Comprehend", boundary: true },
      { label: "Masked Gold", service: "Amazon S3" },
    ],
    showcases: ["sc-1", "sc-2", "sc-6"],
    patterns: "Patterns 1 · 2 · PII gate",
  },
  {
    id: "knowledge",
    title: "Knowledge",
    question: "What does it mean, and who may see it?",
    steps: [
      { label: "Units index per lane", service: "OpenSearch Serverless" },
      { label: "Ontology + graph", service: "Amazon Neptune" },
      { label: "Metadata and ACLs", service: "Amazon DynamoDB" },
      { label: "Governed definitions", service: "COA (recorded)" },
    ],
    showcases: ["sc-3", "sc-4", "sc-8"],
    patterns: "Patterns 3 · 4",
  },
  {
    id: "agent",
    title: "Agent",
    question: "Which figures answer, and from where?",
    steps: [
      { label: "Signed-in request", service: "API Gateway · Cognito" },
      { label: "Agent", service: "AgentCore Runtime" },
      { label: "Tools behind an interceptor", service: "AgentCore Gateway" },
      { label: "Answer with citations", service: "Amazon Bedrock" },
    ],
    showcases: ["sc-5", "sc-7", "sc-9"],
    patterns: "Pattern 5",
  },
];

const LAYERS = [
  { id: "L1", title: "Extraction", where: "what is on the page" },
  { id: "L2", title: "Representation", where: "units, metadata, ontology" },
  { id: "L3", title: "Retrieval and generation", where: "the agent" },
];

export default function HomePage() {
  useDocumentTitle();
  const { packageId } = useWorkspace();
  const heroes = SHOWCASES.filter((s) => s.hero || s.status === "live").sort((a, b) => Number(b.status === "live") - Number(a.status === "live"));
  return (
    <Page className="flex flex-col gap-8">
      <section className="flex flex-col gap-3 pt-2">
        <span className="text-caps uppercase text-text-2">
          {APP_NAME} · {TAGLINE}
        </span>
        <h1 className="max-w-3xl text-xl font-semibold tracking-tight">Most enterprise RAG failures start before retrieval.</h1>
        <p className="max-w-2xl text-sm text-text-2">
          Provenant runs the same documents through two pipelines with the same agent and model. The naive one reads a flat text stream; the other reads the page into evidence units, so
          every figure opens the region it came from.
        </p>
        <div className="mt-1 flex flex-wrap gap-2">
          <Button asChild variant="primary">
            <Link to="/showcase/sc-0">
              <Play />
              Start the story
            </Link>
          </Button>
          <Button asChild variant="secondary">
            <Link to={`/workbench/units?pkg=${encodeURIComponent(packageId)}`}>
              Browse the workbench
              <ArrowRight />
            </Link>
          </Button>
        </div>
      </section>

      <section>
        <SectionHeader title="Three planes" right={<ProvenanceTag provenance="illustrative" source="schematic: no values, no live status yet" />} />
        {/* one grid, three shared rows (header, steps, showcases): every card's footer starts on the same line */}
        <div className="grid grid-cols-1 gap-2 lg:grid-cols-[1fr_auto_1fr_auto_1fr] lg:grid-rows-[auto_1fr_auto] lg:gap-y-0">
          {PLANES.map((p, i) => (
            <PlaneColumn key={p.id} plane={p} arrow={i < PLANES.length - 1} />
          ))}
        </div>
      </section>

      <section>
        <SectionHeader title="Layers" right={<ProvenanceTag provenance="illustrative" source="schematic: no values" />} />
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {LAYERS.map((l) => (
            <div key={l.id} className="flex items-baseline gap-2 rounded-md border border-border bg-surface px-3.5 py-2.5">
              <span className="font-mono text-mono-sm text-text-2">{l.id}</span>
              <span className="text-sm text-text">{l.title}</span>
              <span className="ml-auto truncate text-xs text-text-2">{l.where}</span>
            </div>
          ))}
        </div>
      </section>

      <section>
        <SectionHeader
          title="Showcases"
          right={
            <Link to="/showcase" className="flex items-center gap-1 text-xs text-text-2 hover:text-text">
              All {SHOWCASES.length}
              <ChevronRight className="size-3.5" />
            </Link>
          }
        />
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {heroes.map((s) => (
            <ShowcaseCard key={s.id} s={s} />
          ))}
        </div>
      </section>
    </Page>
  );
}

function SectionHeader({ title, right }: { title: string; right?: ReactNode }) {
  return (
    <div className="mb-2.5 flex items-center gap-2">
      <h2 className="text-caps uppercase text-text-2">{title}</h2>
      <div className="ml-auto">{right}</div>
    </div>
  );
}

function PlaneColumn({ plane, arrow }: { plane: Plane; arrow: boolean }) {
  return (
    <>
      <div className="flex min-w-0 flex-col rounded-md border border-border bg-surface lg:row-span-3 lg:grid lg:grid-rows-subgrid lg:gap-0">
        <div className="border-b border-border px-3.5 py-2.5">
          <div className="text-sm font-medium text-text">{plane.title} plane</div>
          <div className="text-xs text-text-2">
            {plane.question} <span className="text-text-2">· {plane.patterns}</span>
          </div>
        </div>
        <ol className="flex flex-col gap-1 p-2">
          {plane.steps.map((s, i) => (
            <li
              key={s.label}
              className={cn(
                "flex items-center gap-2 rounded-sm px-2 py-1.5",
                s.boundary ? "border border-boundary" : "border border-transparent",
              )}
            >
              <span className="w-4 shrink-0 text-right font-mono text-2xs text-text-2">{i + 1}</span>
              <span className="min-w-0 flex-1 truncate text-sm text-text">{s.label}</span>
              <span className="hidden truncate text-2xs text-text-2 sm:inline">{s.service}</span>
            </li>
          ))}
        </ol>
        <div className="flex flex-wrap content-start gap-1 border-t border-border px-3 py-2">
          {plane.showcases.map((id) => {
            const s = showcaseMeta(id)!;
            return (
              <Link key={id} to={`/showcase/${id}`}>
                <Badge tone={s.status === "live" ? "live" : "neutral"} dot={s.status === "live"} className="hover:bg-surface-2" title={s.status === "live" ? "live" : "coming soon"}>
                  {s.code} {s.title}
                </Badge>
              </Link>
            );
          })}
        </div>
      </div>
      {arrow && (
        <div className="hidden items-center justify-center text-text-2 lg:row-span-3 lg:flex" aria-hidden>
          <ArrowRight className="size-4" />
        </div>
      )}
    </>
  );
}
