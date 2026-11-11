"""S3 key layout for the data bucket (env RP_DATA_BUCKET). Single source of truth.

    bronze/{ownerSub}/{packageId}/{fileName}                      uploaded original (presigned PUT)
    quarantine/{packageId}/{docId}/{fileName}                     upload rejected by `register`
                                                                  (not a PDF, encrypted, too large).
                                                                  Held PII units are NOT moved here:
                                                                  they stay in Silver.
    silver/{packageId}/{docId}/{pv}/register/manifest.json
    silver/{packageId}/{docId}/{pv}/route/route.json
    silver/{packageId}/{docId}/{pv}/extract/text_layer.json       v1.0
    silver/{packageId}/{docId}/{pv}/extract/docling.json          v2.3.1 digital pages
    silver/{packageId}/{docId}/{pv}/extract/docling_task.json     in-flight docling-serve task (resume marker)
    silver/{packageId}/{docId}/{pv}/extract/textract/p{NNN}.json  v2.3.1 scanned pages
    silver/{packageId}/{docId}/{pv}/classify/classification.json
    silver/{packageId}/{docId}/{pv}/units/units.jsonl             all units incl. held
    silver/{packageId}/{docId}/{pv}/units/build_report.json
    silver/{packageId}/{docId}/{pv}/pii_gate/decisions.json
    gold/{packageId}/{docId}/{pv}/units.jsonl                     passed units only
    gold/{packageId}/{docId}/{pv}/units/{unitLocal}.json          one passed unit record
    gold/{packageId}/{docId}/{pv}/pages/p{NNN}.png                page render, held bboxes filled black
    gold/{packageId}/{docId}/{pv}/pages/held_regions.json         held bboxes per page (get_region)
    gold/{packageId}/{docId}/{pv}/index/receipt.json
    gold/{packageId}/{docId}/{pv}/graph/abox.nq
    gold/{packageId}/{docId}/{pv}/graph/receipt.json
    gold/{packageId}/{docId}/{pv}/ready/receipt.json
    config/pipelines/v{pv}.yaml                                   bucket versioning gives versionId
    config/models.yaml
    config/prompts/{name}.txt
    config/ontology/{ontologyVersion}/vocab.json                  vocab snapshot (eu_builder, classify)
    config/ontology/{ontologyVersion}/nquads/{graph}.nq           TBox load files for neptune_load
    config/ontology/{ontologyVersion}/templates/{templateId}.rq   SPARQL templates (graph_hop)
    runs/{runId}.json                                             full run record
    eval/results/{evalRunId}.json

Page numbers are 1-based and zero-padded to 3 digits in keys.
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import StageId
from .ids import parse_unit_id

BRONZE = "bronze"
SILVER = "silver"
GOLD = "gold"
QUARANTINE = "quarantine"
CONFIG = "config"
RUNS = "runs"
EVAL = "eval"

TIERS = (BRONZE, SILVER, GOLD, QUARANTINE, CONFIG, RUNS, EVAL)


def bronze_prefix(owner_sub: str, package_id: str) -> str:
    """Presigned PUTs are restricted to this prefix."""
    return f"{BRONZE}/{owner_sub}/{package_id}/"


def bronze_key(owner_sub: str, package_id: str, file_name: str) -> str:
    if "/" in file_name or file_name in ("", ".", ".."):
        raise ValueError(f"invalid file name {file_name!r}")
    return f"{bronze_prefix(owner_sub, package_id)}{file_name}"


@dataclass(frozen=True)
class BronzeKey:
    owner_sub: str
    package_id: str
    file_name: str


def parse_bronze_key(key: str) -> BronzeKey:
    parts = key.split("/")
    if len(parts) != 4 or parts[0] != BRONZE or not all(parts[1:]):
        raise ValueError(f"not a bronze object key: {key!r}")
    return BronzeKey(owner_sub=parts[1], package_id=parts[2], file_name=parts[3])


def quarantine_key(package_id: str, doc_id: str, file_name: str) -> str:
    return f"{QUARANTINE}/{package_id}/{doc_id}/{file_name}"


# --- silver --------------------------------------------------------------------

def silver_lane_prefix(package_id: str, doc_id: str, pv: str) -> str:
    return f"{SILVER}/{package_id}/{doc_id}/{pv}/"


def silver_stage_prefix(package_id: str, doc_id: str, pv: str, stage: StageId | str) -> str:
    return f"{silver_lane_prefix(package_id, doc_id, pv)}{StageId(stage).value}/"


def manifest_key(package_id: str, doc_id: str, pv: str) -> str:
    return silver_stage_prefix(package_id, doc_id, pv, StageId.REGISTER) + "manifest.json"


def route_key(package_id: str, doc_id: str, pv: str) -> str:
    return silver_stage_prefix(package_id, doc_id, pv, StageId.ROUTE) + "route.json"


def text_layer_key(package_id: str, doc_id: str, pv: str) -> str:
    return silver_stage_prefix(package_id, doc_id, pv, StageId.EXTRACT) + "text_layer.json"


def docling_key(package_id: str, doc_id: str, pv: str) -> str:
    return silver_stage_prefix(package_id, doc_id, pv, StageId.EXTRACT) + "docling.json"


def docling_pages_key(package_id: str, doc_id: str, pv: str) -> str:
    """Sidecar to docling.json: page numbers inside docling.json -> source PDF pages (only some pages are sent)."""
    return silver_stage_prefix(package_id, doc_id, pv, StageId.EXTRACT) + "docling_pages.json"


def docling_task_key(package_id: str, doc_id: str, pv: str) -> str:
    """Resume marker of rp-docling-call: the docling-serve task id of an in-flight conversion, so an SQS
    retry after the invocation deadline polls the same task instead of submitting the PDF again."""
    return silver_stage_prefix(package_id, doc_id, pv, StageId.EXTRACT) + "docling_task.json"


def textract_page_key(package_id: str, doc_id: str, pv: str, page: int) -> str:
    return silver_stage_prefix(package_id, doc_id, pv, StageId.EXTRACT) + f"textract/p{page:03d}.json"


def classification_key(package_id: str, doc_id: str, pv: str) -> str:
    return silver_stage_prefix(package_id, doc_id, pv, StageId.CLASSIFY) + "classification.json"


def silver_units_key(package_id: str, doc_id: str, pv: str) -> str:
    return silver_stage_prefix(package_id, doc_id, pv, StageId.UNITS) + "units.jsonl"


def build_report_key(package_id: str, doc_id: str, pv: str) -> str:
    return silver_stage_prefix(package_id, doc_id, pv, StageId.UNITS) + "build_report.json"


def pii_decisions_key(package_id: str, doc_id: str, pv: str) -> str:
    return silver_stage_prefix(package_id, doc_id, pv, StageId.PII_GATE) + "decisions.json"


# --- gold --------------------------------------------------------------------

def gold_lane_prefix(package_id: str, doc_id: str, pv: str) -> str:
    return f"{GOLD}/{package_id}/{doc_id}/{pv}/"


def gold_units_key(package_id: str, doc_id: str, pv: str) -> str:
    return gold_lane_prefix(package_id, doc_id, pv) + "units.jsonl"


def gold_unit_key(package_id: str, unit_id: str) -> str:
    doc_id, pv, local = parse_unit_id(unit_id)
    return gold_lane_prefix(package_id, doc_id, pv) + f"units/{local}.json"


def page_image_key(package_id: str, doc_id: str, pv: str, page: int) -> str:
    return gold_lane_prefix(package_id, doc_id, pv) + f"pages/p{page:03d}.png"


def held_regions_key(package_id: str, doc_id: str, pv: str) -> str:
    """``{"pages": {"1": [bbox, ...]}}`` - regions filled black on the page images (promote)."""
    return gold_lane_prefix(package_id, doc_id, pv) + "pages/held_regions.json"


def index_receipt_key(package_id: str, doc_id: str, pv: str) -> str:
    return gold_lane_prefix(package_id, doc_id, pv) + "index/receipt.json"


def abox_key(package_id: str, doc_id: str, pv: str) -> str:
    return gold_lane_prefix(package_id, doc_id, pv) + "graph/abox.nq"


def graph_receipt_key(package_id: str, doc_id: str, pv: str) -> str:
    return gold_lane_prefix(package_id, doc_id, pv) + "graph/receipt.json"


def ready_receipt_key(package_id: str, doc_id: str, pv: str) -> str:
    return gold_lane_prefix(package_id, doc_id, pv) + "ready/receipt.json"


# --- config / runs / eval -------------------------------------------------------

def pipeline_config_key(pv: str) -> str:
    return f"{CONFIG}/pipelines/v{pv}.yaml"


def models_config_key() -> str:
    return f"{CONFIG}/models.yaml"


def prompt_key(name: str) -> str:
    """``answer_v1`` -> ``config/prompts/answer_v1.txt``."""
    return f"{CONFIG}/prompts/{name.removesuffix('.txt')}.txt"


def vocab_key(ontology_version: str) -> str:
    return f"{CONFIG}/ontology/{ontology_version}/vocab.json"


def tbox_nquads_key(ontology_version: str, graph: str) -> str:
    return f"{CONFIG}/ontology/{ontology_version}/nquads/{graph}.nq"


def template_key(ontology_version: str, template_id: str) -> str:
    return f"{CONFIG}/ontology/{ontology_version}/templates/{template_id}.rq"


def run_key(run_id: str) -> str:
    return f"{RUNS}/{run_id}.json"


def eval_result_key(eval_run_id: str) -> str:
    return f"{EVAL}/results/{eval_run_id}.json"


# --- access helpers -------------------------------------------------------------

def tier_of(key: str) -> str:
    tier = key.split("/", 1)[0]
    if tier not in TIERS:
        raise ValueError(f"unknown tier for key {key!r}")
    return tier


def package_of(key: str) -> str | None:
    """packageId a key belongs to, or None for config/runs/eval keys."""
    parts = key.split("/")
    tier = parts[0]
    if tier == BRONZE and len(parts) >= 3:
        return parts[2]
    if tier in (SILVER, GOLD, QUARANTINE) and len(parts) >= 2:
        return parts[1]
    return None
