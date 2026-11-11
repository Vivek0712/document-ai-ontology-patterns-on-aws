/** /showcase/:id — loads the storyboard and its payload once (cached by story, package and identity). */
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { NotFound } from "../../app/NotFound";
import { useWorkspace } from "../../app/workspace";
import { useApi, useMe, useSession } from "../../api/context";
import { RpApiError } from "../../api/types";
import { Button, ErrorState, Skeleton } from "../../design";
import { Player } from "../../showcase/engine/Player";
import { showcaseMeta } from "../../showcase/catalog";
import { STORYBOARDS } from "../../showcase/stories/registry";

export default function ShowcaseRoute() {
  const { id = "" } = useParams();
  const loader = STORYBOARDS[id];
  if (!loader) return <NotFound />;
  return <LiveShowcase id={id} />;
}

function LiveShowcase({ id }: { id: string }) {
  const api = useApi();
  const { sub } = useMe();
  const { apiAs } = useSession();
  const { packageId, setSubject } = useWorkspace();
  const [search] = useSearchParams();
  // story-specific URL parameters (`ShowcaseMeta.params`, e.g. SC-11's `ex`) are part of the cache key
  const params = Object.fromEntries((showcaseMeta(id)?.params ?? []).flatMap((k) => (search.get(k) ? [[k, search.get(k)!]] : [])));
  const q = useQuery({
    queryKey: ["showcase", id, sub, packageId, params],
    staleTime: Infinity,
    queryFn: async () => {
      const story = await STORYBOARDS[id]();
      const loaded = await story.load({ api, packageId, peer: apiAs, params });
      return { story, loaded };
    },
  });
  // the top bar follows the package the story plays on (read-only) while it is on screen
  const subject = q.data?.loaded.subjectPackageId;
  useEffect(() => {
    setSubject(subject && subject !== packageId ? subject : null);
    return () => setSubject(null);
  }, [subject, packageId, setSubject]);
  if (q.error) {
    const e = q.error instanceof RpApiError ? q.error : undefined;
    return (
      <div className="mx-auto max-w-xl p-8">
        <ErrorState
          title="This showcase could not load its payload"
          message={q.error instanceof Error ? q.error.message : String(q.error)}
          code={e?.code}
          requestId={e?.requestId}
          action={<Button size="sm" onClick={() => void q.refetch()}>Retry</Button>}
        />
      </div>
    );
  }
  if (!q.data)
    return (
      <div className="grid h-full grid-cols-[1fr_340px] gap-px">
        <Skeleton className="m-6 rounded-md" />
        <div className="flex flex-col gap-3 p-4">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-4 w-56" />
          <Skeleton className="h-4 w-44" />
        </div>
      </div>
    );
  return <Player story={q.data.story} loaded={q.data.loaded} />;
}
