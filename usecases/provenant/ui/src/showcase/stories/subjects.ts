/**
 * Subjects of the pattern showcases (SC-2, SC-3), found by document type and printed identifiers (
 * principle 5): unit and package ids are resolved from the API at load time, never typed.
 */
import type { DocumentItem } from "@contracts/api";
import type { RpApi } from "../../api/types";

/** D1: the quarterly report whose revenue table carries a raised footnote marker. */
export const D1_SUBJECT = { docType: "doc:QuarterlyReport", period: "FY2025-Q3" } as const;

/** D2: the trade confirmation whose coupon carries a step-up footnote on the next page. */
export const D2_SUBJECT = { docType: "doc:TradeConfirmation", security: "XS-2031" } as const;

/**
 * The documents of `docType` the caller can read, with their package. The workspace package is searched first,
 * so a package the presenter picked wins over another one holding the same kind of document.
 */
export async function documentsOfType(api: RpApi, docType: string, preferred?: string): Promise<{ packageId: string; documents: DocumentItem[] }[]> {
  const { items } = await api.listPackages();
  const ids = items.map((p) => p.packageId).sort((a, b) => Number(b === preferred) - Number(a === preferred));
  const out: { packageId: string; documents: DocumentItem[] }[] = [];
  for (const id of ids) {
    const detail = await api.getPackage(id);
    const documents = detail.documents.filter((d) => d.docType === docType);
    if (documents.length) out.push({ packageId: id, documents });
  }
  return out;
}
