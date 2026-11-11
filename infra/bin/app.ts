#!/usr/bin/env node
/**
 * Provenant CDK app. Build Lambda assets first (`make build-lambdas`), then
 * `npx cdk synth --all` / `npx cdk deploy --all` (uses your current AWS credentials; add --profile <name> or
 * export AWS_PROFILE to use a named profile).
 *
 * Context (cdk.json or -c):
 *   rp:account / rp:region      target environment (defaults: the current credentials' account, us-east-1)
 *   rp:availabilityZones        two AZs (default: <region>a, <region>b; fixed so synth makes no lookup)
 *   rp:buildDir                 Lambda build dir (default ../build)
 *   rp:doclingImage             upstream docling-serve-cpu image, pinned by digest
 *   rp:neptuneEngineVersion     Neptune engine version
 *   rp:devOrigins               extra CORS origins for the Vite dev server
 *   rp:agentRuntimeArn          when set, POST /ask is an HTTP proxy (streaming) to the Runtime
 *   rp:askTimeoutMs             /ask integration timeout (> 29000 needs the API Gateway quota raise)
 *   rp:aossAdminArns            operator role ARNs with write access to the AOSS indexes
 */
import { App } from 'aws-cdk-lib';
import { buildApp } from '../lib/app';

const app = new App();
buildApp(app);
app.synth();
