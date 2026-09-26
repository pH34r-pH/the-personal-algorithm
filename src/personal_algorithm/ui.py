"""Server-rendered private UI for data onboarding and connections."""

# ruff: noqa: I001  # Import ordering matches main; keep this exemption local to the rewritten UI.

from __future__ import annotations

from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from .connections import ConnectionStore
from .provider_registry import ProviderRegistry
from .providers import ProviderCapability


EXPORT_SOURCES = (
    (
        "google",
        "Google + YouTube",
        (
            "Largest single export. Keep all products selected if maximum coverage matters; "
            "multi-part Takeout archives are fine."
        ),
        "https://takeout.google.com/",
    ),
    (
        "spotify",
        "Spotify",
        "Request Extended Streaming History for lifetime listening and podcast activity.",
        "https://www.spotify.com/account/privacy/",
    ),
    (
        "x",
        "X",
        "Request the machine-readable archive with posts, follows, messages and account data.",
        "https://x.com/settings/download_your_data",
    ),
    (
        "linkedin",
        "LinkedIn",
        "Request the larger archive, including search, saved items, reactions, messages and jobs.",
        "https://www.linkedin.com/mypreferences/d/download-my-data",
    ),
    (
        "meta",
        "Meta Accounts Center",
        "Use one Accounts Center export for linked Facebook / Instagram data where available.",
        "https://accountscenter.facebook.com/info_and_permissions/dyi/",
    ),
    (
        "reddit",
        "Reddit",
        "Request your account data once, then upload the resulting archive here.",
        "https://www.reddit.com/settings/data-request",
    ),
)


def create_ui_router(
    *,
    registry: ProviderRegistry,
    connections: ConnectionStore,
    archives=None,
    personal_event_count=None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def home() -> str:
        archive_count = len(archives.list()) if archives else 0
        event_count = personal_event_count() if personal_event_count else 0
        body = f"""
        <main><p class="eyebrow">PRIVATE INSTANCE</p>
        <h1>Bring your history<br>under your control.</h1>
        <p class="lede">The priority right now is simple: get as much useful account and
        activity history as possible with as few login and setup steps as possible.</p>
        <div class="stats"><div><strong>{archive_count}</strong><span>archives retained</span></div>
        <div><strong>{event_count}</strong><span>normalized events</span></div></div>
        <div class="hero-actions"><a class="button primary" href="/app/onboarding">Start / continue onboarding</a>
        <a class="button" href="/app/connections">Connections</a></div></main>"""
        return _page("The Personal Algorithm", body)

    @router.get("/onboarding", response_class=HTMLResponse)
    def onboarding() -> str:
        connected = {item.provider_id: item for item in connections.list()}
        github = connected.get("github")
        github_action = (
            f'<span class="status">Connected as {escape(github.account_label)}</span>'
            if github
            else '<a class="button" href="/connections/github/connect">Connect GitHub</a>'
        )

        export_cards = "".join(
            f"""<article class="source-card"><div><p class="eyebrow">{escape(provider.upper())}</p>
            <h2>{escape(name)}</h2><p>{escape(note)}</p></div>
            <a class="button" target="_blank" rel="noopener" href="{escape(url)}">Request / open export ↗</a></article>"""
            for provider, name, note, url in EXPORT_SOURCES
        )
        archive_rows = _archive_rows(archives)
        body = f"""<main><a class="back" href="/app/">← Private home</a>
        <p class="eyebrow">DATA ONBOARDING</p><h1>One login.<br>One export.<br>Keep the data.</h1>
        <p class="lede">Start with the actions that return the most history per step.
        Raw archives are retained outside the live SQLite database so we can improve parsers
        later without asking you to download the same history again.</p>

        <h2 class="section-title">Best first moves</h2>
        <section>
          <article class="source-card featured"><div><p class="eyebrow">GOOGLE</p>
          <h2>Google Takeout</h2><p>Highest-yield first export: YouTube, My Activity,
          Search, Chrome and any other Google products you want to preserve in one request.</p></div>
          <a class="button primary" target="_blank" rel="noopener" href="https://takeout.google.com/">Open Takeout ↗</a></article>
          <article class="source-card featured"><div><p class="eyebrow">SPOTIFY</p>
          <h2>Lifetime listening</h2><p>Request Extended Streaming History once. It gives
          us the historical event stream rather than only a current taste profile.</p></div>
          <a class="button primary" target="_blank" rel="noopener" href="https://www.spotify.com/account/privacy/">Open Spotify privacy ↗</a></article>
          <article class="source-card featured"><div><p class="eyebrow">GITHUB</p>
          <h2>Connect once</h2><p>The existing provider connection can import accessible
          repository history and later support ongoing discovery.</p></div>{github_action}</article>
        </section>

        <h2 class="section-title">Upload anything that is ready</h2>
        <div class="upload-card">
          <label>Provider hint
            <select id="provider">
              <option value="">Auto-detect</option>
              <option value="google">Google / YouTube</option>
              <option value="spotify">Spotify</option>
              <option value="x">X</option>
              <option value="linkedin">LinkedIn</option>
              <option value="meta">Meta</option>
              <option value="reddit">Reddit</option>
              <option value="browser">Browser / local</option>
            </select>
          </label>
          <label>Archive or export file
            <input id="archives" type="file" multiple>
          </label>
          <button id="upload" class="button primary">Store selected files</button>
          <p id="upload-status" class="status" aria-live="polite"></p>
        </div>

        <h2 class="section-title">More high-yield exports</h2>
        <section>{export_cards}</section>

        <h2 class="section-title">Already retained</h2>
        <div class="archive-list">{archive_rows}</div>
        <p id="progress" class="status" aria-live="polite"></p>
        </main>
        {_onboarding_script()}"""
        return _page("Onboarding · The Personal Algorithm", body)

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
                    action += (
                        f'<button class="button" data-bootstrap="{escape(descriptor.id)}">'
                        "Import history</button>"
                    )
            elif descriptor.id == "github":
                action = '<a class="button" href="/connections/github/connect">Connect GitHub</a>'
            else:
                action = '<span class="muted">Not configured yet</span>'
            cards.append(
                f'<article><div><p class="eyebrow">{escape(descriptor.id.upper())}</p>'
                f'<h2>{escape(descriptor.display_name)}</h2><p>{escape(capabilities)}</p></div>'
                f'<div class="actions">{action}</div></article>'
            )

        body = """<main><a class="back" href="/app/onboarding">← Onboarding</a>
        <p class="eyebrow">CONNECTED ACCOUNTS</p><h1>Connections</h1>
        <p class="lede">Connections are for current account access and future sync.
        Historical archives stay separate so one OAuth grant does not pretend to expose data
        that a provider only includes in its export.</p><section>""" + "".join(cards) + """</section>
        <div id="progress" aria-live="polite"></div></main>""" + _bootstrap_script()
        return _page("Connections · The Personal Algorithm", body)

    return router


def _archive_rows(archives) -> str:
    if archives is None:
        return '<p class="muted">Archive storage is not configured.</p>'
    records = archives.list()
    if not records:
        return '<p class="muted">No archives uploaded yet.</p>'
    rows = []
    for record in records:
        provider = record.detected_provider or record.provider_hint or "unknown"
        rows.append(
            f'<div class="archive-row"><div><strong>{escape(record.filename)}</strong>'
            f'<span>{escape(provider)} · {_format_bytes(record.size_bytes)} · '
            f'{record.manifest.get("members", 1)} members</span></div>'
            f'<code>{record.sha256[:12]}</code></div>'
        )
    return "".join(rows)


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


def _onboarding_script() -> str:
    return """<script>
async function runBootstrap(provider) {
  const progress = document.querySelector("#progress");
  progress.textContent = "Importing " + provider + "…";
  const created = await fetch("/connections/" + provider + "/bootstrap", {method:"POST"});
  if (!created.ok) { progress.textContent = "Could not start " + provider + " import."; return; }
  let job = await created.json();
  while (job.status === "pending" || job.status === "running") {
    progress.textContent = "Importing " + provider + " · " + job.checkpoint.imported + " imported";
    const step = await fetch("/connections/" + provider + "/bootstrap/" + job.id + "/step", {method:"POST"});
    if (!step.ok) { progress.textContent = "Import stopped; its checkpoint is safe."; return; }
    job = await step.json();
  }
  progress.textContent = provider + " import complete · " + job.checkpoint.imported + " imported";
}
document.querySelector("#upload")?.addEventListener("click", async () => {
  const files = [...document.querySelector("#archives").files];
  const provider = document.querySelector("#provider").value;
  const status = document.querySelector("#upload-status");
  if (!files.length) { status.textContent = "Choose at least one file."; return; }
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    status.textContent = "Storing " + file.name + " (" + (i + 1) + "/" + files.length + ")…";
    const headers = {"x-archive-filename": file.name};
    if (provider) headers["x-provider-hint"] = provider;
    const response = await fetch("/archives/upload", {method:"POST", headers, body:file});
    if (!response.ok) {
      let detail = "upload failed";
      try { detail = (await response.json()).detail || detail; } catch {}
      status.textContent = file.name + ": " + detail;
      return;
    }
  }
  status.textContent = "Stored " + files.length + " file" + (files.length === 1 ? "" : "s") + ". Reloading…";
  location.reload();
});
const auto = new URLSearchParams(location.search).get("bootstrap");
if (auto) runBootstrap(auto);
</script>"""


def _bootstrap_script() -> str:
    return """<script>
document.querySelectorAll("[data-bootstrap]").forEach(button => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    const provider = button.dataset.bootstrap;
    const progress = document.querySelector("#progress");
    const created = await fetch("/connections/" + provider + "/bootstrap", {method:"POST"});
    if (!created.ok) { progress.textContent = "Could not start import."; button.disabled=false; return; }
    let job = await created.json();
    while (job.status === "pending" || job.status === "running") {
      progress.textContent = "Importing " + provider + " · " + job.checkpoint.imported + " imported";
      const step = await fetch("/connections/" + provider + "/bootstrap/" + job.id + "/step", {method:"POST"});
      if (!step.ok) { progress.textContent = "Import stopped. You can safely resume later."; button.disabled=false; return; }
      job = await step.json();
    }
    progress.textContent = "Import complete · " + job.checkpoint.imported + " imported";
    button.disabled = false;
  });
});
</script>"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)}</title>
<style>
:root {{ color-scheme:dark; font-family:ui-sans-serif,system-ui,sans-serif }}
* {{ box-sizing:border-box }} body {{ margin:0;background:#0b0c0f;color:#f4f1e8 }}
main {{ width:min(960px,calc(100% - 36px));margin:0 auto;padding:64px 0 90px }}
h1 {{ font-size:clamp(3rem,9vw,6.5rem);line-height:.88;letter-spacing:-.065em;margin:.2em 0 .35em }}
h2 {{ margin:.15rem 0 .5rem }} p {{ line-height:1.55 }}
.lede {{ max-width:720px;font-size:1.15rem;color:#b8b5ad;margin-bottom:36px }}
.eyebrow {{ font:600 .72rem ui-monospace,monospace;letter-spacing:.13em;color:#a8ff78 }}
.section-title {{ margin:42px 0 14px;font-size:1.25rem }}
section {{ display:grid;gap:12px }}
article,.upload-card,.archive-row {{ border:1px solid #292c33;border-radius:16px;padding:22px;background:#111318 }}
article {{ display:flex;justify-content:space-between;gap:24px;align-items:center }}
article p {{ color:#9fa3ad;margin:.25rem 0;max-width:620px }}
.featured {{ border-color:#425538 }}
.actions,.hero-actions {{ display:flex;flex-wrap:wrap;gap:10px;align-items:center }}
.button,button {{ display:inline-block;border:1px solid #a8ff78;background:transparent;color:#eaffdf;
padding:10px 14px;border-radius:999px;text-decoration:none;font:inherit;cursor:pointer }}
.primary {{ background:#a8ff78;color:#10140d }} button:disabled {{ opacity:.45 }}
.status {{ color:#a8ff78 }} .muted,.back {{ color:#888e99 }} .back {{ text-decoration:none }}
.stats {{ display:flex;gap:12px;flex-wrap:wrap;margin:0 0 24px }}
.stats div {{ border:1px solid #292c33;border-radius:14px;padding:14px 18px;min-width:160px }}
.stats strong {{ display:block;font-size:1.5rem }} .stats span {{ color:#888e99;font-size:.82rem }}
.upload-card {{ display:grid;gap:14px }} label {{ display:grid;gap:7px;color:#b8b5ad }}
select,input[type=file] {{ width:100%;padding:10px;border:1px solid #353942;border-radius:10px;background:#0b0c0f;color:#f4f1e8 }}
.archive-list {{ display:grid;gap:8px }} .archive-row {{ display:flex;justify-content:space-between;gap:20px;align-items:center;padding:14px 16px }}
.archive-row strong,.archive-row span {{ display:block }} .archive-row span {{ color:#888e99;font-size:.82rem;margin-top:4px }}
code {{ color:#a8ff78 }} #progress {{ margin-top:20px }}
@media(max-width:650px) {{
  article,.archive-row {{ align-items:flex-start;flex-direction:column }}
  .actions {{ align-items:flex-start }} .button {{ width:100%;text-align:center }}
}}
</style></head><body>{body}</body></html>"""
