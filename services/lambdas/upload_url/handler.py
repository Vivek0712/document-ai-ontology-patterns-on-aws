"""rp-upload-url: ``POST /packages/{packageId}/upload-url`` (owner only).

Returns a 5-minute SigV4 presigned PUT for ``bronze/{sub}/{packageId}/{fileName}`` with the declared
``Content-Length`` and ``Content-Type`` signed in, so the object is exactly ``sizeBytes`` (at most
``MAX_UPLOAD_BYTES``) or S3 rejects the PUT. It also writes the
DocumentItem (AWAITING_UPLOAD) and the initial DocAcl, and increments ``docCount`` for a new doc.
Re-requesting the same file name reuses the docId (re-upload re-ingests the same document)."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

from evidence_core import s3keys
from evidence_core.ddb import from_ddb, to_ddb
from evidence_core.ids import doc_id_for
from evidence_core.models.api import (
    UPLOAD_URL_TTL_SECONDS,
    DocAclItem,
    DocumentItem,
    Package,
    UploadUrlRequest,
    UploadUrlResponse,
)
from evidence_core.models.status import iso
from pydantic import ValidationError

from lambdas.ingest_lib.aws import services
from lambdas.ingest_lib.http import HttpError, caller_from_event, error, json_body, respond


def issue(package_id: str, sub: str, req: UploadUrlRequest) -> UploadUrlResponse:
    svc = services()
    item = svc.table("packages").get_item(Key={"packageId": package_id}).get("Item")
    if not item:
        raise HttpError(404, "not_found", "Package not found.")
    pkg = Package.load(from_ddb(item))
    if pkg.owner_sub != sub:
        raise HttpError(403, "forbidden", "Only the package owner can upload documents.")
    doc_id = doc_id_for(package_id, req.file_name)
    key = s3keys.bronze_key(sub, package_id, req.file_name)
    url = svc.s3_presign.generate_presigned_url(
        "put_object", Params={"Bucket": svc.settings.data_bucket, "Key": key, "ContentType": req.content_type,
                              "ContentLength": req.size_bytes},
        ExpiresIn=UPLOAD_URL_TTL_SECONDS, HttpMethod="PUT")
    doc = DocumentItem(package_id=package_id, doc_id=doc_id, file_name=req.file_name, bronze_key=key,
                       content_type=req.content_type, size_bytes=req.size_bytes, uploaded_by=sub,
                       upload_state="AWAITING_UPLOAD", upload_url_issued_at=iso())
    docs = svc.table("documents")
    existed = "Item" in docs.get_item(Key={"packageId": package_id, "docId": doc_id})
    docs.put_item(Item=to_ddb(doc.dump()))
    svc.table("doc_acl").put_item(Item=to_ddb(DocAclItem(doc_id=doc_id, package_id=package_id, owner_sub=sub,
                                                         allowed_principals=pkg.allowed_principals).dump()))
    if not existed:
        svc.table("packages").update_item(Key={"packageId": package_id}, UpdateExpression="ADD docCount :one",
                                          ExpressionAttributeValues={":one": 1})
    return UploadUrlResponse(doc_id=doc_id, key=key, url=url, headers={"Content-Type": req.content_type})


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    request_id = getattr(context, "aws_request_id", None)
    try:
        caller = caller_from_event(event)
        package_id = unquote(((event.get("pathParameters") or {}).get("packageId")) or "")
        if not package_id:
            raise HttpError(400, "bad_request", "packageId is required.")
        try:
            req = UploadUrlRequest.load(json_body(event))
        except ValidationError as e:
            raise HttpError(400, "bad_request", f"Invalid request: {e.errors()[0].get('msg', 'invalid')}") from e
        return respond(200, issue(package_id, caller.sub, req), event)
    except HttpError as e:
        return error(e, request_id, event)
