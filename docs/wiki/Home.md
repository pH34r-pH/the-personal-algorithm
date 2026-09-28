# The Personal Algorithm

The Personal Algorithm is a self-hosted recommendation and discovery system built around one premise: **the person using a feed should own the ranking policy**.

Platforms and public sources can supply candidate content; a Personal Algorithm instance decides what deserves attention, records why an item was ranked where it was, and keeps private history and preference state inside the deployment boundary.

## What this project is

The project separates four concerns that are usually collapsed inside a platform feed:

1. **Acquisition** — source adapters discover candidate items.
2. **Policy** — an explicit, versioned policy determines ranking behavior.
3. **Explanation** — every reference ranking carries a contribution trace.
4. **Learning** — feedback becomes durable evidence without rewriting historical events.

The reference path is deliberately useful without a model API or GPU. Deterministic ranking, inspectable configuration, SQLite persistence, open-web sources, account connections, historical imports, and private presentation form the core.

## Start here

- [Getting started](Getting-Started.md) — install, configure, ingest, and run the development application.
- [Architecture](Architecture.md) — component boundaries and data flow.
- [Contracts and ranking](Contracts-and-Ranking.md) — Candidate, Policy, Explanation, Interaction, and ranking invariants.
- [Sources and providers](Sources-and-Providers.md) — open feeds, authenticated accounts, archives, and bootstrap jobs.
- [Privacy and security](Privacy-and-Security.md) — what belongs in repository state versus private deployment state.
- [Development](Development.md) — repository map, testing, and contribution boundaries.
- [Roadmap and status](Roadmap-and-Status.md) — stable milestones and how to read project status.

## Design principles

The repository is organized around durable boundaries rather than a particular recommender model. Source adapters should be replaceable. Rankers should be replaceable. A learned model may advise, but the applied policy remains visible and user-controlled. Third-party content stays at its canonical source by default rather than being silently mirrored.

The detailed architectural source of truth is [docs/architecture.md](https://github.com/pH34r-pH/the-personal-algorithm/blob/main/docs/architecture.md). This wiki is the navigational and explanatory layer; implementation contracts remain in code and the linked repository documentation.

## Project maturity

The project is under active development. Treat the repository tests and current documentation as the authority for implemented behavior, and the roadmap as intent rather than a compatibility promise. Private personal data, credentials, interaction history, embeddings, and profiles are deployment data and must not be committed to the public repository.
