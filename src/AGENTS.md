# Source boundary

`src/personal_algorithm/` is the installable package and the runtime source of truth.

```text
application.py -> API/UI routers -> private auth wrappers
       |-> Store / ConnectionStore / BootstrapCoordinator
       |-> ArchiveInbox -> provider-specific importers
       |-> ProviderRegistry -> provider OAuth/acquisition
source_config -> sources/{rss,arxiv}.py -> Candidate -> ingest -> rank -> Store
```

- Composition belongs in [`personal_algorithm/application.py`](personal_algorithm/application.py).
- SQLite schema and durable writes belong in [`personal_algorithm/store.py`](personal_algorithm/store.py);
  provider connection metadata belongs in `connections.py`, and raw archive files belong
  in `archives.py` outside the live event tables.
- Provider contracts and registry live in `providers.py` and `provider_registry.py`;
  acquisition adapters live in `sources/` and must remain independently testable.
- Ranking policy and explanations belong in `ranking.py`/`models.py`; do not hide network
  access or credentials in the ranker.
- `credentials.py` is the opaque-secret protocol; `azure_credentials.py` is the Key Vault
  implementation. `instance_auth.py` authorizes the single owner after hosting auth.

Provider secrets, raw personal archives, and deployment credentials never belong in the
source tree or ordinary SQLite payloads. Run `ruff check .` and `pytest -q` after source
changes.
