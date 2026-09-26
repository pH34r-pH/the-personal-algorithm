"""Single-owner authorization behind a trusted hosting authentication layer."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from fastapi import Header, HTTPException, Request


@dataclass(frozen=True, slots=True)
class InstanceAuth:
    owner_subject: str
    identity_header: str = "x-ms-client-principal-name"

    def __post_init__(self) -> None:
        if not self.owner_subject.strip():
            raise ValueError("owner_subject must not be empty")

    def dependency(self, *, redirect_unauthenticated: bool = False):
        expected = self.owner_subject.strip().casefold()

        def require_owner(
            request: Request,
            subject: str | None = Header(default=None, alias=self.identity_header),
        ) -> str:
            if subject is None:
                if redirect_unauthenticated and request.method in {"GET", "HEAD"}:
                    target = request.url.path
                    if request.url.query:
                        target = f"{target}?{request.url.query}"
                    login = f"/.auth/login/aad?post_login_redirect_uri={quote(target, safe='')}"
                    raise HTTPException(status_code=307, headers={"Location": login})
                raise HTTPException(status_code=401, detail="authentication required")
            if subject.strip().casefold() != expected:
                raise HTTPException(status_code=403, detail="instance owner required")
            return subject

        return require_owner
