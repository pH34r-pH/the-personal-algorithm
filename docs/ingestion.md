# Feed ingestion

M1 adds a thin orchestration layer above source adapters.

`ingest_feeds` accepts:

- configured feed URLs;
- a versioned Policy;
- a Store;
- an optional fetcher for testing or alternate transports;
- an optional ranking timestamp.

For each run it:

1. asks the source adapter for Candidates;
2. deduplicates identical Candidate IDs within the run;
3. persists candidates;
4. ranks each candidate under the supplied policy;
5. persists the complete ranking/explanation trace;
6. returns a deterministic score-ordered result.

The function does not own scheduling, credentials, UI state, or policy editing. Those remain separate concerns.

## Deterministic regression checks

The ranking and ingestion contract has a bounded mutation baseline. Run it after
changing either orchestration or reference-ranking behavior:

```sh
irradiate run src/personal_algorithm/ranking.py src/personal_algorithm/ingest.py \
  --tests-dir tests --isolate --report json --output mutation-report.json
python tools/validate_mutation_report.py mutation-report.json \
  --require-file personal_algorithm/ranking.py \
  --require-file personal_algorithm/ingest.py
```

The report uses Stryker Mutation Testing Elements schema v2. The validator checks
the required report structure and status values; it does not impose a score gate.
That leaves equivalent or noisy mutants available for characterization before a
future ratchet is considered.

## Why keep orchestration thin?

Source adapters should be independently testable and replaceable. The ranker should not know where candidates came from. The store should not perform network access. This layer composes those capabilities without collapsing their boundaries.
