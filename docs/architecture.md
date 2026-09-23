# Architecture

## Boundary

The Personal Algorithm is a single-user recommendation control plane. It discovers candidate objects from independently implemented sources, ranks them under an explicit user-owned policy, presents an explanation with every ranking, and records semantically meaningful feedback.

## Core contracts

### Candidate

Minimum portable representation of something the algorithm may recommend.

```text
id
source
source_id
canonical_url
title
author?
summary?
published_at?
discovered_at
content_type
topics[]
metadata{}
provenance{}
```

Adapters may retain source-specific metadata, but core ranking must not require every source to implement every optional field.

### RankedCandidate

```text
candidate
score
ranker
policy_version
explanation
ranked_at
```

### Explanation

An ordered set of signed score contributions plus human-readable evidence.

```text
final_score
contributions[]:
  feature
  value
  weight
  contribution
  reason
```

A ranker that cannot explain its output may exist experimentally, but it should not become the reference ranker.

### Interaction

Explicit feedback is richer than a click.

Initial vocabulary:

```text
opened
skimmed
finished
saved
cited
shared
learned
already_knew
disagreed
misleading
irrelevant
more_like_this
less_like_this
```

Implicit telemetry should be separately identified rather than masquerading as explicit preference.

## Components

### Sources

A source adapter emits Candidates. v0 sources: RSS/Atom, arXiv, GitHub.

### Store

Single-user deployments should begin with SQLite. Raw source payloads may optionally be retained separately. Every transformed record retains provenance.

### Ranker

Reference ranker: deterministic weighted features. Likely early features:

- semantic relevance
- novelty
- active-context relevance
- source/author affinity
- recency
- saturation penalty
- exploration allocation

The final score and every contribution are persisted.

### Policy

Policy is versioned configuration. A natural-language interface may propose a patch, but the resolved patch must be inspectable before application.

### Learner

Consumes Interaction events and produces derived preference state. It must not mutate historical events.

### Presentation

Private web UI consumes RankedCandidates. Source links remain canonical. Rendering full third-party content is optional and source-policy dependent.

## Privacy boundary

The repository may contain example profiles but never requires users to commit:

- OAuth/API credentials
- browsing or consumption history
- interaction logs
- private embeddings
- personal profile text
- imported platform archives

## Extension boundary

Source adapters and rankers should eventually be discoverable plugins. Core should remain useful with no model API and no GPU.

## Deferred questions

- embedding model and representation
- policy DSL
- ranking evaluation protocol
- source plugin packaging
- historical HPI/Dogsheep bridge
- authentication for public-host/private-use deployments
- federation / recommendation exchange
