# Azure reference deployment

This is an **IaC proposal**, not a deployed environment.

The reference shape uses Azure Container Apps: HTTPS ingress, system-assigned managed identity, Key Vault, Azure Files-backed persistent state, Log Analytics, and exactly one replica.

## Required review order

1. validate Bicep;
2. run resource-group what-if;
3. inspect all creates/modifies/deletes;
4. provision required secret values;
5. deploy only after approval;
6. configure Container Apps authentication;
7. verify the app origin cannot bypass authentication;
8. configure custom domain/DNS/certificate;
9. then route the private ph34r.dev surface.

SQLite + Azure Files is deliberately single-replica. Horizontal scale requires a different shared transactional state design.

The runtime identity needs Key Vault secret get/set/delete capability because provider tokens are managed by the application. Purge remains an administrative operation.

Key Vault networking is initially public-endpoint enabled; private networking is a later hardening step.

Authentication is intentionally not guessed in this first template. Container Apps supports built-in authentication, but the exact identity provider/app registration and verified-header mapping must be reviewed against the application's owner-subject contract before public exposure.
