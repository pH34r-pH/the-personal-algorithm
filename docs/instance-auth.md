# Instance authentication

The Personal Algorithm is a single-owner private application by default.

Hosted Azure deployments delegate **authentication** to Azure Container Apps built-in authentication (Easy Auth) with Microsoft Entra ID. The application performs only the second, deliberately small **authorization** check: the verified Entra principal name must equal the configured owner principal name.

## Azure trust boundary

Production uses the platform-provided header:

```text
X-MS-CLIENT-PRINCIPAL-ID
```

Azure Container Apps authentication runs before application code and supplies the authenticated principal metadata to the container. Public ingress must be configured to require authentication; the application must not be deployed as a publicly anonymous service while relying on this header.

`TPA_OWNER_SUBJECT` is the owner's stable Entra principal name.

## Local development

Tests may construct `InstanceAuth` with a different `identity_header` when direct header injection is useful. This is a development/testing convenience, not the production authentication mechanism.

## Separation from provider OAuth

Entra authentication answers **who may use this Personal Algorithm instance**.

GitHub OAuth/App credentials answer **which GitHub account/data the instance may access**.

These are independent trust boundaries.

## Scope

This milestone protects private routes. It intentionally does not implement passwords, multiple owners, delegated administration, or public sharing inside the application.
