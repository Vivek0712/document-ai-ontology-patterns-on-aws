import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "./styles/index.css";
import { QueryClientProvider } from "@tanstack/react-query";
import { LazyMotion, domAnimation } from "framer-motion";
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import type { RuntimeConfig } from "@contracts/api";
import { IS_MOCK, SessionProvider } from "./api/context";
import { createQueryClient } from "./api/queries";
import { App } from "./app/App";
import { PrefsProvider, usePrefs } from "./app/prefs";
import { WorkspaceProvider } from "./app/workspace";
import { loadRuntimeConfig } from "./auth/config";
import { ErrorState, Skeleton, Toaster, TooltipProvider } from "./design";

const queryClient = createQueryClient();

function Boot() {
  const [config, setConfig] = useState<RuntimeConfig>();
  const [error, setError] = useState<string>();
  const { resolvedTheme } = usePrefs();
  useEffect(() => {
    if (IS_MOCK) return;
    loadRuntimeConfig().then(setConfig, (e: Error) => setError(e.message));
  }, []);
  if (error) {
    return (
      <div className="mx-auto max-w-lg p-8">
        <ErrorState title="Configuration could not be loaded" message={`${error}. For local development without AWS run npm run dev:mock.`} />
      </div>
    );
  }
  if (!IS_MOCK && !config)
    return (
      <div className="flex h-full items-center justify-center">
        <Skeleton className="h-4 w-40" />
      </div>
    );
  return (
    <SessionProvider config={config}>
      <WorkspaceProvider>
        <App />
      </WorkspaceProvider>
      <Toaster theme={resolvedTheme} />
    </SessionProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <PrefsProvider>
        <QueryClientProvider client={queryClient}>
          <LazyMotion features={domAnimation} strict>
            <TooltipProvider>
              <Boot />
            </TooltipProvider>
          </LazyMotion>
        </QueryClientProvider>
      </PrefsProvider>
    </BrowserRouter>
  </StrictMode>,
);
