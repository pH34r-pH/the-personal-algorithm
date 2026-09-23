# Roadmap

## M0 — contracts and fixture feed

- define Candidate, RankedCandidate, Explanation, Interaction, Policy
- fixture source with deterministic sample data
- SQLite migrations
- deterministic reference ranker
- rank/explanation tests
- minimal API

**Exit:** a test can ingest fixture candidates, rank them reproducibly, explain every score, and record feedback.

## M1 — useful open-web slice

- RSS/Atom adapter
- arXiv adapter
- GitHub adapter
- private responsive feed UI
- source filters and contexts
- Docker Compose
- import/export policy configuration

**Exit:** a new user can self-host a useful multi-source feed without a model API.

## M2 — semantic personalization

- local embeddings
- novelty/similarity features
- preference state from explicit feedback
- saturation controls
- exploration buckets
- offline ranking evaluation

## M3 — personal history

- optional HPI-compatible bridge
- archive/import adapters
- bootstrap interests from historical data
- provenance explorer

## M4 — policy control

- visual algorithm editor
- temporary contexts and expiring policy deltas
- natural-language policy proposal
- before/after simulation prior to applying changes

## Later

- source adapters with platform-specific authorization
- learned/bandit rankers
- mobile/PWA clients
- optional federation/recommendation exchange

Federation is deliberately not a prerequisite for the single-user product.
