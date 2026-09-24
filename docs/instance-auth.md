# Instance authentication

The Personal Algorithm is a single-owner private application by default.

The application does not implement passwords. Hosted deployments should use an identity-aware reverse proxy/platform authentication layer and pass a **verified** stable subject identifier to the application.

The application then performs the second check: only the configured owner subject may access private routes.

## Trust boundary

The default application header is:

```text
X-Personal-Algorithm-Subject
```

That header is trustworthy **only if the public deployment prevents clients from supplying it directly** and the fronting identity layer strips/replaces inbound copies.

Never expose the application directly to the Internet while trusting this header.

## Azure direction

For the reference Azure deployment, use Azure-hosted authentication/reverse-proxy functionality to authenticate the user and translate the verified platform identity into the application's owner-subject boundary. The precise Azure resource choice belongs in deployment IaC, not core ranking code.

The owner subject should be a stable provider identifier rather than an email address where possible.

## Local development

Tests and local development may inject the identity header directly. That is a development convenience, not an authentication mechanism.

## Scope

This milestone protects private routes. It does not yet implement:

- multiple users;
- delegated administration;
- sessions/passwords inside the application;
- public sharing;
- anonymous feed access.

Those are intentionally outside the single-owner product model.
