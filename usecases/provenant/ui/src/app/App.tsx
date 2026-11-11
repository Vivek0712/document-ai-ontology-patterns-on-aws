/** Route table. Every screen is lazy so the shell stays small; v1 screens keep their logic. */
import { lazy, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { IS_MOCK, useSession } from "../api/context";
import { Skeleton } from "../design";
import { readPref } from "../lib/storage";
import { AppShell } from "./AppShell";
import { NotFound } from "./NotFound";
import { LegacyPage } from "./Page";

const HomePage = lazy(() => import("../features/home/HomePage"));
const ShowcaseIndex = lazy(() => import("../features/showcases/ShowcaseIndex"));
const ShowcaseRoute = lazy(() => import("../features/showcases/ShowcaseRoute"));
const UnitsExplorer = lazy(() => import("../features/units/UnitsExplorer"));
const DevUiPage = lazy(() => import("../features/devui/DevUiPage"));
const TraceScreen = lazy(() => import("../features/trace/TraceScreen"));
const PiiScreen = lazy(() => import("../features/governance/PiiScreen"));
const AccessScreen = lazy(() => import("../features/governance/AccessScreen"));
const ReviewScreen = lazy(() => import("../features/governance/ReviewScreen"));
const OntologyScreen = lazy(() => import("../features/knowledge/OntologyScreen"));
const GovernedScreen = lazy(() => import("../features/knowledge/GovernedScreen"));
const GraphScreen = lazy(() => import("../features/knowledge/GraphScreen"));
const EvaluateScreen = lazy(() => import("../features/evaluate/EvaluateScreen"));
const CostScreen = lazy(() => import("../features/cost/CostScreen"));
const EnvironmentScreen = lazy(() => import("../features/settings/EnvironmentScreen"));
const W = {
  Packages: lazy(() => import("../features/workbench/screens/PackagesScreen").then((m) => ({ default: m.PackagesScreen }))),
  Package: lazy(() => import("../features/workbench/screens/PackageScreen").then((m) => ({ default: m.PackageScreen }))),
  Ask: lazy(() => import("../features/workbench/screens/AskScreen").then((m) => ({ default: m.AskScreen }))),
  Runs: lazy(() => import("../features/workbench/screens/RunsScreen").then((m) => ({ default: m.RunsScreen }))),
  Run: lazy(() => import("../features/workbench/screens/RunsScreen").then((m) => ({ default: m.RunScreen }))),
  Document: lazy(() => import("../features/workbench/screens/DocumentScreen").then((m) => ({ default: m.DocumentScreen }))),
  DocUnits: lazy(() => import("../features/workbench/screens/UnitsScreen").then((m) => ({ default: m.UnitsScreen }))),
  Unit: lazy(() => import("../features/workbench/screens/UnitScreen").then((m) => ({ default: m.UnitScreen }))),

  Pipelines: lazy(() => import("../features/workbench/screens/PipelinesScreen").then((m) => ({ default: m.PipelinesScreen }))),
  SignIn: lazy(() => import("../features/workbench/screens/SignInScreen").then((m) => ({ default: m.SignInScreen }))),
};

export function App() {
  const { session } = useSession();
  if (!session) return <SignInGate />;
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<HomePage />} />
        <Route path="showcase" element={<ShowcaseIndex />} />
        <Route path="showcase/:id" element={<ShowcaseRoute />} />
        <Route path="s/:id/:step" element={<ShortShowcaseLink />} />

        <Route path="workbench" element={<Navigate to="/workbench/packages" replace />} />
        <Route path="workbench/packages" element={<LegacyPage><W.Packages /></LegacyPage>} />
        <Route path="workbench/packages/:packageId" element={<LegacyPage><W.Package /></LegacyPage>} />
        <Route path="workbench/packages/:packageId/ask" element={<W.Ask />} />
        <Route path="workbench/packages/:packageId/runs" element={<W.Runs />} />
        <Route path="workbench/docs/:docId" element={<LegacyPage><W.Document /></LegacyPage>} />
        <Route path="workbench/docs/:docId/units" element={<LegacyPage><W.DocUnits /></LegacyPage>} />
        <Route path="workbench/units" element={<UnitsExplorer />} />
        <Route path="workbench/units/:unitId" element={<LegacyPage><W.Unit /></LegacyPage>} />
        <Route path="workbench/trace/:unitId" element={<TraceScreen />} />
        <Route path="runs/:runId" element={<W.Run />} />
        <Route path="settings" element={<Navigate to="/settings/pipelines" replace />} />
        <Route path="settings/pipelines" element={<LegacyPage><W.Pipelines /></LegacyPage>} />
        <Route path="settings/pipelines/:view" element={<LegacyPage><W.Pipelines /></LegacyPage>} />
        <Route path="settings/environment" element={<EnvironmentScreen />} />

        <Route path="knowledge" element={<Navigate to="/knowledge/ontology" replace />} />
        <Route path="knowledge/ontology" element={<OntologyScreen />} />
        <Route path="knowledge/governed" element={<GovernedScreen />} />
        <Route path="knowledge/graph" element={<GraphScreen />} />
        <Route path="governance" element={<Navigate to="/governance/pii" replace />} />
        <Route path="governance/pii" element={<PiiScreen />} />
        <Route path="governance/access" element={<AccessScreen />} />
        <Route path="governance/review" element={<ReviewScreen />} />
        <Route path="evaluate" element={<EvaluateScreen />} />
        <Route path="cost" element={<CostScreen />} />
        {import.meta.env.DEV && <Route path="dev/ui" element={<DevUiPage />} />}

        {/* earlier short links keep working */}
        <Route path="w/*" element={<LegacyRedirect from="/w/" to="/workbench/" />} />
        <Route path="p/*" element={<LegacyRedirect from="/p/" to="/workbench/packages/" />} />
        <Route path="d/*" element={<LegacyRedirect from="/d/" to="/workbench/docs/" />} />
        <Route path="u/*" element={<LegacyRedirect from="/u/" to="/workbench/units/" />} />
        <Route path="region/*" element={<LegacyRedirect from="/region/" to="/workbench/trace/" />} />
        <Route path="pipelines/*" element={<LegacyRedirect from="/pipelines" to="/settings/pipelines" />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

function LegacyRedirect({ from, to }: { from: string; to: string }) {
  const loc = useLocation();
  return <Navigate to={to + loc.pathname.slice(from.length) + loc.search} replace />;
}

function ShortShowcaseLink() {
  const { id, step } = useParams();
  return <Navigate to={`/showcase/${id}?step=${step}`} replace />;
}

/** Mock mode signs in as the remembered fixture user automatically; real mode shows the Cognito form. */
function SignInGate() {
  const { signInMock } = useSession();
  const [tried, setTried] = useState(false);
  const auto = IS_MOCK && !tried && readPref("mockUser") !== "signed-out";
  useEffect(() => {
    if (!auto) return;
    const user = readPref("mockUser") === "b" ? "b" : "a";
    void signInMock(user).finally(() => setTried(true));
  }, [auto, signInMock]);
  if (auto)
    return (
      <div className="flex h-full items-center justify-center">
        <Skeleton className="h-4 w-40" />
      </div>
    );
  return (
    <div className="legacy">
      <W.SignIn />
    </div>
  );
}
