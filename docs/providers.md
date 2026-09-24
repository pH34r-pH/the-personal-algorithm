# Provider and bootstrap model

Typed sources answer **where can candidates be acquired?** Provider connections answer a different question: **what private account has this instance been authorized to access?**

The Personal Algorithm keeps those concepts separate.

## Four boundaries

### Instance authentication

Controls access to the private Personal Algorithm website. It does not imply authorization to any third-party provider.

### Provider connection

A revocable authorization grant to a third-party account. A Connection stores only an opaque `credential_ref`; token material belongs behind the CredentialStore boundary.

### Bootstrap job

A finite, explicit historical import. Bootstrap is resumable through checkpoints and should be idempotent at the imported-record boundary.

### Live source

An optional ongoing acquisition path. Completing a bootstrap must not silently enable continuous synchronization.

## Capability discovery

Providers advertise capabilities from a common vocabulary:

- sign in
- candidate discovery
- history import
- archive request
- archive import
- continuous sync

The eventual Connections UI should render these descriptors rather than contain a separate hard-coded workflow for every provider.

## Credentials

Provider passwords are never accepted.

OAuth/App tokens must not be written into:

- instance source configuration;
- candidate metadata/provenance;
- interaction/ranking databases;
- logs;
- repository files;
- portable Personal Algorithm exports.

The current milestone defines an opaque CredentialStore interface only. An encrypted implementation and deployment key-management decision must land before authenticated providers are usable outside tests.

## Bootstrap and ranking policy

Historical imports may produce evidence for proposed interests. They must not silently mutate ranking policy. Any inferred policy change should be inspectable and explicitly accepted by the user.
