"""Governed meaning and human review (SC-8 fallback card, W-14).

- ``GET /governance/definitions/{name}``: the Git-governed definition record the ontology build wrote
  (``config/ontology/{ver}/governance/{name}.json``, ``ontology/rpont/governance.py``) for the ontology version the
  evidence-units lane binds. The record is the same for every caller (it is configuration, not package data), so
  any signed-in caller may read it.
- ``POST /review/{packageId}/items/{reviewKey}/decision``: ACCEPT, CORRECT or REJECT one low-confidence field, by a
  caller with access to the package and to the field's document. The decision records the reviewer (sub, 6
  characters) and the time; a CORRECT keeps the extracted ``value`` and records the reviewer's value beside it. The
  previous decision moves to ``history``. The request carries ``expectedDecisionAt`` (the decision the reviewer
  saw, or none): when the item's decision is no longer that one, the route answers 409 and writes nothing.
"""

from __future__ import annotations

import json
import re

from botocore.exceptions import ClientError
from evidence_core.ddb import from_ddb, to_ddb
from evidence_core.ids import short
from evidence_core.models.governance import GovernedDefinitionView, ReviewDecision, ReviewDecisionRequest
from evidence_core.models.status import iso
from evidence_core.models.units import ReviewItem

from ..acl import require_doc, require_package
from ..deps import get_deps
from ..http import Reply, Request, bad_request, conflict, not_found
from . import GOVERNED_DEFINITION, REVIEW_DECISION, router

EVIDENCE_LANE = "2.3.1"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
STATE_OF = {"ACCEPT": "ACCEPTED", "CORRECT": "CORRECTED", "REJECT": "REJECTED"}


def definition_key(ontology_version: str, name: str) -> str:
    return f"config/ontology/{ontology_version}/governance/{name}.json"


@router.route("GET", GOVERNED_DEFINITION)
def governed_definition(req: Request) -> Reply:
    name = req.param("name")
    if not NAME_RE.match(name):
        raise bad_request(f"invalid definition name {name!r}")
    deps = get_deps()
    version = deps.pipeline(EVIDENCE_LANE).config.ontology_version
    if not version:
        raise not_found(f"Lane {EVIDENCE_LANE} binds no ontology.")
    key = definition_key(version, name)
    try:
        text, _ = deps.source().read_text(key)
    except (FileNotFoundError, ClientError) as exc:
        raise not_found(f"No governed definition {name!r} in ontology {version} ({key}); run `make ontology-check` "
                        "and `make seed`.") from exc
    return Reply(GovernedDefinitionView.load(json.loads(text)))


@router.route("POST", REVIEW_DECISION)
def review_decision(req: Request) -> Reply:
    deps = get_deps()
    pkg = require_package(deps, req.caller, req.param("packageId"))
    review_key = req.param("reviewKey")
    body: ReviewDecisionRequest = req.parse(ReviewDecisionRequest)
    if body.action == "CORRECT" and not (body.value or "").strip():
        raise bad_request("CORRECT needs a value")
    if body.action != "CORRECT" and body.value is not None:
        raise bad_request("only CORRECT takes a value")
    table = deps.table("review_queue")
    row = table.get_item(Key={"packageId": pkg.package_id, "reviewKey": review_key}).get("Item")
    if not row:
        raise not_found(f"No review item {review_key} in {pkg.package_id}.")
    item = ReviewItem.load(from_ddb(row))
    require_doc(deps, req.caller, item.doc_id)
    previous = item.decision
    if (previous.at if previous else None) != body.expected_decision_at:
        raise conflict("Someone decided this item after you opened it; reload it.")
    decision = ReviewDecision(action=body.action, by=short(req.caller.sub), at=iso(),
                              value=body.value.strip() if body.value else None, note=body.note)
    history = [*item.history, previous] if previous else list(item.history)
    updated = item.model_copy(update={"state": STATE_OF[body.action], "decision": decision, "history": history})
    # the reviewer's view, checked again at write time (another decision between the read above and this put)
    expected = body.expected_decision_at
    try:
        table.put_item(Item=to_ddb(updated.dump()),
                       ConditionExpression="#d.#at = :at" if expected else "attribute_not_exists(#d)",
                       ExpressionAttributeNames={"#d": "decision", **({"#at": "at"} if expected else {})},
                       **({"ExpressionAttributeValues": {":at": expected}} if expected else {}))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise conflict("Someone decided this item after you opened it; reload it.") from exc
        raise
    return Reply(updated)
