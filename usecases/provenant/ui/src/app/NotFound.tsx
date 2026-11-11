import { ArrowRight, SearchX } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { Button, Card } from "../design";
import { Page, PageHeader } from "./Page";

/** Every route in the sitemap is built; anything else (a mistyped link, an unknown showcase id) lands here. */
export function NotFound() {
  const { pathname } = useLocation();
  return (
    <Page>
      <PageHeader crumbs={[{ label: "Provenant" }]} title="Not found" />
      <Card className="max-w-3xl p-5">
        <div className="flex items-start gap-3">
          <SearchX className="mt-0.5 size-4 shrink-0 text-text-3" aria-hidden />
          <div className="flex flex-col gap-3">
            <p className="text-sm text-text">
              There is no page at <span className="font-mono text-mono-sm">{pathname}</span>.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button asChild size="sm" variant="secondary">
                <Link to="/showcase">
                  Browse the showcases
                  <ArrowRight />
                </Link>
              </Button>
              <Button asChild size="sm" variant="ghost">
                <Link to="/">Home</Link>
              </Button>
            </div>
          </div>
        </div>
      </Card>
    </Page>
  );
}
