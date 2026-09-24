# Azure Key Vault credential store

Azure Key Vault is the reference hosted credential store for The Personal Algorithm.

## Why

Provider OAuth refresh/access tokens are secrets, not application data. Key Vault provides a purpose-built secret data plane, Microsoft Entra authentication, Azure RBAC, audit/monitoring integration, deletion/recovery controls, and managed-identity access from Azure-hosted applications.

The application stores only opaque references such as:

```text
akv://tpa-github-<hashed-account-id>
```

The provider account identifier is hashed before it becomes part of the Key Vault secret name.

## Authentication

Production Azure deployments should use a managed identity and `DefaultAzureCredential`. No Key Vault access credential should be stored in application configuration.

For local development, `DefaultAzureCredential` can use an authenticated Azure developer identity. Local development should use a development vault, not the production vault.

## RBAC

Use the Azure RBAC permission model, not legacy Key Vault access policies.

The runtime identity needs secret data-plane permissions sufficient for the operations actually used by the CredentialStore: set/update, get, and delete. Do not grant Contributor/Owner merely to make secret access work.

Administrative identities and the application runtime identity should remain separate.

## Vault policy

Recommended hosted defaults:

- one vault for this application/environment;
- RBAC authorization;
- soft delete and purge protection;
- diagnostic logging;
- network restrictions/private endpoint where deployment architecture permits;
- no secret values in application logs;
- separate development and production vaults.

## Deletion semantics

`CredentialStore.delete` begins Key Vault secret deletion. It deliberately does not purge. Purging is an administrative/deployment policy and should not be available to ordinary provider-disconnect code.

## Portability

Azure Key Vault is a reference implementation, not a core requirement. The `CredentialStore` protocol remains provider-neutral so a non-Azure self-hoster can implement an OS keychain, Vault, Kubernetes secret provider, or another secure backend.
