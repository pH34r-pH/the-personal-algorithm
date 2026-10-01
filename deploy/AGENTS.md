# Deployment boundary

`deploy/` contains deployment handoff documentation, not an executable deployment
surface. Azure-specific assumptions live under [`azure/`](azure/).

- Keep application runtime contracts in [`../docs/`](../docs/) and source in
  [`../src/`](../src/); update this boundary when deployment configuration changes.
- Preserve the separation between Azure Container Apps Easy Auth/Entra instance
  authentication, the application owner-object-id check, GitHub provider OAuth, and
  Azure Key Vault credential storage.
- Documentation does not prove that an Azure resource, identity, role, vault, or
  deployment currently exists. Require a current receipt or source configuration before
  making that claim.
- Do not add deployment, permission, or credential changes as part of documentation
  maintenance.
