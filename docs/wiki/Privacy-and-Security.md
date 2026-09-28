# Privacy and security

Personal data is a deployment concern, not repository content.

## Keep out of Git

A real instance may contain OAuth or API credentials, archive files, browsing and consumption history, interaction logs, embeddings, personal profile text, provider tokens, and policy state derived from private behavior. Those belong in the configured deployment stores and secret-management boundary.

## Credential rule

Provider passwords are not an accepted integration mechanism. OAuth or application tokens must not be copied into candidate metadata, interaction databases, source configuration committed to Git, logs, or portable exports.

## Development versus deployment

The local development API is a convenience, not a hardened Internet-facing authentication layer. Deployment documentation defines the supported identity, secret, and infrastructure boundaries.

## Provenance without leakage

Candidates and transformations should retain enough provenance to explain where an item came from without copying credentials or private provider payloads into public-facing records.

## Reporting security problems

Do not open a public issue containing a secret, token, private archive fragment, or exploitable vulnerability. Follow the repository's [security policy](https://github.com/pH34r-pH/the-personal-algorithm/blob/main/SECURITY.md).

The broader privacy boundary is documented in [docs/architecture.md](https://github.com/pH34r-pH/the-personal-algorithm/blob/main/docs/architecture.md) and provider credential rules in [docs/providers.md](https://github.com/pH34r-pH/the-personal-algorithm/blob/main/docs/providers.md).
