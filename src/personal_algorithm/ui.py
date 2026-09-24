"""Minimal server-rendered private UI for account connections."""

from __future__ import annotations

from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from .connections import ConnectionStore
from .provider_registry import ProviderRegistry
from .providers import ProviderCapability


def create_ui_router(*, registry: ProviderRegistry, connections: ConnectionStore) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def home() -> str:
        return _page("The Personal Algorithm", """
        <main><p class="eyebrow">PRIVATE INSTANCE</p>
        <h1>Your feeds.<br>Your objectives.<br>Your algorithm.</h1>
        <p class="lede">Platforms can provide candidates. You decide what deserves attention.</p>
        <a class="button" href="/app/connections">Connections</a></main>""")

    @router.get("/connections", response_class=HTMLResponse)
    def connection_page() -> str:
        connected = {item.provider_id: item for item in connections.list()}
        cards = []
        for descriptor in registry.descriptors():
            connection = connected.get(descriptor.id)
            capabilities = " · ".join(
                cap.value.replace("_", " ")
                for cap in sorted(descriptor.capabilities, key=lambda cap: cap.value)
            )
            if connection:
                action = f'<span class="status">Connected as {escape(connection.account_label)}</span>'
                if ProviderCapability.HISTORY_IMPORT in descriptor.capabilities:
                    action += f'<button data-bootstrap="{escape(descriptor.id)}">Bootstrap history</button>'
            elif descriptor.id == "github":
                action = '<a class="button" href="/connections/github/connect">Connect GitHub</a>'
            else:
                action = '<span class="muted">Not configured yet</span>'
            cards.append(
                f'<article><div><p class="eyebrow">{escape(descriptor.id.upper())}</p>'
                f'<h2>{escape(descriptor.display_name)}</h2><p>{escape(capabilities)}</p></div>'
                f'<div class="actions">{action}</div></article>'
            )

        body = """<main><a class="back" href="/app/">← Algorithm</a>
        <p class="eyebrow">YOUR DATA, WITH YOUR PERMISSION</p><h1>Connections</h1>
        <p class="lede">Connect accounts explicitly. Historical bootstrap and continuous
        discovery remain separate choices.</p><section>""" + "".join(cards) + """</section>
        <div id="progress" aria-live="polite"></div></main>
<script>
document.querySelectorAll("[data-bootstrap]").forEach(button => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    const provider = button.dataset.bootstrap;
    const created = await fetch("/connections/" + provider + "/bootstrap", {method:"POST"});
    if (!created.ok) { document.querySelector("#progress").textContent = "Could not start bootstrap."; button.disabled=false; return; }
    let job = await created.json();
    const progress = document.querySelector("#progress");
    while (job.status === "pending" || job.status === "running") {
      progress.textContent = "Importing " + provider + " · " + job.checkpoint.imported + " imported";
      const step = await fetch("/connections/" + provider + "/bootstrap/" + job.id + "/step", {method:"POST"});
      if (!step.ok) { progress.textContent = "Bootstrap stopped. You can safely resume later."; button.disabled=false; return; }
      job = await step.json();
    }
    progress.textContent = "Bootstrap complete · " + job.checkpoint.imported + " imported";
    button.disabled = false;
  });
});
</script>"""
        return _page("Connections · The Personal Algorithm", body)

    return router


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)}</title>
<style>
:root {{ color-scheme:dark; font-family:ui-sans-serif,system-ui,sans-serif }}
* {{ box-sizing:border-box }} body {{ margin:0;background:#0b0c0f;color:#f4f1e8 }}
main {{ width:min(900px,calc(100% - 36px));margin:0 auto;padding:72px 0 }}
h1 {{ font-size:clamp(3rem,9vw,6.5rem);line-height:.88;letter-spacing:-.065em;margin:.2em 0 .35em }}
h2 {{ margin:.15rem 0 .5rem }} p {{ line-height:1.55 }}
.lede {{ max-width:620px;font-size:1.15rem;color:#b8b5ad;margin-bottom:42px }}
.eyebrow {{ font:600 .72rem ui-monospace,monospace;letter-spacing:.13em;color:#a8ff78 }}
section {{ display:grid;gap:12px }} article {{ border:1px solid #292c33;border-radius:16px;padding:22px;
display:flex;justify-content:space-between;gap:24px;align-items:center;background:#111318 }}
article p {{ color:#9fa3ad;margin:.25rem 0 }} .actions {{ display:flex;flex-direction:column;align-items:flex-end;gap:10px }}
.button,button {{ display:inline-block;border:1px solid #a8ff78;background:transparent;color:#eaffdf;
padding:10px 14px;border-radius:999px;text-decoration:none;font:inherit;cursor:pointer }}
button:disabled {{ opacity:.45 }} .status,#progress {{ color:#a8ff78 }} .muted,.back {{ color:#888e99 }}
.back {{ text-decoration:none }} #progress {{ margin-top:24px;font-family:ui-monospace,monospace }}
@media(max-width:650px) {{ article {{ align-items:flex-start;flex-direction:column }} .actions {{ align-items:flex-start }} }}
</style></head><body>{body}</body></html>"""
