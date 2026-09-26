"""Private archive intake API."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Header, HTTPException, Request

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

    return router


def _record(record) -> dict:
    value = asdict(record)
    value["received_at"] = record.received_at.isoformat()
    return value
