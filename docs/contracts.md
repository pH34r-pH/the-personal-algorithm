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

Domain, JSON, and stored replay tests check that the score, explanation total,
and sum of contributions agree. Checks use relative and absolute tolerances of
`1e-12`.

## RankingRun and Impression

A generated reference run records its identifier, one UTC evaluation time,
ranker version, policy content digest, and ordered members. The context fields
are null for the non-contextual reference ranker. Results sort by descending
score and then ascending candidate ID. Each member has a 1-based position,
content-addressed candidate snapshot, score, and explanation.

Candidate and policy snapshots use canonical UTF-8 JSON with sorted keys and
finite numbers. Candidate timestamps are normalized to UTC. SQLite deduplicates
identical snapshots by SHA-256 and writes a whole generated run in one
transaction. Replay reads those snapshots and verifies the retained ordering
and explanations using the original evaluation time. A new ranking creates a
new run identity.

The standalone local API accepts a candidate list at `POST /ranking-runs` and
keeps `POST /rank` for one candidate. `GET /ranking-runs/{id}` verifies and
returns the retained result. Ranking and ingestion create generated runs.

`POST /impressions` records a presentation confirmed by the caller. It
accepts the run ID, observed candidate IDs in presentation order, and actual
presentation time. The caller can supply a stable impression ID for retries.
Reusing that ID requires the same run, order, and time. Presentation evidence
and later interactions have separate identities. Records from before this
feature retain their original evidence.

## Interaction

Interactions are immutable feedback events. Initial explicit vocabulary:

`opened`, `skimmed`, `finished`, `saved`, `cited`, `shared`, `learned`, `already_knew`, `disagreed`, `misleading`, `irrelevant`, `more_like_this`, `less_like_this`.

Explicit feedback is distinguished from future implicit telemetry.
Feedback can supply an `impression_id`. The associated impression must have
included that candidate at or before the feedback timestamp.

## Boundary rules

- timestamps must carry a timezone;
- required strings must be non-empty;
- topic lists contain strings;
- policy weights and affinity values are finite numbers, including signed and zero values;
- unknown interaction kinds are rejected;
- interactions cannot reference an unknown candidate;
- malformed public input returns a client error rather than leaking persistence details.
