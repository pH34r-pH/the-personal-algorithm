# GitHub browser authorization

The GitHub provider now has an explicit browser authorization boundary.

## Flow

```text
Connections UI
    |
    | begin()
    v
GitHub authorization page
    |
    | code + state
    v
callback
    |
    | one-time state validation
    | token exchange
    | GET /user validation
    v
CredentialStore (Key Vault)
    |
    v
opaque Connection
```

Authorization state is random, stored only as a SHA-256 digest, and consumed exactly once before token exchange. A replayed or unknown state is rejected.

The callback token is validated against GitHub before it is persisted.

## Deployment note

The initial AuthorizationStateStore is intentionally single-process/in-memory. It is correct for local development and makes the security contract testable, but it is not the final hosted implementation.

Before multiple web replicas or restart-tolerant OAuth are enabled, replace it with a short-lived durable state store with expiration. State data is not a long-term personal-data record.

## GitHub App migration

This module models the web authorization mechanics while the project settles the final GitHub App registration/deployment details. GitHub App installation tokens and permission discovery can later implement the same Connection/provider contracts without changing bootstrap or ranking.
