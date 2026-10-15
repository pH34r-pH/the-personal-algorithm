# Private application composition

`personal_algorithm.application.create_private_app` is the composition root for the
hosted single-owner instance. Source capabilities described here do not establish
that a selected deployment is running this revision or has working credentials.

The application shares SQLite state, connections, bootstrap checkpoints and the raw
archive inbox between its protected API and server-rendered UI. GitHub authorization
and its credential store are constructed only when that optional provider is configured.
The public landing page and health endpoint remain separate from owner-only routes.

## Environment and archive-only startup

`TPA_OWNER_OBJECT_ID` is required. `TPA_DATABASE` defaults to
`data/personal-algorithm.sqlite3`. `TPA_ARCHIVE_DIR` defaults to `data/archives`, and
`TPA_ARCHIVE_MAX_BYTES` defaults to 50 GiB per upload. Production should mount raw
archive storage separately from the live SQLite snapshot.

GitHub additionally requires all of:

```text
TPA_KEY_VAULT_URL
TPA_GITHUB_CLIENT_ID
TPA_GITHUB_CLIENT_SECRET
```

Missing or partial GitHub configuration disables its connection route with HTTP 503;
it does not prevent owner-authenticated archive intake. Tests can supply a credential
store explicitly instead of Key Vault. A complete configuration is not evidence that
Key Vault permissions, provider grants, callback routing or tokens currently work.
The GitHub client secret must be injected through private deployment secret references,
never committed or copied from another application's connector.

The private report at `/app/onboarding/readiness` lists configured providers, implemented
parsers and unimplemented capabilities. Live access remains `not_verified`. Optional
`TPA_SOURCE_REVISION` and platform `CONTAINER_APP_REVISION` values are diagnostic labels,
not an independently verified attestation of running code. No secret values are returned.

## Available intake routes

| Source | Current behavior | Important boundary |
| --- | --- | --- |
| GitHub | Optional OAuth; browser-driven repository metadata import | Not complete issue/PR/commit history or continuous sync |
| Spotify | Extended Streaming History parsing after archive upload | Other files remain retained, not normalized |
| Reddit | ZIP or standalone `comments.csv` / `posts.csv` parsing | Posts/comments snapshots only; other tables remain retained |
| Google, X, LinkedIn, Meta and other archives | Raw retention | Their semantic parsers and OAuth are not implemented here |

The UI uses the same parser registry as the import API. Supported uploads can be parsed
immediately through the browser's subsequent import request. This is not a durable
background worker: leaving before the request is made can leave an archive retained but
not parsed. Retained supported archives have an explicit import/retry action.

Provider bootstrap also remains browser-driven. Its UI reports the actual terminal
status rather than treating every stopped job as successful. Keep the page open during
that operation. Background execution and cross-device setup recovery remain follow-up work.

## Reddit exports

[Reddit's export guidance][reddit-help] says delivery normally arrives in the Reddit
inbox, with email used in some circumstances; preparation may take up to 30 days.
Check that inbox before assuming a missing email means the export is still pending.

Upload the original ZIP, or a named supported CSV, through the private onboarding page.
The parser uses standard-library streaming CSV/ZIP readers, never extracts members to
the filesystem and never calls Reddit. It rejects unsafe member paths, links, encrypted
CSV members and selected decompressed data exceeding its safety limits (128 MiB per
CSV, 512 MiB total). These limits are independent of the raw upload limit.

Multiline Unicode text and row provenance are retained. Invalid or timezone-free dates
remain unknown instead of receiving fabricated timestamps. Identical rows have stable
snapshot identities across exports; edited rows remain distinct observations. The
existing store's upsert updates the event's provenance to the most recently imported
copy, while the original archives remain available. Import summaries count event
upserts, not necessarily newly created rows. A valid empty table can contain zero events.

Malformed data is reported as failed with a sanitized message; raw data is retained.
Previously committed batches may remain after a late failure, and a retry safely upserts
those rows. The importer has synthetic regression coverage; real account exports still
require private validation. Do not upload personal archives or account results to GitHub.

[reddit-help]: https://support.reddithelp.com/hc/en-us/articles/360043048352-How-do-I-request-a-copy-of-my-Reddit-data-and-information

## Start and verification

```sh
uvicorn personal_algorithm.application:app_from_env --factory
```

The application origin must remain behind the trusted identity-aware front end described
in [instance authentication](instance-auth.md). An archive-only start does not make a
caller-supplied identity header safe on an exposed origin.

Before a real connection session, privately verify the deployed revision, owner sign-in,
origin protection, persistent storage, backup behavior and the configured provider's
callback and credential access. A passing health probe is not a completed onboarding
preflight. New OAuth grants and export requests still require the owner's provider session.
