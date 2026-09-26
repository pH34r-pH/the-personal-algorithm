# Instance authentication

The Personal Algorithm is a single-owner private application by default.

Hosted Azure deployments delegate **authentication** to Azure Container Apps built-in authentication (Easy Auth) with Microsoft Entra ID. The application performs only the second, deliberately small **authorization** check: the verified Entra principal object ID must equal the configured owner object ID.

## Azure trust boundary

Production uses the platform-provided header:

```text
X-MS-CLIENT-PRINCIPAL-ID
```

Azure Container Apps authentication supplies authenticated principal metadata to the container. The public root and health endpoint may remain anonymous, while private UI routes send unauthenticated browser requests through `/.auth/login/aad` and private API routes fail closed with 401/403.

The hosting layer must keep `X-MS-CLIENT-PRINCIPAL-ID` platform-controlled so an external caller cannot spoof it.

`TPA_OWNER_OBJECT_ID` is the owner's immutable Microsoft Entra directory object ID. Use this instead of an email address, UPN, or display name because those identifiers can change while the directory object remains the same.

## Local development

Tests may construct `InstanceAuth` with a different `identity_header` when direct header injection is useful. This is a development/testing convenience, not the production authentication mechanism.

## Separation from provider OAuth

Entra authentication answers **who may use this Personal Algorithm instance**.

GitHub OAuth/App credentials answer **which GitHub account/data the instance may access**.

These are independent trust boundaries.

## Scope

This milestone protects private routes. It intentionally does not implement passwords, multiple owners, delegated administration, or public sharing inside the application.
