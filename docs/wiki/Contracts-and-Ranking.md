# Contracts and ranking

The project uses a small set of domain contracts so source integrations and ranking experiments do not leak provider-specific assumptions into the core.

## Candidate

A Candidate is a pointer plus enough metadata to rank and explain an item. Stable identity, source identity, canonical URL, title, discovery time, and content type form the minimum contract; optional author, summary, publication time, topics, source metadata, and provenance enrich it.

The canonical URL and provenance are first-class because the product is a discovery system, not a third-party content mirror.

## Policy

Policy is versioned configuration. The reference ranker recognizes explicit weighted features such as topic affinity, source affinity, and recency. A future natural-language interface may propose changes, but the resolved policy change should be inspectable before it is applied.

## RankedCandidate and Explanation

A reference ranking records:

- final score;
- ranker identity;
- policy version;
- ranking timestamp;
- a complete explanation trace.

Each explanation contribution records the feature value, weight, signed contribution, and a human-readable reason. The final score is the sum of those contributions.

## Interaction

Feedback is represented as immutable semantic events such as `saved`, `finished`, `learned`, `irrelevant`, `more_like_this`, and `less_like_this`. This is intentionally richer than a click/no-click signal.

## Ranking philosophy

The project does not optimize a hidden “engagement” objective. Novelty, active context, familiarity, counterpoint, exploration, saturation, and source affinity can all be explicit policy choices. This makes disagreement with the algorithm actionable: inspect the policy and trace, then change the policy.

Authoritative contract details live in [docs/contracts.md](https://github.com/pH34r-pH/the-personal-algorithm/blob/main/docs/contracts.md).
