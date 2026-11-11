/** Text width in CSS px for SVG labels that stay a constant screen size (cached; canvas measureText). */
let ctx: CanvasRenderingContext2D | null | undefined;
const cache = new Map<string, number>();

export function textWidth(text: string, px: number, weight = 500, mono = false): number {
  const font = `${weight} ${px}px ${mono ? '"JetBrains Mono Variable", ui-monospace, monospace' : '"Inter Variable", system-ui, sans-serif'}`;
  const key = `${font}|${text}`;
  const hit = cache.get(key);
  if (hit !== undefined) return hit;
  if (ctx === undefined) ctx = typeof document !== "undefined" ? document.createElement("canvas").getContext("2d") : null;
  let w = text.length * px * (mono ? 0.6 : 0.55);
  if (ctx) {
    ctx.font = font;
    w = ctx.measureText(text).width;
  }
  cache.set(key, w);
  return w;
}
