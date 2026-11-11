/**
 * PageCanvas: a page image with an SVG layer system in PDF points (top-left origin), and a
 * camera. The SVG viewBox *is* the camera, so the image and every overlay scale together and stay vector-crisp;
 * strokes use `vector-effect: non-scaling-stroke` and labels size themselves with `usePageCamera().px`.
 *
 * Two ways to drive the camera:
 * - `focus` + `animate` (workbench): the canvas eases to the rect with a smooth zoom (600 ms).
 * - `focus` + `animate={false}` (showcases): the caller interpolates per frame, so a scrubbed or screenshotted
 *   frame is a pure function of (payload, step, progress).
 * With `interactive`, wheel/drag pan and zoom (⌘/Ctrl + wheel or pinch zooms), double-click refits.
 */
import { easeCubicInOut } from "d3-ease";
import { Maximize, Minus, Plus } from "lucide-react";
import {
  createContext,
  type PointerEvent as RPointerEvent,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { IconButton, cn } from "../../design";
import type { Rect } from "../geom";
import { type Size, type View, fitView, sameRect, viewInterpolator, zoomAt } from "./camera";

export interface PageImageSource {
  src: string;
  /** higher-resolution render, swapped in when the zoom outgrows `src` */
  src2x?: string;
  /** dots per inch of `src` (Gold renders are 150) */
  dpi?: number;
}

/** One page of a spread: several pages side by side in one coordinate space (PDF points, top-left origin). */
export interface Sheet {
  image?: PageImageSource;
  /** the sheet's top-left corner in spread coordinates */
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Lay pages out left to right with `gap` points between them. Layers draw a page's rects shifted by
 * `offset(i)` (see `onSheet`); the canvas takes `pageWidth`/`pageHeight` = the spread's extent.
 */
export function spread(pages: { width: number; height: number; image?: PageImageSource }[], gap = 24): { sheets: Sheet[]; width: number; height: number } {
  let x = 0;
  const sheets = pages.map((p) => {
    const s = { image: p.image, x, y: 0, width: p.width, height: p.height };
    x += p.width + gap;
    return s;
  });
  return { sheets, width: Math.max(0, x - gap), height: Math.max(0, ...pages.map((p) => p.height)) };
}

/**
 * A small map of the whole page or spread (120 px wide by default) with the camera's rect, for frames that zoom in
 * on a part of a spread: the viewer keeps both pages in view while reading one row. Pass it as PageCanvas `frame`.
 */
export function MiniMap({ sheets, width: extentW, height: extentH, view, size = 120, opacity = 1, className }: { sheets: Sheet[]; width: number; height: number; view: Rect; size?: number; opacity?: number; className?: string }) {
  if (opacity <= 0 || !extentW || !extentH) return null;
  const k = size / extentW;
  const h = Math.ceil(extentH * k);
  const v = [Math.max(0, view[0]), Math.max(0, view[1]), Math.min(extentW, view[2]), Math.min(extentH, view[3])];
  return (
    <svg width={size} height={h} viewBox={`0 0 ${extentW} ${extentH}`} className={cn("pointer-events-none absolute bottom-3 left-3 rounded-xs border border-border bg-surface/90 shadow-sm", className)} style={{ opacity }} aria-hidden>
      {sheets.map((sh, i) => (
        <g key={i}>
          <rect x={sh.x} y={sh.y} width={sh.width} height={sh.height} fill="#fff" />
          {sh.image && <image href={sh.image.src} x={sh.x} y={sh.y} width={sh.width} height={sh.height} preserveAspectRatio="none" opacity={0.85} />}
        </g>
      ))}
      <rect x={v[0]} y={v[1]} width={Math.max(0, v[2] - v[0])} height={Math.max(0, v[3] - v[1])} fill="none" stroke="var(--text-2)" strokeWidth={1.25} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** A page rect moved onto its sheet of a spread. */
export const onSheet = (r: Rect, sheet: Pick<Sheet, "x" | "y">): Rect => [r[0] + sheet.x, r[1] + sheet.y, r[2] + sheet.x, r[3] + sheet.y];

interface CameraCtx {
  view: View;
  /** the canvas size in CSS px (layers clamp labels inside it) */
  size: Size;
  /** CSS px -> page points at the current zoom: size labels and badges with px(12) */
  px: (n: number) => number;
  /** unique prefix for SVG ids (patterns, masks) */
  uid: string;
}

/**
 * The canvas size before it is measured. 0×0 (the default) renders nothing until the ResizeObserver reports;
 * server-side renders and tests provide a size so every layer is in the markup they check.
 */
export const CanvasSizeHint = createContext<Size>({ w: 0, h: 0 });

const Ctx = createContext<CameraCtx>({ view: { x: 0, y: 0, k: 1 }, size: { w: 0, h: 0 }, px: (n) => n, uid: "pc" });
export const usePageCamera = () => useContext(Ctx);

interface Props {
  image?: PageImageSource;
  pageWidth: number;
  pageHeight: number;
  focus?: Rect;
  /** screen padding around the focus rect, in px */
  padding?: number;
  /** 0 = contain the focus rect (centred), 1 = fit its width with the top pinned; values between blend */
  widthFit?: number;
  /** "top": contain the focus rect but pin its top `padding` px below the frame's top (no dead band, no jumps) */
  align?: "center" | "top";
  animate?: boolean;
  interactive?: boolean;
  /** 0..1, dims the page image (focus emphasis lives in layers, this is for whole-page fades) */
  imageOpacity?: number;
  ariaLabel: string;
  className?: string;
  children?: ReactNode;
  /** HTML overlay pinned to the canvas frame (legends, badges); not transformed by the camera */
  frame?: ReactNode;
  /** several pages in one coordinate space (`spread()`); replaces `image`, and pageWidth/Height are the extent */
  sheets?: Sheet[];
}

let seq = 0;

export function PageCanvas({ image, pageWidth, pageHeight, focus, padding = 24, widthFit = 0, align = "center", animate = true, interactive = false, imageOpacity = 1, ariaLabel, className, children, frame, sheets: sheetsProp }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<Size>(useContext(CanvasSizeHint));
  const page: Rect = useMemo(() => [0, 0, pageWidth, pageHeight], [pageWidth, pageHeight]);
  const target = focus ?? page;
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const viewRef = useRef(view);
  viewRef.current = view;
  const [userMoved, setUserMoved] = useState(false);
  const lastTarget = useRef<Rect>();
  const raf = useRef<number>();
  const uid = useMemo(() => `pc${++seq}`, []);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  // follow the focus rect: instantly (first frame, resize, animate=false) or with an eased smooth zoom
  useLayoutEffect(() => {
    if (!size.w || !size.h) return;
    const changed = !sameRect(lastTarget.current, target);
    const end = fitView(target, size, padding, 12, widthFit, align === "top");
    if (raf.current) cancelAnimationFrame(raf.current);
    if (!changed && userMoved) return;
    const first = lastTarget.current === undefined;
    lastTarget.current = target;
    if (changed) setUserMoved(false);
    const reduce = document.documentElement.dataset.motion === "reduced" || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (!animate || first || !changed || reduce) {
      setView(end);
      return;
    }
    const interp = viewInterpolator(viewRef.current, end, size);
    const t0 = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - t0) / 600);
      setView(interp(easeCubicInOut(t)));
      if (t < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target[0], target[1], target[2], target[3], size.w, size.h, padding, widthFit, align, animate]);

  const fitK = size.w ? fitView(page, size, padding).k : 1;
  const minK = fitK * 0.5;
  const maxK = fitK * 14;

  const zoomBy = useCallback(
    (factor: number, px = size.w / 2, py = size.h / 2) => {
      setUserMoved(true);
      setView((v) => zoomAt(v, factor, px, py, minK, maxK));
    },
    [size.w, size.h, minK, maxK],
  );

  // wheel needs a non-passive listener to prevent page scroll
  useEffect(() => {
    const el = ref.current;
    if (!el || !interactive) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) zoomBy(Math.exp(-e.deltaY * 0.01), e.clientX - r.left, e.clientY - r.top);
      else {
        setUserMoved(true);
        setView((v) => ({ ...v, x: v.x + e.deltaX / v.k, y: v.y + e.deltaY / v.k }));
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [interactive, zoomBy]);

  const drag = useRef<{ x: number; y: number } | null>(null);
  const onPointerDown = (e: RPointerEvent) => {
    if (!interactive || e.button !== 0) return;
    drag.current = { x: e.clientX, y: e.clientY };
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: RPointerEvent) => {
    if (!drag.current) return;
    const dx = e.clientX - drag.current.x;
    const dy = e.clientY - drag.current.y;
    drag.current = { x: e.clientX, y: e.clientY };
    setUserMoved(true);
    setView((v) => ({ ...v, x: v.x - dx / v.k, y: v.y - dy / v.k }));
  };
  const endDrag = () => (drag.current = null);
  const refit = () => {
    setUserMoved(false);
    setView(fitView(target, size, padding, 12, widthFit, align === "top"));
  };

  const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
  const hrefOf = (img?: PageImageSource) => (img?.src2x && view.k * dpr > ((img.dpi ?? 150) / 72) * 1.15 ? img.src2x : img?.src);
  const sheets: Sheet[] = sheetsProp ?? [{ image, x: 0, y: 0, width: pageWidth, height: pageHeight }];
  const ctx = useMemo<CameraCtx>(() => ({ view, size, px: (n: number) => n / view.k, uid }), [view, size, uid]);
  const ready = size.w > 0 && size.h > 0;

  return (
    <div
      ref={ref}
      className={cn("relative h-full min-h-0 w-full select-none overflow-hidden bg-canvas", interactive && "cursor-grab active:cursor-grabbing", className)}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={interactive ? refit : undefined}
    >
      {ready &&
        sheets.map((sh, i) => (
          <div
            key={i}
            aria-hidden
            className="absolute bg-white shadow-page outline outline-1 outline-border-strong"
            style={{ left: (sh.x - view.x) * view.k, top: (sh.y - view.y) * view.k, width: sh.width * view.k, height: sh.height * view.k }}
          />
        ))}
      {ready && (
        <svg
          role="img"
          aria-label={ariaLabel}
          width={size.w}
          height={size.h}
          viewBox={`${view.x} ${view.y} ${size.w / view.k} ${size.h / view.k}`}
          className="on-paper absolute inset-0 block"
        >
          <Ctx.Provider value={ctx}>
            {sheets.map((sh, i) => {
              const href = hrefOf(sh.image);
              return (
                <g key={i}>
                  {href && (
                    <image
                      href={href}
                      x={sh.x}
                      y={sh.y}
                      width={sh.width}
                      height={sh.height}
                      preserveAspectRatio="none"
                      style={{ opacity: imageOpacity, filter: "brightness(var(--page-dim))", transition: "opacity var(--dur-base)" }}
                    />
                  )}
                  <rect x={sh.x} y={sh.y} width={sh.width} height={sh.height} fill="none" stroke="rgb(0 0 0 / 0.12)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
                </g>
              );
            })}
            {children}
          </Ctx.Provider>
        </svg>
      )}
      {frame}
      {interactive && ready && (
        <div className="absolute bottom-3 right-3 flex items-center gap-0.5 rounded-sm border border-border bg-surface/90 p-0.5 shadow-sm backdrop-blur" onPointerDown={(e) => e.stopPropagation()} onDoubleClick={(e) => e.stopPropagation()}>
          <IconButton size="sm" label="Zoom out" icon={<Minus />} onClick={() => zoomBy(1 / 1.4)} />
          <span className="tnum w-11 text-center text-2xs text-text-2">{Math.round((view.k / fitK) * 100)}%</span>
          <IconButton size="sm" label="Zoom in" icon={<Plus />} onClick={() => zoomBy(1.4)} />
          <IconButton size="sm" label="Fit" shortcut="double-click" icon={<Maximize />} onClick={refit} />
        </div>
      )}
    </div>
  );
}
