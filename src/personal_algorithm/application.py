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
<meta name="viewport" content="width=device-width,initial-scale=1"><title>The Personal Algorithm</title>
<style>
:root{color-scheme:dark;font-family:Georgia,serif}*{box-sizing:border-box}body{margin:0;background:#f0eadc;color:#171717}
main{width:min(760px,calc(100% - 28px));margin:auto;padding:38px 0 90px}header{border-bottom:3px double #171717;padding-bottom:20px;margin-bottom:34px}
.kicker,.meta,.action{font-family:ui-monospace,monospace}.kicker{font-size:.72rem;letter-spacing:.13em;text-transform:uppercase}
h1{font-size:clamp(2.7rem,8vw,5.5rem);line-height:.88;letter-spacing:-.055em;margin:.2em 0}.lede{font-size:1.12rem;line-height:1.55;max-width:650px}
nav{display:flex;gap:18px;flex-wrap:wrap;margin-top:20px;font-family:ui-monospace,monospace;font-size:.82rem}a{color:inherit}
.top8{margin:34px 0 44px;border-top:1px solid #171717;border-bottom:1px solid #171717;padding:18px 0 22px}.top8-head{display:flex;justify-content:space-between;gap:16px;align-items:baseline;margin-bottom:14px}.top8 h2{font-size:1.45rem;margin:0}.top8-note{font: .68rem ui-monospace,monospace;color:#6b675f}.top8-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.pin{aspect-ratio:1;border:1px solid #171717;background:#fffdf6;padding:9px;display:flex;flex-direction:column;justify-content:space-between;text-decoration:none}.pin-thumb{flex:1;min-height:68px;background:repeating-linear-gradient(45deg,#d8d2c5,#d8d2c5 8px,#eee8dc 8px,#eee8dc 16px);margin-bottom:8px}.pin span{font:700 .64rem ui-monospace,monospace;line-height:1.25}.feed{display:grid;gap:30px}.unit{background:#fffdf6;border:1px solid #171717;box-shadow:5px 5px 0 #171717}
.content{padding:22px}.meta{font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:#62605a}.unit h2{font-size:1.65rem;margin:.35em 0}.unit p{line-height:1.55}
.preview{height:150px;border:1px solid #b9b4a8;background:repeating-linear-gradient(135deg,#ded8cb,#ded8cb 14px,#eae5da 14px,#eae5da 28px);display:grid;place-items:center;font:700 .72rem ui-monospace,monospace;letter-spacing:.12em;color:#6d685f}
.actions{border-top:1px solid #171717;display:grid;grid-template-columns:repeat(4,1fr);font-family:ui-monospace,monospace}
.action{border:0;border-right:1px solid #171717;background:#f8f3e8;padding:12px 8px;text-align:center;font-size:.74rem}.action:last-child{border-right:0}.publish{background:#171717;color:#f8f3e8}
.note{margin-top:36px;border-top:1px dotted #171717;padding-top:18px;font-size:.9rem;line-height:1.55}
@media(max-width:560px){.top8-grid{grid-template-columns:repeat(2,1fr)}.actions{grid-template-columns:1fr 1fr}.action:nth-child(2){border-right:0}.action:nth-child(-n+2){border-bottom:1px solid #171717}}
</style></head><body><main><header><div class="kicker">A small personal web · curated by an algorithm, governed by a person</div>
<h1>The Personal<br>Algorithm</h1>
<p class="lede">A self-hosted feed that learns what deserves your attention without owning it. The private algorithm gathers and ranks; this public page is what its owner deliberately chooses to publish.</p>
<nav><a href="/app/">open my private algorithm ↗</a><a href="#feed">published feed ↓</a></nav></header>
<section class="top8"><div class="top8-head"><h2>Top 8</h2><span class="top8-note">pinned by the owner · not ranked</span></div><div class="top8-grid">
<a class="pin" href="#feed"><div class="pin-thumb"></div><span>01 · a favorite thing</span></a>
<a class="pin" href="#feed"><div class="pin-thumb"></div><span>02 · keep this close</span></a>
<a class="pin" href="#feed"><div class="pin-thumb"></div><span>03 · worth your time</span></a>
<a class="pin" href="#feed"><div class="pin-thumb"></div><span>04 · perennial</span></a>
<a class="pin" href="#feed"><div class="pin-thumb"></div><span>05 · rabbit hole</span></a>
<a class="pin" href="#feed"><div class="pin-thumb"></div><span>06 · changed my mind</span></a>
<a class="pin" href="#feed"><div class="pin-thumb"></div><span>07 · show a friend</span></a>
<a class="pin" href="#feed"><div class="pin-thumb"></div><span>08 · just because</span></a>
</div></section>
<section class="feed" id="feed">
<article class="unit"><div class="content"><div class="meta">video · example published item</div><div class="preview">MEDIA PREVIEW</div><h2>A thing worth passing along</h2><p>Each unit can be a video, link, paper, image, note, or other candidate surfaced by the private algorithm. Publication is an explicit human choice.</p></div>
<div class="actions"><button class="action">🙂 react</button><button class="action">↗ share</button><button class="action">☆ save</button><button class="action publish">↑ publish</button></div></article>
<article class="unit"><div class="content"><div class="meta">link · example published item</div><h2>The feed is a garden, not a firehose.</h2><p>Web 1.0 ownership with useful Web 2.0 affordances: durable pages and links, plus lightweight reactions, sharing, bookmarks, and deliberate promotion from private discovery to public curation.</p></div>
<div class="actions"><button class="action">✨ react</button><button class="action">↗ share</button><button class="action">★ saved</button><button class="action publish">↑ publish</button></div></article>
</section>
<p class="note"><strong>Public by choice.</strong> React, share, and save are ordinary reader actions. Publish is different: it is an owner action that promotes a selected item from the private Personal Algorithm into this public feed.</p>
</main></body></html>"""

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def app_from_env() -> FastAPI:
    return create_private_app(ApplicationSettings.from_env())
