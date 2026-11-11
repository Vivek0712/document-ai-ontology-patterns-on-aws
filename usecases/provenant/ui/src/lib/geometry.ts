/**
 * Canonical bbox frame: PDF points, top-left origin, 1-based page.
 * Scale to pixels by imageWidthPx / pageWidth; never flip an axis.
 * Engine-native frames (Docling BOTTOMLEFT, Textract normalised) are converted here for the stage inspector.
 */
import type { BBox, CanonicalRole } from "@contracts/api";

/**
 * A rect in image pixels ({x, y, w, h}) for the v1 workbench page view. Not to be confused with
 * `viz/geom.ts` `Rect`, a [x0, y0, x1, y1] tuple in PDF points used by PageCanvas.
 */
export interface PixelRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function toPixels(b: BBox, pageWidth: number, imageWidthPx: number): PixelRect {
  const s = imageWidthPx / pageWidth;
  return { x: b.x0 * s, y: b.y0 * s, w: (b.x1 - b.x0) * s, h: (b.y1 - b.y0) * s };
}

export function union(boxes: BBox[]): BBox | undefined {
  if (!boxes.length) return undefined;
  return boxes.reduce((a, b) => ({
    page: a.page,
    x0: Math.min(a.x0, b.x0),
    y0: Math.min(a.y0, b.y0),
    x1: Math.max(a.x1, b.x1),
    y1: Math.max(a.y1, b.y1),
  }));
}

export function fmtBox(b: BBox | undefined): string {
  if (!b) return "—";
  const r = (n: number) => String(Math.round(n * 10) / 10);
  return `p.${b.page} · x0 ${r(b.x0)}, y0 ${r(b.y0)}, x1 ${r(b.x1)}, y1 ${r(b.y1)}`;
}

/** An engine block drawn on the page in the stage inspector. */
export interface EngineBlock {
  id: string;
  page: number;
  label: string;
  role: CanonicalRole | "other";
  text?: string;
  bbox: BBox;
}

const DOCLING_ROLE: Record<string, CanonicalRole> = {
  title: "title",
  section_header: "section_header",
  text: "text",
  paragraph: "text",
  list_item: "list_item",
  table: "table",
  caption: "caption",
  footnote: "footnote",
  picture: "picture",
  formula: "formula",
  page_header: "page_header",
  page_footer: "page_footer",
  key_value: "key_value",
};

const TEXTRACT_ROLE: Record<string, CanonicalRole> = {
  LAYOUT_TITLE: "title",
  LAYOUT_SECTION_HEADER: "section_header",
  LAYOUT_TEXT: "text",
  LAYOUT_LIST: "list_item",
  LAYOUT_TABLE: "table",
  LAYOUT_FIGURE: "picture",
  LAYOUT_HEADER: "page_header",
  LAYOUT_FOOTER: "page_footer",
  LAYOUT_KEY_VALUE: "key_value",
  KEY_VALUE_SET: "key_value",
  TABLE: "table",
  CELL: "table_cell",
};

interface DoclingProv {
  page_no: number;
  bbox: { l: number; t: number; r: number; b: number; coord_origin?: string };
}
interface DoclingItem {
  self_ref: string;
  label: string;
  text?: string;
  prov?: DoclingProv[];
}
interface DoclingDoc {
  pages?: Record<string, { size: { width: number; height: number } }>;
  texts?: DoclingItem[];
  tables?: DoclingItem[];
  pictures?: DoclingItem[];
}

/** DoclingDocument JSON -> canonical blocks (BOTTOMLEFT origin flipped with the page height). */
export function doclingBlocks(doc: DoclingDoc): EngineBlock[] {
  const out: EngineBlock[] = [];
  for (const item of [...(doc.texts ?? []), ...(doc.tables ?? []), ...(doc.pictures ?? [])]) {
    for (const p of item.prov ?? []) {
      const h = doc.pages?.[String(p.page_no)]?.size.height ?? 792;
      const bl = (p.bbox.coord_origin ?? "BOTTOMLEFT").toUpperCase() === "BOTTOMLEFT";
      const y0 = bl ? h - p.bbox.t : p.bbox.t;
      const y1 = bl ? h - p.bbox.b : p.bbox.b;
      out.push({
        id: item.self_ref,
        page: p.page_no,
        label: item.label,
        role: DOCLING_ROLE[item.label] ?? "other",
        text: item.text,
        bbox: { page: p.page_no, x0: p.bbox.l, y0: Math.min(y0, y1), x1: p.bbox.r, y1: Math.max(y0, y1) },
      });
    }
  }
  return out;
}

interface TextractBlock {
  BlockType: string;
  Id: string;
  Page?: number;
  Text?: string;
  Geometry?: { BoundingBox: { Left: number; Top: number; Width: number; Height: number } };
}

/** Textract AnalyzeDocument response (one page) -> canonical blocks (normalised geometry x page size). */
export function textractBlocks(resp: { Blocks?: TextractBlock[] }, page: number, width = 612, height = 792): EngineBlock[] {
  return (resp.Blocks ?? [])
    .filter((b) => b.Geometry && b.BlockType !== "PAGE" && b.BlockType !== "WORD" && b.BlockType !== "LINE")
    .map((b) => {
      const g = b.Geometry!.BoundingBox;
      return {
        id: b.Id,
        page: b.Page ?? page,
        label: b.BlockType,
        role: TEXTRACT_ROLE[b.BlockType] ?? "other",
        text: b.Text,
        bbox: { page: b.Page ?? page, x0: g.Left * width, y0: g.Top * height, x1: (g.Left + g.Width) * width, y1: (g.Top + g.Height) * height },
      };
    });
}

/** Role colours for overlays (--l1 blue for layout roles, --l2 purple for derived/unit). */
export function roleColor(role: string): string {
  switch (role) {
    case "title":
    case "section_header":
      return "var(--l1)";
    case "table":
    case "table_cell":
      return "var(--l1-alt)";
    case "key_value":
      return "var(--l3)";
    case "picture":
    case "figure":
      return "var(--look)";
    case "unit_label":
    case "marker_link":
      return "var(--l2)";
    case "page_header":
    case "page_footer":
      return "var(--fine)";
    default:
      return "var(--secondary)";
  }
}
