# Security policy

## Reporting a vulnerability

Please do not disclose vulnerabilities, credentials, OAuth material, private archives, or personal-data samples in a public issue.

Use GitHub's private vulnerability reporting / Security Advisories for this repository when available. Include the affected revision, a minimal reproduction, impact, and any known mitigation. Avoid attaching real personal data when synthetic data can demonstrate the problem.

## Scope

Security-sensitive areas include instance authentication, OAuth/provider callbacks, credential storage, archive ingestion, path handling, private APIs, deployment configuration, and any feature that could expose personal history or ranking state.

The development server and example configuration are not a hardened public deployment boundary. See the deployment and provider documentation before exposing an instance to the Internet.
