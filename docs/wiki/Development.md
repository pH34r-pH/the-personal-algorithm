# Development

## Repository map

- `src/personal_algorithm/` — core contracts, ranking, persistence, providers, APIs, and UI.
- `tests/` — contract and behavior tests.
- `docs/` — architecture, provider, deployment, source, and milestone documentation.
- `deploy/` — deployment-specific material.
- `config.example.json` — safe example instance configuration.
- `.github/workflows/` — CI, publication, and structural quality automation.

## Change discipline

Prefer changes that preserve replaceable boundaries:

- source-specific behavior stays in adapters/providers;
- credentials stay behind the credential-store abstraction;
- ranking semantics stay explicit and versioned;
- historical interactions remain immutable;
- explanations remain complete enough to reconstruct a reference score;
- private deployment state does not enter fixtures or documentation.

## Tests

Install development dependencies and run the repository test suite before submitting a change. When changing a contract, add tests for both valid and invalid boundary behavior rather than only the happy path.

## Documentation

A behavior-changing pull request should update the nearest authoritative document in `docs/`. Wiki pages are orientation and navigation; they should link to detailed sources rather than fork them.

Read [CONTRIBUTING.md](https://github.com/pH34r-pH/the-personal-algorithm/blob/main/CONTRIBUTING.md) before opening a pull request.
