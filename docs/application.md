# Private application composition

`personal_algorithm.application.create_private_app` is the composition root for the hosted single-owner instance.

It creates one shared set of application services:

- SQLite Store
- ConnectionStore
- BootstrapCoordinator
- CredentialStore (Azure Key Vault by default)
- GitHubProvider
- ProviderRegistry
- GitHub browser authorization
- single-owner InstanceAuth
- Connections API
- private server-rendered UI

The health endpoint remains public for infrastructure probes. Connections and UI routes share the same owner-auth boundary and provider state.

## Required environment

```text
TPA_DATABASE
TPA_OWNER_OBJECT_ID
TPA_KEY_VAULT_URL
TPA_GITHUB_CLIENT_ID
TPA_GITHUB_CLIENT_SECRET
```

`TPA_DATABASE` defaults to `data/personal-algorithm.sqlite3`; the remaining settings fail closed if absent.

The GitHub client secret is itself deployment secret material. For the reference Azure deployment it should be injected from Key Vault/platform secret references rather than committed or placed in a checked-in environment file.

## Start

A deployment can expose:

```sh
uvicorn personal_algorithm.application:app_from_env --factory
```

The application origin must remain behind the trusted identity-aware front end described in `docs/instance-auth.md`.
