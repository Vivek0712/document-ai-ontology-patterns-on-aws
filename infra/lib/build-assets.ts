/**
 * Lambda code and layers produced by scripts/build_lambdas.sh (no Docker bundling).
 * The build directory defaults to <repo>/build and can be overridden with context `rp:buildDir`
 * or env RP_BUILD_DIR (tests point it at a stub tree).
 */
import * as fs from 'fs';
import * as path from 'path';
import { REPO_ROOT } from './names';

export type LayerName = 'core' | 'ingest' | 'agent' | 'api';

export interface BuildManifest {
  codeSha: string;
  codePath: string;
  layers: Partial<Record<LayerName, { requirementsSha: string; path: string; sizeMb?: number }>>;
}

export interface BuildAssets {
  root: string;
  codeDir: string;
  codeSha: string;
  layerDir(name: LayerName): string | undefined;
}

export function loadBuildAssets(buildDir?: string): BuildAssets {
  const root = path.resolve(buildDir ?? process.env.RP_BUILD_DIR ?? path.join(REPO_ROOT, 'build'));
  const manifestPath = path.join(root, 'lambda', 'manifest.json');
  if (!fs.existsSync(manifestPath)) {
    throw new Error(`${manifestPath} not found: run scripts/build_lambdas.sh (make build-lambdas) before cdk synth`);
  }
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf-8')) as BuildManifest;
  const codeDir = path.join(root, manifest.codePath);
  if (!fs.existsSync(path.join(codeDir, 'lambdas')) || !fs.existsSync(path.join(codeDir, 'evidence_core'))) {
    throw new Error(`${codeDir} is incomplete: rerun scripts/build_lambdas.sh`);
  }
  return {
    root,
    codeDir,
    codeSha: manifest.codeSha,
    layerDir(name: LayerName): string | undefined {
      const entry = manifest.layers[name];
      if (!entry) return undefined;
      const dir = path.join(root, entry.path);
      return fs.existsSync(path.join(dir, 'python')) ? dir : undefined;
    },
  };
}
