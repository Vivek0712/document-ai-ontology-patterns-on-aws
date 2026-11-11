import { ChevronRight, Home, PanelLeftClose, PanelLeftOpen, Palette, SlidersHorizontal } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { IconButton, Tooltip, cn } from "../design";
import { readPref, writePref } from "../lib/storage";
import { Logo } from "./Logo";
import { type NavItem, type NavSection, isLive, navSections } from "./nav";
import { usePrefs } from "./prefs";
import { useWorkspace } from "./workspace";

/** Score how well `item` matches the current path: exact path > prefix; the best item is the active one. */
function matchScore(item: NavItem, pathname: string): number {
  const path = item.to.split("?")[0];
  if (pathname === path) return 1000 + path.length;
  const prefix = item.activePrefixes?.find((p) => pathname.startsWith(p));
  return prefix ? prefix.length : -1;
}

export function Sidebar() {
  const { sidebarCollapsed: collapsed, setSidebarCollapsed } = usePrefs();
  const { packageId } = useWorkspace();
  const { pathname } = useLocation();
  const sections = navSections(packageId);
  const live = sections.filter(isLive);
  const roadmap = sections.filter((s) => !isLive(s));
  const [open, setOpen] = useState<Set<string>>(() => new Set((readPref("nav.open") ?? "").split(",").filter(Boolean)));
  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      writePref("nav.open", [...next].join(",") || undefined);
      return next;
    });
  const extra: NavItem[] = [
    { label: "Pipelines diff", to: "/settings/pipelines", activePrefixes: ["/settings"] },
    ...(import.meta.env.DEV ? [{ label: "Design system", to: "/dev/ui" }] : []),
  ];
  const all = [{ label: "Home", to: "/" }, ...sections.flatMap((s) => s.items), ...extra];
  const active = all.reduce<{ item?: NavItem; score: number }>(
    (best, it) => {
      const s = matchScore(it, pathname);
      return s > best.score ? { item: it, score: s } : best;
    },
    { score: -1 },
  ).item;

  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col border-r border-border bg-bg-subtle transition-[width] duration-base ease-standard",
        collapsed ? "w-[var(--sidebar-w-collapsed)]" : "w-[var(--sidebar-w)]",
      )}
      aria-label="Primary"
    >
      <div className={cn("flex h-[var(--topbar-h)] shrink-0 items-center gap-2 border-b border-border", collapsed ? "justify-center" : "px-3")}>
        <Link to="/" className="flex min-w-0 items-center gap-2" aria-label="Provenant home">
          <Logo />
          {!collapsed && <span className="truncate text-sm font-semibold tracking-tight text-text">Provenant</span>}
        </Link>
        {!collapsed && (
          <IconButton className="ml-auto" size="sm" label="Collapse sidebar" shortcut="[" icon={<PanelLeftClose />} onClick={() => setSidebarCollapsed(true)} />
        )}
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-2">
        {collapsed ? (
          <div className="flex flex-col items-center gap-1">
            <RailLink to="/" label="Home" icon={<Home />} active={active?.to === "/"} />
            {sections.map((s) => {
              const Icon = s.icon;
              const on = s.items.some((i) => i === active);
              return <RailLink key={s.id} to={s.items[0].to} label={s.label} icon={<Icon />} active={on} />;
            })}
          </div>
        ) : (
          <>
            <SideLink item={{ label: "Home", to: "/" }} active={active?.to === "/"} icon={<Home />} />
            {live.map((s) =>
              s.collapsible ? (
                <CollapsibleSection key={s.id} section={s} active={active} open={open.has(s.id) || pathname.startsWith(s.collapsible.openOn)} onToggle={() => toggle(s.id)} />
              ) : (
                <LiveSection key={s.id} section={s} active={active} open={open.has(s.id)} onToggle={() => toggle(s.id)} />
              ),
            )}
            {roadmap.length > 0 && (
              <div className="mt-4">
                <Disclosure open={open.has("roadmap") || roadmap.some((s) => s.items.includes(active!))} onToggle={() => toggle("roadmap")} count={roadmap.reduce((n, s) => n + s.items.length, 0)}>
                  Roadmap
                </Disclosure>
                {(open.has("roadmap") || roadmap.some((s) => s.items.includes(active!))) &&
                  roadmap.map((s) => (
                    <div key={s.id} className="mt-1.5">
                      <div className="flex h-6 items-center px-2 text-xs text-text-3">{s.label}</div>
                      {s.items.map((it) => (
                        <SideLink key={it.to} item={it} active={it === active} muted />
                      ))}
                    </div>
                  ))}
              </div>
            )}
          </>
        )}
      </nav>

      <div className={cn("border-t border-border p-2", collapsed && "flex flex-col items-center gap-1")}>
        {collapsed ? (
          <>
            <RailLink to="/settings/pipelines" label="Pipelines diff" icon={<SlidersHorizontal />} active={active === extra[0]} />
            <IconButton size="sm" label="Expand sidebar" shortcut="[" icon={<PanelLeftOpen />} onClick={() => setSidebarCollapsed(false)} />
          </>
        ) : (
          extra.map((it) => <SideLink key={it.to} item={it} active={it === active} icon={it.to === "/dev/ui" ? <Palette /> : <SlidersHorizontal />} />)
        )}
      </div>
    </aside>
  );
}

/**
 * A long group (the showcases) that folds to its header: open on its own pages or when the reader opened it, with
 * its items under their sub-headings (the acts).
 */
function CollapsibleSection({ section, active, open, onToggle }: { section: NavSection; active?: NavItem; open: boolean; onToggle(): void }) {
  const items = section.items.filter((i) => i.status !== "next");
  const expanded = open || items.includes(active!);
  return (
    <div className="mt-4">
      <Disclosure open={expanded} onToggle={onToggle} count={items.filter((i) => i.group).length}>
        {section.label}
      </Disclosure>
      {expanded &&
        items.map((it, i) => (
          <div key={it.to}>
            {it.group && it.group !== items[i - 1]?.group && <div className="flex h-6 items-end px-2 pb-0.5 text-caps uppercase text-text-3">{it.group}</div>}
            <SideLink item={it} active={it === active} />
          </div>
        ))}
    </div>
  );
}

/** A group with live items: those first, then its unbuilt ones folded into one "+N coming" row. */
function LiveSection({ section, active, open, onToggle }: { section: NavSection; active?: NavItem; open: boolean; onToggle(): void }) {
  const built = section.items.filter((i) => i.status !== "next");
  const coming = section.items.filter((i) => i.status === "next");
  const expanded = open || coming.includes(active!);
  return (
    <div className="mt-4">
      <div className="flex h-6 items-center gap-1.5 px-2 text-caps uppercase text-text-2">{section.label}</div>
      {built.map((it) => (
        <SideLink key={it.to} item={it} active={it === active} />
      ))}
      {coming.length > 0 && (
        <>
          <Disclosure open={expanded} onToggle={onToggle} count={coming.length} quiet>
            coming
          </Disclosure>
          {expanded && coming.map((it) => <SideLink key={it.to} item={it} active={it === active} muted />)}
        </>
      )}
    </div>
  );
}

function Disclosure({ open, onToggle, count, quiet, children }: { open: boolean; onToggle(): void; count: number; quiet?: boolean; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-expanded={open}
      onClick={onToggle}
      className={cn(
        "flex h-7 w-full items-center gap-1.5 rounded-sm px-2 text-left transition-colors duration-fast hover:bg-surface-2/60",
        quiet ? "text-xs text-text-3 hover:text-text-2" : "text-caps uppercase text-text-2 hover:text-text",
      )}
    >
      <ChevronRight className={cn("size-3.5 shrink-0 transition-transform duration-fast", open && "rotate-90")} aria-hidden />
      {quiet ? (
        <span>
          {open ? "" : "+"}
          {count} {children}
        </span>
      ) : (
        <>
          <span className="flex-1">{children}</span>
          <span className="text-xs normal-case tracking-normal text-text-3">{count}</span>
        </>
      )}
    </button>
  );
}

function SideLink({ item, active, icon, muted }: { item: NavItem; active: boolean; icon?: ReactNode; muted?: boolean }) {
  return (
    <Link
      to={item.to}
      aria-current={active ? "page" : undefined}
      className={cn(
        "relative flex h-7 items-center gap-2 rounded-sm px-2 text-sm transition-colors duration-fast [&_svg]:size-4",
        active ? "bg-surface-2 text-text" : muted ? "text-text-3 hover:bg-surface-2/60 hover:text-text-2" : "text-text-2 hover:bg-surface-2/60 hover:text-text",
      )}
    >
      {active && <span className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-text" aria-hidden />}
      {icon && <span className="text-text-2">{icon}</span>}
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
      {item.hint && <span className="max-w-[88px] truncate font-mono text-2xs text-text-3">{item.hint}</span>}
    </Link>
  );
}

function RailLink({ to, label, icon, active }: { to: string; label: string; icon: ReactNode; active: boolean }) {
  return (
    <Tooltip content={label} side="right">
      <Link
        to={to}
        aria-label={label}
        className={cn(
          "flex size-8 items-center justify-center rounded-sm [&_svg]:size-4",
          active ? "bg-surface-2 text-text" : "text-text-2 hover:bg-surface-2 hover:text-text",
        )}
      >
        {icon}
      </Link>
    </Tooltip>
  );
}
