# Architecture

The Personal Algorithm is a single-user recommendation control plane. Its architecture is intentionally layered so that acquisition, ranking, presentation, and learning can evolve independently.

```text
sources / provider imports
          |
          v
       Candidate
          |
          v
        Ranker <----- versioned Policy
          |
          v
   RankedCandidate
      + Explanation
          |
          v
     Presentation
          |
          v
      Interaction
          |
          v
        Learner
```

## Source boundary

A source adapter answers “what candidates are available?” It should not decide final ranking policy. Open feeds and authenticated providers can share the same Candidate boundary even though their acquisition and credential models differ.

## Storage boundary

Single-user deployments begin with SQLite. Transformed records retain provenance. Raw account archives may be retained separately so parsers can improve without asking the user to re-download history.

## Ranking boundary

The reference ranker is deterministic and weighted. Its output includes the policy version, ranker identity, final score, and an explanation whose signed contributions sum to that score. Learned rankers can be explored without weakening the reference contract.

## Learning boundary

Interactions are immutable events. A learner may derive new preference state, but it must not rewrite historical feedback. Explicit actions and future implicit telemetry remain distinguishable.

## Presentation boundary

The private application consumes ranked candidates and explanations. Canonical source links remain primary. Re-hosting third-party content is not required for useful discovery.

## Extension boundary

Core functionality should remain viable without a hosted model service or GPU. Source adapters, rankers, credential stores, and presentation surfaces are replaceable edges around stable domain contracts.

For exact field definitions and deferred questions, see [docs/architecture.md](https://github.com/pH34r-pH/the-personal-algorithm/blob/main/docs/architecture.md).
