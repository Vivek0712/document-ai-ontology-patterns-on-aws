/**
 * Camera maths for PageCanvas. A view is the page-space point at the container's top-left (x, y) and the
 * scale k in CSS px per PDF point. Zoom-to-box uses d3's smooth zoom (van Wijk & Nuij) so a long jump
 * zooms out a little and back in, which reads as one deliberate move.
 */
import { interpolateZoom } from "d3-interpolate";

// d3-interpolate ≥ 2 has interpolateZoom.rho(); @types/d3-interpolate does not declare it
const smoothZoom = (interpolateZoom as unknown as { rho(r: number): typeof interpolateZoom }).rho(1.2);
import type { Rect } from "../geom";

export interface View {
  x: number;
  y: number;
  k: number;
}

export interface Size {
  w: number;
  h: number;
}

/**
 * The view that fits `rect` inside the container with `padPx` screen padding. `widthFit` blends from 0 (contain,
 * centred) to 1 (fit the width, top pinned): a tall page then fills the column instead of floating in it, and a
 * story can ease between the two without a jump. `pinTop` keeps the scale and pins the rect's top `padPx` below the
 * container's top (spreads: the paper starts at the same place whatever the camera frames, so nothing jumps).
 */
export function fitView(rect: Rect, size: Size, padPx = 24, maxK = 12, widthFit = 0, pinTop = false): View {
  const w = Math.max(1, size.w - 2 * padPx);
  const h = Math.max(1, size.h - 2 * padPx);
  const rw = Math.max(1e-6, rect[2] - rect[0]);
  const contain = Math.min(maxK, w / rw, h / Math.max(1e-6, rect[3] - rect[1]));
  const k = contain + (Math.min(maxK, w / rw) - contain) * widthFit;
  const cx = (rect[0] + rect[2]) / 2;
  const centred = (rect[1] + rect[3]) / 2 - size.h / (2 * k);
  const pinned = rect[1] - padPx / k;
  return { x: cx - size.w / (2 * k), y: pinTop ? pinned : centred + (pinned - centred) * widthFit, k };
}

/** Interpolator between two views for the given container size (smooth zoom). */
export function viewInterpolator(a: View, b: View, size: Size): (t: number) => View {
  const toZ = (v: View): [number, number, number] => [v.x + size.w / (2 * v.k), v.y + size.h / (2 * v.k), size.w / v.k];
  const i = smoothZoom(toZ(a), toZ(b));
  return (t) => {
    const [cx, cy, width] = i(t);
    const k = size.w / width;
    return { x: cx - size.w / (2 * k), y: cy - size.h / (2 * k), k };
  };
}

/** Zoom by `factor` keeping the page point under (px, py) fixed. */
export function zoomAt(v: View, factor: number, px: number, py: number, minK: number, maxK: number): View {
  const k = Math.min(maxK, Math.max(minK, v.k * factor));
  const ax = v.x + px / v.k;
  const ay = v.y + py / v.k;
  return { x: ax - px / k, y: ay - py / k, k };
}

export const sameRect = (a?: Rect, b?: Rect) => a === b || (!!a && !!b && a.every((n, i) => Math.abs(n - b[i]) < 0.01));
