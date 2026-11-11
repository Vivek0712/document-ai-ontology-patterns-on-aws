import { Page, PageHeader } from "../../app/Page";
import { SHOWCASE_GROUPS, SHOWCASES } from "../../showcase/catalog";
import { ShowcaseCard } from "./ShowcaseCard";

export default function ShowcaseIndex() {
  const live = SHOWCASES.filter((s) => s.status === "live").length;
  return (
    <Page>
      <PageHeader
        title="Showcases"
        description={`One guided, scrubbable explainer per pattern claim, drawn from real pipeline artifacts. ${live} of ${SHOWCASES.length} live.`}
      />
      <div className="flex flex-col gap-6">
        {SHOWCASE_GROUPS.map((g) => (
          <section key={g}>
            <h2 className="mb-2 text-caps uppercase text-text-2">{g}</h2>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {SHOWCASES.filter((s) => s.group === g).map((s) => (
                <ShowcaseCard key={s.id} s={s} />
              ))}
            </div>
          </section>
        ))}
      </div>
    </Page>
  );
}
