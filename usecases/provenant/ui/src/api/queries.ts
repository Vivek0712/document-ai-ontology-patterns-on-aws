/**
 * TanStack Query over the v1 API client (and the v2 X-ray route). Query keys always include the identity
 * (sub), so switching User A / User B never serves the other user's cache.
 */
import { QueryClient, useQuery } from "@tanstack/react-query";
import { useApi, useMe } from "./context";
import { RpApiError } from "./types";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        // 4xx answers are final (403 stays 403); retry only network/5xx, twice
        retry: (n, e) => !(e instanceof RpApiError && e.status >= 400 && e.status < 500) && n < 2,
      },
    },
  });
}

export const keys = {
  packages: (sub: string) => ["packages", sub] as const,
  package: (sub: string, pkg: string) => ["package", sub, pkg] as const,
  document: (sub: string, docId: string) => ["document", sub, docId] as const,
  xray: (sub: string, docId: string, page: number, size: number | undefined, runId?: string) => ["xray", sub, docId, page, size ?? "control", runId ?? ""] as const,
};

export function usePackages() {
  const api = useApi();
  const { sub } = useMe();
  return useQuery({ queryKey: keys.packages(sub), queryFn: () => api.listPackages() });
}

export function usePackage(packageId: string | undefined) {
  const api = useApi();
  const { sub } = useMe();
  return useQuery({
    queryKey: keys.package(sub, packageId ?? ""),
    queryFn: () => api.getPackage(packageId!),
    enabled: Boolean(packageId),
  });
}

export function useXray(docId: string | undefined, page: number, size?: number, runId?: string) {
  const api = useApi();
  const { sub } = useMe();
  return useQuery({
    queryKey: keys.xray(sub, docId ?? "", page, size, runId),
    queryFn: () => api.getXray(docId!, page, size, runId),
    enabled: Boolean(docId),
    staleTime: 240_000, // an inspection of immutable bytes; the presigned page image lives 300 s
    placeholderData: (prev) => (prev?.docId === docId && prev?.page === page ? prev : undefined),
  });
}
