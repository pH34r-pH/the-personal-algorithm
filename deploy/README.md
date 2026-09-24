# Deployment contract

The Personal Algorithm repository owns the portable application artifact and documents what a host must provide. It does **not** own pH34r-specific cloud infrastructure.

## Application artifact

Build the repository `Dockerfile`. The container listens on port 8000 and persists single-instance state at:

```text
/data/personal-algorithm.sqlite3
```

Required hosted configuration is documented in `docs/application.md` and `docs/instance-auth.md`.

## Host requirements

A production host must provide:

- HTTPS ingress;
- a trusted identity-aware authentication layer;
- an unspoofable verified owner-subject header;
- durable single-instance storage for `/data`;
- a CredentialStore implementation (Azure Key Vault is the reference backend);
- provider client credentials through a secret mechanism;
- exactly one application replica while SQLite remains the transactional state backend;
- health probing without exposing private routes.

## pH34r deployment

The private pH34r.dev Azure realization is owned by the private `pH34r-pH/long-haul-fleet` repository under `services/personal-algorithm/`.

This separation is intentional: downstream users can reproduce this application without inheriting one operator's Azure subscription topology, DNS authority, identities, or deployment workflows.
