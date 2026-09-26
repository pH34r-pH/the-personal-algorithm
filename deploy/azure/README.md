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

## Key Vault roles

There are two distinct Key Vault consumers:

- Container Apps secret-reference resolution only needs read access (Microsoft documents **Key Vault Secrets User** for this).
- The Personal Algorithm runtime itself creates, updates, reads, and soft-deletes provider-token secrets, so its managed identity needs a role covering those data-plane operations. The current template uses **Key Vault Secrets Officer** for the runtime identity.

The application never purges secrets.

## Deployment caveat

The current template references the GitHub client secret from Key Vault during Container App creation while using the app's system-assigned identity. Azure documentation notes that system-assigned identity is unavailable until the app exists in some creation flows. We must validate this exact ARM/Bicep dependency path with Azure what-if/deployment validation before applying it. If Azure rejects the cycle, switch the secret-reference bootstrap to a user-assigned identity created first.

Key Vault networking is initially public-endpoint enabled; private networking is a later hardening step.

Authentication is intentionally not guessed in this first template. Container Apps supports built-in authentication, but the exact identity provider/app registration and verified-header mapping must be reviewed against the application's immutable owner-object-ID contract before public exposure.
