# Connections API

The private Connections surface is now provider-driven rather than hard-coded around source types.

## Discover

`GET /connections` returns provider descriptors plus current connection state. A web UI can render Connect/Connected state, requested permissions, and supported capabilities from this response.

## GitHub authorization

When GitHub authorization is configured:

- `GET /connections/github/connect?redirect_uri=...` redirects to GitHub.
- `GET /connections/github/callback?code=...&state=...` validates the callback, stores the token through CredentialStore, and persists only the opaque Connection.

The callback response never contains the access token.

## Manual bootstrap

- `POST /connections/{provider}/bootstrap` creates a pending finite job.
- `POST /connections/{provider}/bootstrap/{job}/step` executes one resumable provider step.
- `GET /connections/{provider}/bootstrap/{job}` exposes progress.

This API deliberately requires explicit step calls. A future UI can offer a "continue until complete" action, but the underlying operation remains observable and resumable.

## Security boundary

These routes are intended for a private authenticated application. They must not be exposed publicly until instance authentication/authorization middleware is present.

Provider credentials remain in CredentialStore/Key Vault; the application database contains only opaque references.
