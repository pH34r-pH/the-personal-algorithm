# Instance authentication

The Personal Algorithm is a single-owner private application by default.

Hosted Azure deployments delegate **authentication** to Azure Container Apps built-in authentication (Easy Auth) with Microsoft Entra ID. The application performs only the second, deliberately small **authorization** check: the verified Entra principal object ID must equal the configured owner object ID.

## Azure trust boundary

Production uses the platform-provided header:

```text
X-MS-CLIENT-PRINCIPAL-ID
```

Azure Container Apps authentication supplies authenticated principal metadata to the container. The public root and health endpoint may remain anonymous, while private UI routes send unauthenticated browser requests through `/.auth/login/aad` and private API routes fail closed with 401/403.

The application compares the header with the owner ID. Azure Easy Auth authenticates
the session, strips caller-supplied identity headers, and supplies its own authenticated
identity before forwarding a request. Require HTTPS and keep the origin reachable
only through that authenticated ingress. Apply the same header handling and transport
requirements to any additional proxy in the request path. See the
[Azure Container Apps authentication documentation](https://learn.microsoft.com/en-us/azure/container-apps/authentication).

The hosted factory starts only when the platform operator sets
`TPA_TRUSTED_HOST_AUTH=azure-container-apps-easy-auth`. This setting declares the
ingress contract. The app cannot verify it from inside the container or authenticate
the header value itself. Before exposing a revision, the deployment owner must
privately confirm that the origin cannot be reached directly and that an anonymous
or non-owner request with a caller-supplied identity header is denied at ingress
or by the application after platform authentication.

`TPA_OWNER_OBJECT_ID` is the owner's immutable Microsoft Entra directory object ID. Use this instead of an email address, UPN, or display name because those identifiers can change while the directory object remains the same.

## Local development

Tests inject synthetic headers into the composed app to model the platform handoff.
They cover missing identity, a different owner, and the configured owner. The real
ingress and its handling of untrusted headers require the deployment checks above.

## Separation from provider OAuth

Entra authentication answers **who may use this Personal Algorithm instance**.

GitHub OAuth/App credentials answer **which GitHub account/data the instance may access**.

These are independent trust boundaries.

## Scope

This milestone protects private routes. It intentionally does not implement passwords, multiple owners, delegated administration, or public sharing inside the application.
