# Deployment

The reference hosted deployment targets Azure but deployment artifacts are intentionally separate from core recommendation logic.

Required properties:

- private application origin behind an identity-aware public entry point;
- verified stable owner subject forwarded to the application;
- inbound copies of the trusted identity header stripped/replaced;
- managed identity for Azure Key Vault;
- least-privilege Key Vault secret data-plane access;
- persistent storage for SQLite or a later durable store;
- HTTPS-only public surface;
- health probe access without exposing private routes.

Azure IaC should be introduced behind a reviewed what-if before any deployment is performed.
