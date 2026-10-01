# The Personal Algorithm — project context

## Purpose and authority

The Personal Algorithm is a local-first, single-owner recommendation application. The
source code and tests define current behavior; `docs/` explains contracts and deployment
boundaries; historical milestone and roadmap documents remain evidence of prior intent,
not proof that a feature is deployed.

## Architecture and data flow

```text
config/env + source definitions
          -> RSS/arXiv/provider acquisition
          -> Candidate records in SQLite
          -> deterministic ReferenceRanker + Policy
          -> rankings/explanations in SQLite
          -> local API and authenticated private UI

provider OAuth -> Key Vault secret -> opaque credential_ref in SQLite
raw export upload -> content-addressed archive_dir -> provider importer -> PersonalEvent rows
Azure Easy Auth -> X-MS-CLIENT-PRINCIPAL-ID -> owner-object-id authorization
```

| Boundary | Change here | Ownership and invariants |
| --- | --- | --- |
| `src/personal_algorithm/` | Application composition, API/UI, storage, providers, acquisition, and ranking; see [`src/AGENTS.md`](src/AGENTS.md). | Keep source adapters replaceable, ranking inspectable, and persistence separate from network access. |
| `tests/` | Unit/API/provider/storage regressions and safe XML fixtures; see [`tests/AGENTS.md`](tests/AGENTS.md). | Tests remain hermetic; fixtures contain no real credentials or personal archives. |
| `docs/` | Current architecture, contracts, auth, provider, onboarding, and wiki source; see [`docs/AGENTS.md`](docs/AGENTS.md). | Documentation distinguishes designed, local, hosted, and deployed behavior. |
| `deploy/` | Deployment handoff and Azure notes; see [`deploy/AGENTS.md`](deploy/AGENTS.md). | Deployment docs do not grant deployment authority or prove a live resource exists. |
| `.github/workflows/` | Test, audit, packaging, publication, and wiki-sync automation. | Preserve existing read/OIDC/publish boundaries; documentation work does not widen runner trust or permissions. |

## Storage and authentication boundaries

- SQLite stores candidates, personal events, rankings, provider connections, and
  resumable bootstrap jobs. It stores opaque credential references, never provider token
  values.
- `archive_dir` is separate raw personal-data storage. Uploads are content-addressed,
  inspected without unsafe extraction, and retained outside the live SQLite event tables
  so parsers can improve without requesting the export again.
- Provider OAuth identifies the connected provider account and is independent of instance
  authentication. `AzureKeyVaultCredentialStore` stores token material in Key Vault and
  returns `akv://...` references whose names hash the account ID.
- Hosted Azure authentication supplies `X-MS-CLIENT-PRINCIPAL-ID`; the application only
  authorizes the configured owner object ID. Do not treat a caller-supplied header as
  trusted outside the hosting boundary.
- The public landing and health routes are separate from protected connections, archive,
  API, and UI routes. Keep the public/private boundary intact.

`README.md`, scoped `AGENTS.md` maps, `src/`, tests, `deploy/`, and current contract
docs under `docs/` are living surfaces. Milestone, roadmap, prior-art, and dated
evidence documents are retained history; label a behavior superseded only when current
source and tests prove the replacement. Documentation CI selects the living surface and
explicitly leaves those historical records out.

## Where to change things

- Change composition or storage/auth behavior in `src/personal_algorithm/` and add a
  focused test under `tests/`.
- Change a provider's acquisition semantics under `src/personal_algorithm/sources/` or
  its provider module; keep `models.py`, `providers.py`, and serialization contracts in
  sync.
- Change deployment assumptions in `docs/` or `deploy/`, not in a readme-only claim.
- Use descriptive names for new living Markdown. Single-word names such as `README.md`
  and `AGENTS.md` are valid; reject new numeric-only or issue-number-only living docs
  such as `123.md` and `issue-123.md`. Meaningful numbered series and historical
  evidence IDs remain valid.
- Keep repository links relative so the documentation checker validates map references.

## Exact validation

```bash
git diff --check
git fetch origin main
python tools/check_documentation_hygiene.py --base origin/main --head HEAD --self-test
python -m pip install -e ".[dev]"
ruff check .
pytest -q
DOCS_FILE="$(mktemp)"
python tools/check_documentation_hygiene.py --base origin/main --head HEAD --print-docs > "$DOCS_FILE"
if test -s "$DOCS_FILE"; then mapfile -t DOCS < "$DOCS_FILE"; npx --yes markdownlint-cli2@0.18.1 --config .markdownlint-cli2.mjs "${DOCS[@]}"; lychee --offline --verbose --no-progress "${DOCS[@]}"; fi
```

The documentation workflow is read-only. It does not build, publish, deploy, or change
runner admission or permissions.
