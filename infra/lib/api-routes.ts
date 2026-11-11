/**
 * REST routes of `rp-api`. The resource templates are the ones the Python router keys on
 * (services/lambdas/api/routes/__init__.py), so a route is either here and in the router, or nowhere.
 */
export type RouteTarget = 'rp-api' | 'rp-upload-url' | 'rp-get-region-http' | 'rp-xray' | 'ask';

export interface Route {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE';
  path: string;
  target: RouteTarget;
}

export const ROUTES: Route[] = [
  { method: 'GET', path: '/me', target: 'rp-api' },
  { method: 'GET', path: '/packages', target: 'rp-api' },
  { method: 'POST', path: '/packages', target: 'rp-api' },
  { method: 'GET', path: '/packages/{packageId}', target: 'rp-api' },
  { method: 'DELETE', path: '/packages/{packageId}', target: 'rp-api' },
  { method: 'POST', path: '/packages/{packageId}/upload-url', target: 'rp-upload-url' },
  { method: 'GET', path: '/packages/{packageId}/status', target: 'rp-api' },
  { method: 'GET', path: '/documents/{docId}', target: 'rp-api' },
  { method: 'GET', path: '/documents/{docId}/lanes/{pv}/stages/{stageId}', target: 'rp-api' },
  { method: 'GET', path: '/documents/{docId}/lanes/{pv}/stages/{stageId}/overlay', target: 'rp-api' },
  { method: 'GET', path: '/documents/{docId}/lanes/{pv}/events', target: 'rp-api' },
  { method: 'GET', path: '/documents/{docId}/lanes/{pv}/units', target: 'rp-api' },
  { method: 'POST', path: '/documents/{docId}/reprocess', target: 'rp-api' },
  { method: 'GET', path: '/units/{unitId}', target: 'rp-api' },
  { method: 'GET', path: '/units/{unitId}/lineage', target: 'rp-api' },
  { method: 'GET', path: '/region/{unitId}', target: 'rp-get-region-http' },
  { method: 'GET', path: '/artifacts', target: 'rp-api' },
  { method: 'GET', path: '/pipelines', target: 'rp-api' },
  { method: 'GET', path: '/pipelines/diff', target: 'rp-api' },
  { method: 'GET', path: '/pipelines/{pv}', target: 'rp-api' },
  { method: 'POST', path: '/ask', target: 'ask' },
  { method: 'GET', path: '/runs', target: 'rp-api' },
  { method: 'GET', path: '/runs/{runId}', target: 'rp-api' },
  { method: 'GET', path: '/review', target: 'rp-api' },
  // v2 inspection routes
  // {pv} rather than a literal 1.0: a static `1.0` sibling of `{pv}` would capture /lanes/1.0/units and the rest
  // (API Gateway prefers static segments). rp-xray answers 400 for any lane but 1.0.
  { method: 'GET', path: '/documents/{docId}/lanes/{pv}/xray', target: 'rp-xray' },
  { method: 'GET', path: '/documents/{docId}/lanes/{pv}/build', target: 'rp-api' },
  { method: 'GET', path: '/inspect/filters', target: 'rp-api' },
  { method: 'GET', path: '/runs/{runId}/calls/{callId}/path', target: 'rp-api' },
  { method: 'GET', path: '/showcases', target: 'rp-api' },
  { method: 'GET', path: '/showcases/{showcaseId}/payload', target: 'rp-api' },
  // governance, measurement and knowledge
  { method: 'GET', path: '/access/decisions', target: 'rp-api' },
  { method: 'GET', path: '/governance/definitions/{name}', target: 'rp-api' },
  { method: 'POST', path: '/review/{packageId}/items/{reviewKey}/decision', target: 'rp-api' },
  { method: 'GET', path: '/eval/runs', target: 'rp-api' },
  { method: 'GET', path: '/eval/runs/{evalRunId}', target: 'rp-api' },
  { method: 'GET', path: '/cost', target: 'rp-api' },
  { method: 'GET', path: '/ontology/classes', target: 'rp-api' },
  { method: 'GET', path: '/ontology/terms/{iri}', target: 'rp-api' },
  { method: 'GET', path: '/graph/neighbourhood', target: 'rp-api' },
  // COA environment lifecycle (rp-api forwards to rp-coa-control; without it GET says "not deployed")
  { method: 'GET', path: '/coa/environment', target: 'rp-api' },
  { method: 'POST', path: '/coa/environment/up', target: 'rp-api' },
  { method: 'POST', path: '/coa/environment/down', target: 'rp-api' },
  { method: 'PUT', path: '/coa/environment/settings', target: 'rp-api' },
  { method: 'POST', path: '/coa/environment/keep-alive', target: 'rp-api' },
];
