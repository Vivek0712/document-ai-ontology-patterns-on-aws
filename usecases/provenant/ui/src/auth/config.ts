import type { RuntimeConfig } from "@contracts/api";

/** Runtime config written by the rp-web stack next to index.html (never bundled). */
export async function loadRuntimeConfig(): Promise<RuntimeConfig> {
  const res = await fetch("/config.json", { cache: "no-store" });
  if (!res.ok) throw new Error(`/config.json: ${res.status} (the rp-web stack writes it next to index.html)`);
  return (await res.json()) as RuntimeConfig;
}
