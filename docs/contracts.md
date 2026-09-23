# Public contracts

M0 establishes four portable domain contracts and one policy contract. They are Python dataclasses internally; JSON is validated at the HTTP boundary.

## Candidate

A recommendation candidate is a pointer plus enough metadata to rank and explain it.

Required fields:

- `id`: stable identifier inside the Personal Algorithm instance
- `source`: adapter/source namespace
- `source_id`: source-native stable identifier
- `canonical_url`: destination for consumption
- `title`
- `discovered_at`: timezone-aware ISO-8601 timestamp
- `content_type`

Optional fields include author, summary, publication time, topics, arbitrary source metadata, and provenance.

The canonical URL and provenance are first-class because the default product behavior is discovery, not third-party-content replication.

## Policy

A Policy has a version plus named feature weights and explicit topic/source affinity maps. M0's reference ranker recognizes:

- `topic_affinity`
- `source_affinity`
- `recency`

Unknown weights are harmless to the reference ranker. Future rankers may introduce additional features without changing Candidate.

## RankedCandidate and Explanation

Every reference ranking contains:

- final score
- ranker identity
- policy version
- ranking timestamp
- an Explanation

An Explanation contains every feature's raw value, policy weight, signed contribution, and human-readable reason. The final score is the sum of contributions.

This invariant is tested.

## Interaction

Interactions are immutable feedback events. Initial explicit vocabulary:

`opened`, `skimmed`, `finished`, `saved`, `cited`, `shared`, `learned`, `already_knew`, `disagreed`, `misleading`, `irrelevant`, `more_like_this`, `less_like_this`.

Explicit feedback is distinguished from future implicit telemetry.

## Boundary rules

- timestamps must carry a timezone;
- required strings must be non-empty;
- topic lists contain strings;
- policy weights are numeric;
- unknown interaction kinds are rejected;
- interactions cannot reference an unknown candidate;
- malformed public input returns a client error rather than leaking persistence details.
