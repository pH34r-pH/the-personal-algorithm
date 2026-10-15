"""Private archive intake API."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Header, HTTPException, Request

from .archive_importers import ARCHIVE_IMPORTERS, IMPORT_COVERAGE
from .archives import ArchiveInbox


def create_archive_router(inbox: ArchiveInbox) -> APIRouter:
    router = APIRouter(prefix="/archives", tags=["archives"])

    @router.get("")
    def list_archives() -> dict:
        return {"archives": [_record(record) for record in inbox.list()]}

    @router.post("/upload", status_code=201)
    async def upload_archive(
        request: Request,
        filename: str = Header(alias="x-archive-filename"),
        provider_hint: str | None = Header(default=None, alias="x-provider-hint"),
    ) -> dict:
        try:
            record = await inbox.store_stream(
                filename,
                request.stream(),
                provider_hint=provider_hint,
            )
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        return _record(record)

    @router.post("/{sha256}/import")
    def import_archive(sha256: str) -> dict:
        record = inbox.get(sha256)
        if record is None:
            raise HTTPException(status_code=404, detail="archive not found")
        provider = record.detected_provider or record.provider_hint
        importer = ARCHIVE_IMPORTERS.get(provider)
        if importer is None:
            raise HTTPException(
                status_code=409,
                detail="semantic importer is not available for this archive yet",
            )
        try:
            summary = importer(inbox, inbox.store, record)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "provider": provider,
            "status": "imported",
            "coverage": IMPORT_COVERAGE[provider],
            "summary": asdict(summary),
        }

    return router


def _record(record) -> dict:
    value = asdict(record)
    value["received_at"] = record.received_at.isoformat()
    provider = record.detected_provider or record.provider_hint
    value["import_available"] = provider in ARCHIVE_IMPORTERS
    value["import_coverage"] = IMPORT_COVERAGE.get(provider, "Retained only; parser not implemented")
    return value
