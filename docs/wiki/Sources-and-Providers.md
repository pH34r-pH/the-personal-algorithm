# Sources and providers

The Personal Algorithm distinguishes **content sources** from **provider connections**.

A source answers where candidates can be acquired. A provider connection represents revocable authorization to a private third-party account. Keeping those concepts separate prevents account credentials, historical imports, and live feeds from becoming one opaque integration.

## Four boundaries

### Instance authentication

Controls access to the private Personal Algorithm application. It does not grant access to a third-party account.

### Provider connection

Represents a revocable grant. The connection stores an opaque credential reference; token material belongs behind the credential-store boundary.

### Bootstrap job

A finite, explicit historical import. Bootstrap work is resumable and should be idempotent at the imported-record boundary.

### Live source

An optional ongoing acquisition path. Completing a historical import must not silently enable continuous synchronization.

## Capability discovery

Providers advertise capabilities such as sign-in, candidate discovery, history import, archive request/import, and continuous sync. UI and orchestration should consume these descriptors instead of hard-coding a unique workflow for each provider.

## Historical data and policy

Imported history may become evidence for a proposed preference change. It must not silently mutate ranking policy. Suggested changes should remain inspectable and explicitly accepted.

See [docs/providers.md](https://github.com/pH34r-pH/the-personal-algorithm/blob/main/docs/providers.md), [docs/sources.md](https://github.com/pH34r-pH/the-personal-algorithm/blob/main/docs/sources.md), and the provider-specific documents under `docs/`.
