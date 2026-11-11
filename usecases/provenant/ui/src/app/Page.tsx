import { ChevronRight, Play } from "lucide-react";
import { type ReactNode, useEffect } from "react";
import { Link } from "react-router-dom";
import { Button, cn } from "../design";

export const APP_NAME = "Provenant";
export const TAGLINE = "Every answer, provenance included.";

/** The browser tab title: "<page> · Provenant" (the app name alone when there is no page title). */
export function useDocumentTitle(title?: ReactNode) {
  const text = typeof title === "string" && title ? `${title} · ${APP_NAME}` : APP_NAME;
  useEffect(() => {
    document.title = text;
  }, [text]);
}

/** Standard page container: max 1440, 24 px gutters (16 on phones), 20 px top. */
export function Page({ children, className, wide }: { children: ReactNode; className?: string; wide?: boolean }) {
  return <div className={cn("mx-auto w-full px-4 pb-10 pt-5 sm:px-6", wide ? "max-w-none" : "max-w-content", className)}>{children}</div>;
}

export interface Crumb {
  label: ReactNode;
  to?: string;
}

/** Breadcrumb + title (16/24) + one-line description + page actions. */
export function PageHeader({ crumbs, title, description, actions, meta }: { crumbs?: Crumb[]; title: ReactNode; description?: ReactNode; actions?: ReactNode; meta?: ReactNode }) {
  useDocumentTitle(title);
  return (
    <div className="mb-5 flex flex-wrap items-end gap-x-4 gap-y-2">
      <div className="min-w-0 flex-1">
        {crumbs && crumbs.length > 0 && (
          <nav aria-label="Breadcrumb" className="mb-1 flex flex-wrap items-center gap-1 text-xs text-text-2">
            {crumbs.map((c, i) => (
              <span key={i} className="flex items-center gap-1">
                {c.to ? (
                  <Link to={c.to} className="hover:text-text-2">
                    {c.label}
                  </Link>
                ) : (
                  <span>{c.label}</span>
                )}
                {i < crumbs.length - 1 && <ChevronRight className="size-3" aria-hidden />}
              </span>
            ))}
          </nav>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg">{title}</h1>
          {meta}
        </div>
        {description && <p className="mt-0.5 max-w-3xl text-sm text-text-2">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

/** Wraps a v1 workbench screen: v2 page frame + the legacy class scope restyled onto the tokens. */
export function LegacyPage({ children }: { children: ReactNode }) {
  return (
    <Page>
      <div className="legacy">{children}</div>
    </Page>
  );
}

/** A reference screen's entry into its showcase (: one topic, one home, two entry points). */
export function PlayExplainer({ showcaseId }: { showcaseId: string }) {
  return (
    <Button size="sm" variant="secondary" asChild>
      <Link to={`/showcase/${showcaseId}`}>
        <Play />
        Play explainer
      </Link>
    </Button>
  );
}
