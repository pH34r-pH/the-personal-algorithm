# The Personal Algorithm

[![CI](https://github.com/pH34r-pH/the-personal-algorithm/actions/workflows/ci.yml/badge.svg)](https://github.com/pH34r-pH/the-personal-algorithm/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/pH34r-pH/the-personal-algorithm)](LICENSE)

<p align="center">
  <img src="docs/assets/hero.webp" alt="The Personal Algorithm — private curation and user-owned ranking" width="100%">
</p>

**Your feeds. Your history. Your objectives. Your algorithm.**

The Personal Algorithm is an open, self-hosted recommendation and discovery system that makes ranking policy an explicit part of the product. Sources provide candidates; your instance decides what deserves attention, preserves provenance, and explains the reference score.

> Platforms may supply content. They do not have to own the final ranking policy.

## Why this exists

Most recommendation systems combine acquisition, ranking, feedback, and business objectives behind one opaque interface. This project separates them. A useful instance should be able to ingest permitted sources, rank candidates deterministically under a versioned policy, show why each item received its score, record semantic feedback, and keep personal state inside the deployment boundary.

The core remains useful without a model API or GPU.

## What is implemented

The repository contains portable Candidate / RankedCandidate / Explanation / Interaction / Policy contracts, SQLite-backed state, deterministic ranking, source and provider abstractions, open-feed ingestion, private application/API surfaces, historical-import plumbing, deployment documentation, and CI/structural quality gates.

Implementation changes quickly. The default branch and tests are authoritative; [the roadmap](docs/roadmap.md) describes intended milestones rather than promises.

## Quick start

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp config.example.json config.json
personal-algorithm-ingest --config config.json
```

Run the development application with:

```sh
uvicorn personal_algorithm.api:app --reload
```

Do not expose the unauthenticated development server directly to the public Internet.

## Architecture

```text
sources / imports -> Candidate -> Ranker + Policy -> RankedCandidate + Explanation
                                                           |
                                                           v
                                                     Presentation
                                                           |
                                                           v
                                                     Interaction -> Learner
```

The important boundary is ownership: source adapters acquire, policy decides, explanation makes the reference decision inspectable, and immutable interaction events provide evidence for future preference state.

Read [Architecture](docs/architecture.md), [Public contracts](docs/contracts.md), and the [project Wiki](https://github.com/pH34r-pH/the-personal-algorithm/wiki) for the complete map.

## Privacy model

The public repository contains code, tests, fixtures, and safe example configuration. Real credentials, provider tokens, archives, interaction history, embeddings, profiles, and personal policy state are deployment data. They do not belong in Git.

Provider passwords are not an integration mechanism. Authenticated providers use revocable authorization and opaque credential references; historical bootstrap and continuous sync remain separate capabilities.

## Repository map

- `src/personal_algorithm/` — application and domain implementation.
- `tests/` — executable contracts and regression coverage.
- `docs/` — authoritative design and deployment documentation.
- `docs/wiki/` — canonical source for the public GitHub Wiki.
- `deploy/` — deployment-specific material.
- `.github/workflows/` — CI, publication, and quality automation.

## Contributing and security

Contributions are welcome; read [CONTRIBUTING.md](CONTRIBUTING.md) and use the pull-request template. Security problems or accidental personal-data exposure should follow [SECURITY.md](SECURITY.md), not a public issue containing sensitive material.

## Citation and license

Research use can cite [CITATION.cff](CITATION.cff). The project is licensed under the Apache License 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
