# The Personal Algorithm

**Your feeds. Your history. Your objectives. Your algorithm.**

The Personal Algorithm is an open, self-hosted framework for taking ownership of recommendation and discovery. Sources provide candidates; your instance decides what deserves your attention.

> Platforms may supply content. They do not have to own the final ranking policy.

## Status

Early design scaffold. The first milestone is deliberately small: open feeds and public sources -> a universal Candidate contract -> an inspectable deterministic ranker -> explanations -> a private web feed -> explicit feedback.

## Principles

1. **User-owned ranking.** The objective function belongs to the person running the instance.
2. **Explain every recommendation.** Scores should carry an inspectable contribution trace.
3. **Local-first personal state.** History, feedback, embeddings, credentials, and private profiles are deployment data, not repository data.
4. **Sources are adapters.** Prefer official APIs, feeds, exports, and permitted access. Never make bypassing access controls a product requirement.
5. **Discovery before consumption.** Linking back to the source is the default; copying or re-hosting third-party content is not required.
6. **Exploration is intentional.** Familiarity, adjacency, counterpoint, serendipity, and chaos are explicit policy choices.
7. **Models advise; people govern.** A model may propose policy changes, but policy changes remain visible and user-controlled.
8. **Portable core.** Source, ranker, presentation, and learner interfaces should be independently replaceable.

## Architecture

```text
Source adapters
      |
      v
   Candidate
      |
      v
    Ranker <-------------------+
      |                        |
      v                        |
RankedCandidate                |
 + Explanation                 |
      |                        |
      v                        |
 Presentation                  |
      |                        |
      v                        |
 Interaction ----------------> Learner
```

See [docs/architecture.md](docs/architecture.md) and [docs/prior-art.md](docs/prior-art.md).

## First vertical slice

The initial implementation should prove the contract rather than maximize source coverage:

- RSS/Atom
- arXiv
- GitHub
- universal Candidate schema
- deterministic transparent ranking
- explanation trace
- explicit feedback events
- private web UI
- Docker Compose deployment

YouTube, Reddit, social networks, historical-data import, learned rankers, natural-language policy editing, and federation are intentionally later.

## Personal deployments

The public repository contains code and example configuration only. A deployment owns its profile, source credentials, history, interaction logs, embeddings, and policy configuration.

A future goal is a reproducible path approximately as simple as:

```sh
cp .env.example .env
docker compose up
```

followed by browser-based onboarding.

## Non-goals (for now)

- becoming another social network
- mirroring the entire web
- maximizing engagement or time-on-site
- training a foundation model on third-party content
- bypassing authentication, rate limits, DRM, or anti-bot controls
- federation between Personal Algorithm instances

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
