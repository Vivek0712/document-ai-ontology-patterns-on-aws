/**
 * The mock world as tests read it (node only): `world.json` with every package shard merged in, so a test can look
 * up any unit, region or payload synchronously. The MockApi built from it has nothing left to fetch lazily.
 */
import { readFileSync } from "node:fs";
import { mergeShard, type World } from "../mocks/world";

export const MOCK_ROOT = new URL("../../mock-public/mock/", import.meta.url);

export function fullWorld(): World {
  const world = JSON.parse(readFileSync(new URL("world.json", MOCK_ROOT), "utf8")) as World;
  for (const path of Object.values(world.shards ?? {})) {
    mergeShard(world, JSON.parse(readFileSync(new URL(path, MOCK_ROOT), "utf8")) as Record<string, unknown>);
  }
  delete world.shards;
  return world;
}
