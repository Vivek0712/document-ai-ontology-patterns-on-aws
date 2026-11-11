/** Rect helpers in PDF points (top-left origin). A Rect is [x0, y0, x1, y1]. */
/** [x0, y0, x1, y1] */
export type Rect = [number, number, number, number];

export const rw = (r: Rect) => r[2] - r[0];
export const rh = (r: Rect) => r[3] - r[1];
export const center = (r: Rect): [number, number] => [(r[0] + r[2]) / 2, (r[1] + r[3]) / 2];

export function union(rects: (Rect | null | undefined)[]): Rect | undefined {
  const rs = rects.filter((r): r is Rect => Boolean(r));
  if (!rs.length) return undefined;
  return [Math.min(...rs.map((r) => r[0])), Math.min(...rs.map((r) => r[1])), Math.max(...rs.map((r) => r[2])), Math.max(...rs.map((r) => r[3]))];
}

export function pad(r: Rect, p: number): Rect {
  return [r[0] - p, r[1] - p, r[2] + p, r[3] + p];
}

export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

export function lerpRect(a: Rect, b: Rect, t: number): Rect {
  return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t), lerp(a[3], b[3], t)];
}

/** BBox / XrayRect object -> Rect tuple. */
export function toRect(b: { x0: number; y0: number; x1: number; y1: number }): Rect {
  return [b.x0, b.y0, b.x1, b.y1];
}

export const clamp01 = (t: number) => (t < 0 ? 0 : t > 1 ? 1 : t);

/** Progress of a sub-interval [a, b] of a step's progress t, clamped to 0..1. */
export const seg = (t: number, a: number, b: number) => clamp01((t - a) / (b - a));
