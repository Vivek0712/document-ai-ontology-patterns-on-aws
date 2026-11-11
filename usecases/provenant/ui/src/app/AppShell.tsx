import { Suspense, useState } from "react";
import { Outlet } from "react-router-dom";
import { API_MODE } from "../api/context";
import { Skeleton } from "../design";
import { CommandPalette } from "./CommandPalette";
import { usePrefs } from "./prefs";
import { ShortcutSheet } from "./ShortcutSheet";
import { useGlobalShortcuts } from "./shortcuts";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

/** Sidebar + top bar + scrolling content + footer. */
export function AppShell() {
  const prefs = usePrefs();
  const [palette, setPalette] = useState(false);
  const [help, setHelp] = useState(false);
  useGlobalShortcuts({
    palette: () => setPalette((o) => !o),
    theme: prefs.toggleTheme,
    sidebar: () => prefs.setSidebarCollapsed(!prefs.sidebarCollapsed),
    help: () => setHelp(true),
  });

  return (
    <div className="flex h-full overflow-hidden bg-bg">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onOpenPalette={() => setPalette(true)} />
        <main id="main" className="min-h-0 flex-1 overflow-y-auto">
          <Suspense fallback={<RouteFallback />}>
            <Outlet />
          </Suspense>
        </main>
        <footer className="flex h-[var(--footer-h)] shrink-0 items-center gap-4 border-t border-border bg-bg-subtle px-4 text-2xs text-text-2">
          <span>Fictitious data · NewAge Global Fintech · constructed documents</span>
          <span className="ml-auto hidden sm:inline">
            {API_MODE === "http" ? "Live API" : API_MODE === "replay" ? "Replay mode · dev only" : "Mock API · dev only · not a recording"}
          </span>
        </footer>
      </div>
      <CommandPalette open={palette} onOpenChange={setPalette} />
      <ShortcutSheet open={help} onOpenChange={setHelp} />
    </div>
  );
}

function RouteFallback() {
  return (
    <div className="mx-auto flex max-w-content flex-col gap-3 px-6 pt-6">
      <Skeleton className="h-5 w-48" />
      <Skeleton className="h-4 w-96" />
      <Skeleton className="mt-4 h-64 w-full" />
    </div>
  );
}
