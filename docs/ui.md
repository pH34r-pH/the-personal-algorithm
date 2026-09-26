# Private web UI

The first UI intentionally covers the bootstrap experience.

The private home establishes the product proposition and links to **Connections**. Provider cards are generated from ProviderDescriptor capability metadata rather than a separate UI model.

A disconnected GitHub provider offers **Connect GitHub**. A connected provider supporting historical import offers **Bootstrap history**. The browser drives one persisted bootstrap step at a time and reports imported counts; failure leaves the checkpoint available for later resumption.

Historical bootstrap and continuous discovery remain separate choices.

The UI router is protected by the single-owner instance-auth boundary. The same identity-aware-proxy requirements apply to HTML and JSON private surfaces.

This is deliberately server-rendered and dependency-light. A richer client can replace it later without changing provider/bootstrap contracts.


## Data-first onboarding

The private home now leads with **Onboarding** rather than an implementation-oriented provider list.

The onboarding page explicitly optimizes for data yield per owner action:

- one OAuth connection when it unlocks useful current/ongoing data;
- one provider archive request when the export is richer than the API;
- zero-login uploads for data already present on the owner's devices.

Raw archives are content-addressed and retained outside the SQLite snapshot. The page can accept multiple files from split exports and records provider hints plus archive manifests before provider-specific parsers exist.

GitHub authorization now constructs its callback URL from the current request and returns directly to onboarding, which automatically starts the finite GitHub history bootstrap after successful authorization.
