/**
 * Global workspace context shown in the top bar: the current package and the lane selection.
 * The URL stays the source of truth where a screen has one (`/workbench/packages/:pkg`, `?pkg=`); this context
 * remembers the last choice so links from Home and the palette land somewhere sensible.
 */
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { readPref, writePref } from "../lib/storage";

export type LaneChoice = "1.0" | "2.3.1" | "both";
export const DEFAULT_PACKAGE = "pkg-lending-01";

interface Workspace {
  packageId: string;
  setPackageId(id: string): void;
  /**
   * A showcase whose subject lives in another package publishes it here while it plays; the top bar shows it
   * read-only instead of the workspace package, so the bar never names a package the screen is not showing.
   */
  subject: string | null;
  setSubject(id: string | null): void;
  lane: LaneChoice;
  setLane(l: LaneChoice): void;
}

const Ctx = createContext<Workspace | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [packageId, setPkg] = useState(() => readPref("package") ?? DEFAULT_PACKAGE);
  const [lane, setLaneState] = useState<LaneChoice>(() => (readPref("lane") as LaneChoice | undefined) ?? "both");
  const [subject, setSubject] = useState<string | null>(null);
  const loc = useLocation();

  const setPackageId = useCallback((id: string) => {
    setPkg(id);
    writePref("package", id);
  }, []);
  const setLane = useCallback((l: LaneChoice) => {
    setLaneState(l);
    writePref("lane", l);
  }, []);

  // follow the package in the URL
  useEffect(() => {
    const fromPath = loc.pathname.match(/^\/workbench\/packages\/([^/]+)/)?.[1];
    const fromQuery = new URLSearchParams(loc.search).get("pkg");
    const id = fromPath ? decodeURIComponent(fromPath) : fromQuery;
    if (id && id !== packageId) setPackageId(id);
  }, [loc.pathname, loc.search, packageId, setPackageId]);

  const value = useMemo(() => ({ packageId, setPackageId, lane, setLane, subject, setSubject }), [packageId, setPackageId, lane, setLane, subject]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkspace(): Workspace {
  const c = useContext(Ctx);
  if (!c) throw new Error("useWorkspace outside WorkspaceProvider");
  return c;
}
