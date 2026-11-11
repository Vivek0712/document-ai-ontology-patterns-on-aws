import { ChevronsUpDown, FileStack, Lock, LogOut, Moon, Search, Sun, UserRound, Zap } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import { API_MODE, IS_MOCK, useSession } from "../api/context";
import { usePackages } from "../api/queries";
import {
  Badge,
  DropdownMenu,
  DropdownMenuCheckItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  IconButton,
  Kbd,
  SegmentedControl,
  Tooltip,
  cn,
} from "../design";
import { usePrefs } from "./prefs";
import { type LaneChoice, useWorkspace } from "./workspace";

export function TopBar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const { resolvedTheme, toggleTheme } = usePrefs();
  const { lane, setLane } = useWorkspace();
  return (
    <header className="flex h-[var(--topbar-h)] shrink-0 items-center gap-2 border-b border-border bg-bg-subtle px-3">
      <PackageSwitcher />
      <span className="mx-1 h-4 w-px bg-border" aria-hidden />
      <SegmentedControl<LaneChoice>
        aria-label="Pipeline lanes"
        size="sm"
        value={lane}
        onValueChange={setLane}
        items={[
          { value: "1.0", label: "Naive · 1.0", title: "Text layer + fixed 300-token chunks (the control lane)" },
          { value: "2.3.1", label: "Evidence units · 2.3.1", title: "Layout-aware evidence units + ontology" },
          { value: "both", label: "Both", title: "Both lanes side by side" },
        ]}
      />
      <DataModeBadge />
      <div className="ml-auto flex items-center gap-1.5">
        <button
          type="button"
          onClick={onOpenPalette}
          className="hidden h-7 w-56 items-center gap-2 rounded-sm border border-border bg-surface px-2 text-xs text-text-2 transition-colors duration-fast hover:border-border-strong hover:text-text-2 md:flex"
        >
          <Search className="size-3.5" aria-hidden />
          <span className="flex-1 text-left">Search or jump to…</span>
          <Kbd>⌘</Kbd>
          <Kbd>K</Kbd>
        </button>
        <IconButton className="md:hidden" label="Search" shortcut="⌘K" icon={<Search />} onClick={onOpenPalette} />
        <IdentityMenu />
        <IconButton label={resolvedTheme === "dark" ? "Light theme" : "Dark theme"} shortcut="T" icon={resolvedTheme === "dark" ? <Sun /> : <Moon />} onClick={toggleTheme} />
      </div>
    </header>
  );
}

function PackageSwitcher() {
  const { packageId, setPackageId, subject } = useWorkspace();
  const { data } = usePackages();
  const nav = useNavigate();
  const { pathname } = useLocation();
  const current = data?.items.find((p) => p.packageId === packageId);
  if (subject) return <SubjectChip packageId={subject} name={data?.items.find((p) => p.packageId === subject)?.name} workspace={current?.name ?? packageId} />;
  const choose = (id: string) => {
    setPackageId(id);
    const m = pathname.match(/^\/workbench\/packages\/[^/]+(\/.*)?$/);
    if (m) nav(`/workbench/packages/${encodeURIComponent(id)}${m[1] ?? ""}`);
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button type="button" className="flex h-7 min-w-0 items-center gap-2 rounded-sm px-2 text-sm text-text hover:bg-surface-2" aria-label="Switch package">
          <FileStack className="size-4 text-text-2" aria-hidden />
          <span className="max-w-[220px] truncate font-medium">{current?.name ?? packageId}</span>
          <ChevronsUpDown className="size-3.5 text-text-3" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-80">
        <DropdownMenuLabel>Packages you can read</DropdownMenuLabel>
        {(data?.items ?? []).map((p) => (
          <DropdownMenuCheckItem key={p.packageId} checked={p.packageId === packageId} onSelect={() => choose(p.packageId)} detail={`${p.packageId} · ${p.docCount} docs`}>
            {p.name}
          </DropdownMenuCheckItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem icon={<FileStack />} onSelect={() => nav("/workbench/packages")}>
          All packages
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** While a showcase plays on another package than the workspace's: that package, read-only, with a lock. */
function SubjectChip({ packageId, name, workspace }: { packageId: string; name?: string; workspace: string }) {
  return (
    <Tooltip content={`This showcase found its subject by document type in ${packageId}. The workspace package (${workspace}) comes back when you leave it.`}>
      <span className="flex h-7 min-w-0 items-center gap-2 rounded-sm px-2 text-sm text-text" role="status" aria-label={`Showcase subject: ${name ?? packageId} (${packageId})`}>
        <Lock className="size-3.5 text-text-2" aria-hidden />
        <span className="text-xs text-text-2">Showcase subject</span>
        <span className="max-w-[200px] truncate font-medium">{name ?? packageId}</span>
        <span className="hidden font-mono text-mono-sm text-text-2 xl:inline">{packageId}</span>
      </span>
    </Tooltip>
  );
}

function DataModeBadge() {
  if (API_MODE === "http") {
    return (
      <Tooltip content="Live API · Amazon API Gateway + Cognito">
        <Badge dot tone="agent" className="ml-1">Live</Badge>
      </Tooltip>
    );
  }
  return (
    <Tooltip content={API_MODE === "replay" ? "Replay of recorded runs. Dev only." : "Mock API: v1 contract fixtures plus X-ray exports from the local pipeline (not AWS). Dev only."}>
      <Badge tone="running" className="ml-1 border-dashed font-mono uppercase tracking-wide" data-testid="data-mode">
        {API_MODE === "replay" ? "Replay" : "Mock"}
      </Badge>
    </Tooltip>
  );
}

function IdentityMenu() {
  const { session, signOut, signInMock, mockControls } = useSession();
  const me = session?.me;
  if (!me) return null;
  const letter = me.username?.endsWith("-b") || me.email?.startsWith("user-b") ? "B" : me.username?.endsWith("-a") || me.email?.startsWith("user-a") ? "A" : (me.email ?? me.username ?? "?").slice(0, 1).toUpperCase();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button type="button" className="flex h-7 items-center gap-1.5 rounded-sm px-1.5 text-sm text-text-2 hover:bg-surface-2 hover:text-text" aria-label="Identity">
          <span className={cn("flex size-5 items-center justify-center rounded-full bg-surface-3 text-2xs font-semibold text-text")}>{letter}</span>
          <span className="hidden max-w-[150px] truncate lg:inline">User {letter}</span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <div className="px-2 py-1.5">
          <div className="text-sm text-text">{me.email ?? me.username}</div>
          <div className="text-xs text-text-2">{me.groups.join(", ") || "no groups"}</div>
          <div className="font-mono text-mono-sm text-text-2">sub {me.sub.slice(0, 8)}…</div>
        </div>
        {IS_MOCK && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Switch identity (mock)</DropdownMenuLabel>
            <DropdownMenuCheckItem checked={letter === "A"} onSelect={() => void signInMock("a")} detail="lending-analyst">
              User A
            </DropdownMenuCheckItem>
            <DropdownMenuCheckItem checked={letter === "B"} onSelect={() => void signInMock("b")} detail="markets-analyst">
              User B
            </DropdownMenuCheckItem>
            {mockControls && (
              <DropdownMenuItem icon={<Zap />} onSelect={() => mockControls.fastForward()}>
                Finish running pipelines now
              </DropdownMenuItem>
            )}
          </>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem icon={IS_MOCK ? <UserRound /> : <LogOut />} onSelect={signOut}>
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
