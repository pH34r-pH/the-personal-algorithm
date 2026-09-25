"""Composition root for a configured single-owner Personal Algorithm instance."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .azure_credentials import AzureKeyVaultCredentialStore
from .bootstrap import BootstrapCoordinator
from .connections import ConnectionStore
from .connections_api import create_connections_router
from .github_auth import GitHubAuthorization
from .github_provider import GitHubProvider
from .instance_auth import InstanceAuth
from .oauth_state import DurableAuthorizationStateStore
from .private_api import protect
from .provider_registry import ProviderRegistry
from .store import Store
from .ui import create_ui_router


@dataclass(frozen=True, slots=True)
class ApplicationSettings:
    database: str
    owner_subject: str
    key_vault_url: str
    github_client_id: str
    github_client_secret: str

    @classmethod
    def from_env(cls) -> ApplicationSettings:
        required = {
            "database": os.getenv("TPA_DATABASE", "data/personal-algorithm.sqlite3"),
            "owner_subject": os.getenv("TPA_OWNER_SUBJECT", ""),
            "key_vault_url": os.getenv("TPA_KEY_VAULT_URL", ""),
            "github_client_id": os.getenv("TPA_GITHUB_CLIENT_ID", ""),
            "github_client_secret": os.getenv("TPA_GITHUB_CLIENT_SECRET", ""),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"missing application settings: {', '.join(missing)}")
        return cls(**required)


def create_private_app(
    settings: ApplicationSettings,
    *,
    credentials=None,
    github_http=None,
    oauth_http=None,
) -> FastAPI:
    database_path = Path(settings.database)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(database_path)
    connection_store = ConnectionStore(store)
    bootstrap = BootstrapCoordinator(store)

    credential_store = credentials or AzureKeyVaultCredentialStore(settings.key_vault_url)
    github = GitHubProvider(credential_store, client=github_http)
    registry = ProviderRegistry()
    registry.register(github)

    github_auth = GitHubAuthorization(
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
        credentials=credential_store,
        states=DurableAuthorizationStateStore(store),
        http=oauth_http,
        provider=github,
    )

    auth = InstanceAuth(owner_subject=settings.owner_subject)
    app = FastAPI(title="The Personal Algorithm", version="0.1.0")

    private_connections = protect(
        create_connections_router(
            registry=registry,
            connections=connection_store,
            bootstrap=bootstrap,
            github_auth=github_auth,
            github_provider=github,
        ),
        auth,
    )
    private_ui = protect(
        create_ui_router(registry=registry, connections=connection_store),
        auth,
    )

    app.include_router(private_connections)
    app.include_router(private_ui, prefix="/app")

    @app.get("/", response_class=HTMLResponse)
    def landing() -> str:
        return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ph34r.dev · Projects</title>
<style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:#090b10;color:#f5f3ec}main{width:min(1180px,calc(100% - 40px));margin:auto;padding:64px 0 96px}
header{display:flex;justify-content:space-between;align-items:end;margin-bottom:48px}.mark{font:600 .74rem ui-monospace,monospace;letter-spacing:.16em;color:#aab2c0}
h1{font-size:clamp(3rem,8vw,6.6rem);line-height:.9;letter-spacing:-.065em;margin:.15em 0}.intro{max-width:610px;color:#9ea6b5;line-height:1.6}
.gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px}.tile{position:relative;overflow:hidden;min-height:480px;border:1px solid #252a34;border-radius:28px;background:#10131a}
.demo{position:absolute;inset:0;padding:26px;display:grid;grid-template-columns:1.2fr .8fr;grid-template-rows:80px 1fr 110px;gap:12px;opacity:.75}
.box{border:1px solid #343b49;border-radius:18px;background:#171b24}.wide{grid-column:1/-1}.tall{grid-row:2}.stack{display:grid;gap:12px}.glass{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:flex-end;padding:34px;background:linear-gradient(180deg,rgba(13,16,23,.28),rgba(13,16,23,.78));backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);border:1px solid rgba(255,255,255,.07)}
.lock{width:44px;height:44px;border:1px solid rgba(255,255,255,.25);border-radius:50%;display:grid;place-items:center;margin-bottom:22px;font-size:20px}
.eyebrow{font:600 .68rem ui-monospace,monospace;letter-spacing:.15em;color:#a8ff78}.tile h2{font-size:clamp(2.3rem,5vw,4rem);line-height:.94;letter-spacing:-.055em;margin:.2em 0}.tile p{max-width:520px;color:#c0c5ce;line-height:1.55}
.enter{display:inline-block;margin-top:16px;color:#f4ffe9;text-decoration:none;border-bottom:1px solid #a8ff78;width:max-content;padding-bottom:4px}
@media(max-width:600px){main{width:min(100% - 24px,1180px);padding-top:36px}.tile{min-height:440px}}
</style></head><body><main><header><div><div class="mark">PH34R.DEV / SIDE PROJECTS</div><h1>Things under construction.</h1></div></header>
<p class="intro">Small systems for exploring ownership, agency, computation, and the odd corners where software meets the physical world.</p>
<section class="gallery"><article class="tile"><div class="demo"><div class="box wide"></div><div class="box tall"></div><div class="stack"><div class="box"></div><div class="box"></div><div class="box"></div></div><div class="box wide"></div></div>
<div class="glass"><div class="lock">⌁</div><div class="eyebrow">PRIVATE INSTANCE · PROJECT 01</div><h2>The Personal<br>Algorithm</h2>
<p>Your feeds. Your history. Your objectives. A self-hosted experiment in taking ownership of recommendation and discovery.</p>
<a class="enter" href="/app/">Enter my personal algorithm →</a></div></article></section></main></body></html>"""

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def app_from_env() -> FastAPI:
    return create_private_app(ApplicationSettings.from_env())
