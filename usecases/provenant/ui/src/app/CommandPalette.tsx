/** ⌘K command palette (cmdk): navigate anywhere, jump to a showcase step, open a document, run an action. */
import { Command } from "cmdk";
import {
  ArrowRight,
  Contrast,
  FileText,
  Link2,
  type LucideIcon,
  PanelLeft,
  PlayCircle,
  Rows3,
  UserRound,
  Waves,
} from "lucide-react";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { IS_MOCK, useSession } from "../api/context";
import { usePackage } from "../api/queries";
import { Dialog, Kbd, toast } from "../design";
import { SHOWCASES } from "../showcase/catalog";
import { STORY_STEPS } from "../showcase/stories/steps";
import { ROUTE_ICONS, navSections } from "./nav";
import { usePrefs } from "./prefs";
import { useWorkspace } from "./workspace";

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const nav = useNavigate();
  const prefs = usePrefs();
  const { packageId, setLane } = useWorkspace();
  const { signInMock } = useSession();
  const pkg = usePackage(open ? packageId : undefined);
  const run = (fn: () => void) => () => {
    onOpenChange(false);
    fn();
  };
  const go = (to: string) => run(() => nav(to));

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Command palette">
      <Command loop className="flex max-h-[min(520px,70vh)] flex-col" label="Command palette">
        <div className="flex items-center gap-2 border-b border-border px-3">
          <Command.Input autoFocus placeholder="Search pages, showcase steps, documents, actions…" className="h-11 flex-1 bg-transparent text-sm text-text outline-none placeholder:text-text-3" />
          <Kbd>Esc</Kbd>
        </div>
        <Command.List className="min-h-0 flex-1 overflow-y-auto p-1.5 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-2.5 [&_[cmdk-group-heading]]:text-caps [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:text-text-2">
          <Command.Empty className="px-3 py-6 text-center text-sm text-text-2">No matches.</Command.Empty>

          <Command.Group heading="Navigate">
            <Item icon={ROUTE_ICONS.home} onSelect={go("/")} keywords={["start"]}>Home</Item>
            <Item icon={PlayCircle} onSelect={go("/showcase")}>All showcases</Item>
            {navSections(packageId).flatMap((s) =>
              s.items.map((it) => (
                <Item key={`${s.id}-${it.to}`} icon={s.icon} onSelect={go(it.to)} detail={it.status === "next" ? "coming next" : s.label} keywords={[s.label]}>
                  {it.label}
                </Item>
              )),
            )}
            <Item icon={ROUTE_ICONS.diff} onSelect={go("/settings/pipelines")} keywords={["config", "diff"]}>Pipelines diff</Item>
            {import.meta.env.DEV && <Item icon={ROUTE_ICONS.design} onSelect={go("/dev/ui")} keywords={["kitchen", "components"]}>Design system</Item>}
          </Command.Group>

          <Command.Group heading="Showcase steps">
            {SHOWCASES.filter((s) => STORY_STEPS[s.id]).flatMap((s) =>
              STORY_STEPS[s.id].map((st, i) => (
                <Item key={`${s.id}-${st.id}`} icon={PlayCircle} onSelect={go(`/showcase/${s.id}?step=${i + 1}`)} detail={`${s.code} · step ${i + 1}`} keywords={[s.title, s.code]}>
                  {s.title} · {st.title}
                </Item>
              )),
            )}
            {SHOWCASES.filter((s) => s.status === "next").map((s) => (
              <Item key={s.id} icon={PlayCircle} onSelect={go(`/showcase/${s.id}`)} detail={`${s.code} · coming next`}>
                {s.title}
              </Item>
            ))}
          </Command.Group>

          {pkg.data && (
            <Command.Group heading={`Documents · ${pkg.data.package.name}`}>
              {pkg.data.documents.map((d) => (
                <Item key={d.docId} icon={FileText} onSelect={go(`/workbench/units?pkg=${encodeURIComponent(packageId)}&doc=${encodeURIComponent(d.docId)}`)} detail={d.docType ?? undefined} keywords={[d.docId]}>
                  {d.fileName}
                </Item>
              ))}
            </Command.Group>
          )}

          <Command.Group heading="Actions">
            <Item icon={Contrast} onSelect={run(prefs.toggleTheme)} shortcut="T">Toggle theme</Item>
            <Item icon={Waves} onSelect={run(() => prefs.setMotion(prefs.reducedMotion ? "full" : "reduced"))}>
              {prefs.reducedMotion ? "Allow motion" : "Reduce motion"}
            </Item>
            <Item icon={PanelLeft} onSelect={run(() => prefs.setSidebarCollapsed(!prefs.sidebarCollapsed))} shortcut="[">Toggle sidebar</Item>
            <Item icon={Rows3} onSelect={run(() => setLane("both"))} keywords={["lane"]}>Lanes: both</Item>
            <Item icon={Rows3} onSelect={run(() => setLane("1.0"))} keywords={["lane"]}>Lane: 1.0 naive</Item>
            <Item icon={Rows3} onSelect={run(() => setLane("2.3.1"))} keywords={["lane"]}>Lane: 2.3.1 evidence units</Item>
            {IS_MOCK && <Item icon={UserRound} onSelect={run(() => void signInMock("a"))} keywords={["identity"]}>Switch to User A</Item>}
            {IS_MOCK && <Item icon={UserRound} onSelect={run(() => void signInMock("b"))} keywords={["identity"]}>Switch to User B</Item>}
            <Item
              icon={Link2}
              onSelect={run(() => void navigator.clipboard?.writeText(window.location.href).then(() => toast.success("Link copied")))}
              keywords={["share", "url"]}
            >
              Copy link to this view
            </Item>
          </Command.Group>
        </Command.List>
      </Command>
    </Dialog>
  );
}

function Item({ icon: Icon, children, onSelect, detail, shortcut, keywords }: { icon: LucideIcon; children: ReactNode; onSelect: () => void; detail?: string; shortcut?: string; keywords?: string[] }) {
  return (
    <Command.Item
      onSelect={onSelect}
      keywords={keywords}
      className="group flex h-8 cursor-default select-none items-center gap-2 rounded-sm px-2 text-sm text-text-2 data-[selected=true]:bg-surface-2 data-[selected=true]:text-text"
    >
      <Icon className="size-4 shrink-0 text-text-3 group-data-[selected=true]:text-text-2" aria-hidden />
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {detail && <span className="truncate text-xs text-text-2">{detail}</span>}
      {shortcut ? <Kbd>{shortcut}</Kbd> : <ArrowRight className="size-3.5 text-text-3 opacity-0 group-data-[selected=true]:opacity-100" aria-hidden />}
    </Command.Item>
  );
}
