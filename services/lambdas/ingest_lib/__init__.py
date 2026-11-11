"""Shared helpers for the ingestion plane.

Only ingestion Lambdas and ``local_pipeline`` import this package. Modules:

- ``aws``        lazily created AWS clients behind one overridable ``Services`` object
- ``store``      S3 reads/writes that return ``ArtifactRef`` (size, sha256, versionId)
- ``pdf``        pypdf / pypdfium2 helpers (page info, text layer, page subsets, rendering)
- ``layout``     the canonical layout model every engine is normalised into (PDF points, top-left origin, 1-based pages)
- ``docling_layout`` / ``textract_layout``  engine JSON -> layout blocks
- ``vocab``      SKOS vocab snapshot: engine label -> role, printed label -> field concept
- ``embedder``   Titan Text Embeddings V2 and a deterministic local embedder
- ``units_io``   unit JSONL, UnitStatus and ReviewQueue writes
- ``pii``        the hold rule (docs/decisions/0003-pii-gate-rule.md) over Amazon Comprehend DetectPiiEntities results
- ``render``     page PNGs with held regions filled black
- ``aoss``       OpenSearch Serverless client (SigV4, service ``aoss``)
- ``rdf``        N-Quads for the ABox graph
- ``chunker``    recursive splitter for the v1.0 fixed chunks
- ``classifier`` SKOS indicator scoring + compact-model tie-break
- ``docling_client`` HTTP client for docling-serve
- ``rollup``     package rollup from DocStatus records
- ``http``       API Gateway proxy helpers for ``upload_url``
"""
