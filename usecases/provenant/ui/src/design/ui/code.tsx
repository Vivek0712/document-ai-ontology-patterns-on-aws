import { Check, ChevronRight, Copy } from "lucide-react";
import { type ReactNode, useState } from "react";
import { cn } from "../cn";
import { IconButton } from "./button";

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <IconButton
      size="sm"
      label={done ? "Copied" : "Copy"}
      icon={done ? <Check /> : <Copy />}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          window.setTimeout(() => setDone(false), 1200);
        });
      }}
    />
  );
}

const KEYWORDS = /\b(SELECT|WHERE|FILTER|PREFIX|GRAPH|OPTIONAL|BIND|GROUP BY|ORDER BY|LIMIT|AS|FROM|JOIN|ON|AND|OR|true|false|null)\b/g;

/** Neutral tokenizer for JSON/YAML/SPARQL/SQL: keys --text-2, strings/numbers --text, keywords weight 500. */
function tokenize(line: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /("(?:\\.|[^"\\])*")(\s*:)?|^(\s*[\w.-]+)(:)(?=\s|$)|(#.*$)|(-?\b\d+(?:\.\d+)?\b)|(\b[A-Z][A-Z ]+\b|\btrue\b|\bfalse\b|\bnull\b)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(line))) {
    if (m.index > last) out.push(line.slice(last, m.index));
    if (m[1]) out.push(<span key={i++} className={m[2] ? "text-text-2" : "text-text"}>{m[1]}</span>, m[2] ?? "");
    else if (m[3]) out.push(<span key={i++} className="text-text-2">{m[3]}</span>, m[4]);
    else if (m[5]) out.push(<span key={i++} className="text-text-2">{m[5]}</span>);
    else if (m[6]) out.push(<span key={i++} className="text-text">{m[6]}</span>);
    else if (m[7] && KEYWORDS.test(m[7])) out.push(<span key={i++} className="font-medium text-text">{m[7]}</span>);
    else out.push(m[0]);
    KEYWORDS.lastIndex = 0;
    last = re.lastIndex;
    if (m[0] === "") re.lastIndex++;
  }
  out.push(line.slice(last));
  return out;
}

const hanging = (line: string) => {
  const ch = (/^\s*/.exec(line)?.[0].length ?? 0) + 2;
  return { paddingLeft: `${ch}ch`, textIndent: `-${ch}ch` };
};

/** Code block with line numbers, highlighted lines and copy. mono-sm. Long lines soft-wrap (hanging indent). */
export function CodeBlock({ code, highlight = [], title, maxHeight = 360, className }: { code: string; highlight?: number[]; title?: ReactNode; maxHeight?: number; className?: string }) {
  const lines = code.replace(/\n$/, "").split("\n");
  return (
    <div className={cn("overflow-hidden rounded-sm border border-border bg-surface-2", className)}>
      {title !== undefined && (
        <div className="flex h-8 items-center justify-between border-b border-border pl-3 pr-1 text-xs text-text-2">
          <span className="truncate font-mono">{title}</span>
          <CopyButton text={code} />
        </div>
      )}
      <pre className="overflow-auto py-2 font-mono text-mono-sm" style={{ maxHeight }} tabIndex={0}>
        {lines.map((l, i) => (
          <div key={i} className={cn("flex pr-3", highlight.includes(i + 1) && "border-l-2 border-text-2 bg-surface-3")}>
            <span className="w-9 shrink-0 select-none pr-3 text-right text-text-2">{i + 1}</span>
            {/* a long line wraps under its own indent + 2ch (a hanging indent) instead of clipping mid-token */}
            <span className="min-w-0 whitespace-pre-wrap text-text [overflow-wrap:anywhere]" style={hanging(l)}>
              {tokenize(l)}
            </span>
          </div>
        ))}
      </pre>
    </div>
  );
}

/**
 * Collapsible JSON tree. `highlight` takes JSONPath-ish paths ("$.chunks[0].rects") and draws a left rule + tint.
 * Long values soft-wrap with a hanging indent, so a query string or model id is never clipped mid-token.
 */
export function JsonView({ value, highlight = [], collapseAfter = 2, maxHeight = 420, className }: { value: unknown; highlight?: string[]; collapseAfter?: number; maxHeight?: number; className?: string }) {
  return (
    <div className={cn("overflow-auto rounded-sm border border-border bg-surface-2 py-2 font-mono text-mono-sm", className)} style={{ maxHeight }} tabIndex={0} role="region" aria-label="JSON">
      <JsonNode name={undefined} value={value} path="$" depth={0} highlight={highlight} collapseAfter={collapseAfter} />
    </div>
  );
}

function JsonNode({ name, value, path, depth, highlight, collapseAfter }: { name?: string; value: unknown; path: string; depth: number; highlight: string[]; collapseAfter: number }) {
  const isObj = value !== null && typeof value === "object";
  const [open, setOpen] = useState(depth < collapseAfter);
  const hit = highlight.some((h) => h === path || path.startsWith(`${h}.`) || path.startsWith(`${h}[`));
  const key = name !== undefined && <span className="text-text-2">{name}: </span>;
  const pad = { paddingLeft: 12 + depth * 14 };
  if (!isObj) {
    return (
      <div className={cn("whitespace-pre-wrap pr-3 [overflow-wrap:anywhere]", hit && "border-l-2 border-text-2 bg-surface-3")} style={{ paddingLeft: pad.paddingLeft + 16, textIndent: -16 }}>
        {key}
        <span className="text-text">{JSON.stringify(value)}</span>
      </div>
    );
  }
  const entries = Array.isArray(value) ? value.map((v, i) => [String(i), v] as const) : Object.entries(value as Record<string, unknown>);
  const [o, c] = Array.isArray(value) ? ["[", "]"] : ["{", "}"];
  return (
    <div className={cn(hit && "border-l-2 border-text-2 bg-surface-3")}>
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center whitespace-pre pr-3 text-left hover:bg-surface-3" style={{ paddingLeft: pad.paddingLeft - 12 }}>
        <ChevronRight className={cn("size-3 shrink-0 text-text-3 transition-transform duration-fast", open && "rotate-90")} aria-hidden />
        {key}
        <span className="text-text-2">
          {o}
          {!open && ` ${entries.length} ${Array.isArray(value) ? "items" : "keys"} ${c}`}
        </span>
      </button>
      {open && (
        <>
          {entries.map(([k, v]) => (
            <JsonNode
              key={k}
              name={Array.isArray(value) ? undefined : k}
              value={v}
              path={Array.isArray(value) ? `${path}[${k}]` : `${path}.${k}`}
              depth={depth + 1}
              highlight={highlight}
              collapseAfter={collapseAfter}
            />
          ))}
          <div className="text-text-2" style={pad}>
            {c}
          </div>
        </>
      )}
    </div>
  );
}
