# Private web UI

The first UI intentionally covers the bootstrap experience.

The private home establishes the product proposition and links to **Connections**. Provider cards are generated from ProviderDescriptor capability metadata rather than a separate UI model.

A disconnected GitHub provider offers **Connect GitHub**. A connected provider supporting historical import offers **Bootstrap history**. The browser drives one persisted bootstrap step at a time and reports imported counts; failure leaves the checkpoint available for later resumption.

Historical bootstrap and continuous discovery remain separate choices.

The UI router is protected by the single-owner instance-auth boundary. The same identity-aware-proxy requirements apply to HTML and JSON private surfaces.

This is deliberately server-rendered and dependency-light. A richer client can replace it later without changing provider/bootstrap contracts.
