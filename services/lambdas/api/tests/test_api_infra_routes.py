"""The API Gateway resource tree (infra/lib/api-routes.ts) and the Python router must agree."""

from __future__ import annotations

import re

from lambdas.api.routes import router
from lambdas.api.tests.support import REPO_ROOT

_ROUTE = re.compile(r"\{\s*method:\s*'(\w+)',\s*path:\s*'([^']+)',\s*target:\s*'([\w-]+)'\s*\}")


def test_rp_api_routes_match_infra() -> None:
    text = (REPO_ROOT / "infra" / "lib" / "api-routes.ts").read_text()
    infra = {(m, p) for m, p, target in _ROUTE.findall(text) if target == "rp-api"}
    assert infra, "no routes parsed from infra/lib/api-routes.ts"
    assert infra == set(router.routes)


def test_other_owners_routes_present_in_infra() -> None:
    text = (REPO_ROOT / "infra" / "lib" / "api-routes.ts").read_text()
    targets = {(m, p): t for m, p, t in _ROUTE.findall(text)}
    assert targets[("POST", "/packages/{packageId}/upload-url")] == "rp-upload-url"
    assert targets[("GET", "/region/{unitId}")] == "rp-get-region-http"
    assert targets[("POST", "/ask")] == "ask"
