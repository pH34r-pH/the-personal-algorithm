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
3. freezes the candidate and policy inputs and evaluates them at one time;
4. atomically persists the latest candidate rows and generated ranking run;
5. returns descending score order with candidate ID as the tie-break.

The stored run has a fixed membership and a reproducible explanation for each
position. The local ingestion command emits each item's `run_id`, position,
and candidate input digest with its score. A presentation is recorded
separately when a caller confirms which candidates were shown together.
[Ranking and presentation contract](contracts.md#rankingrun-and-impression)

The function does not own scheduling, credentials, UI state, or policy editing. Those remain separate concerns.

## Local message evidence

The archive uploader accepts structured Pixel message JSONL with the `messages` provider
hint. `personal_algorithm.message_import.import_sent_messages` also accepts that JSONL
and a `Store` directly. It only imports records explicitly labeled `sent`,
retains source-file digest and row position, and omits recipient details and media.
The input and database are private instance data and must remain outside this public
repository. Source direction labels and device coverage are not independently verified.
Stored messages are evidence only; the importer does not change ranking policy or infer
preferences. Import reads a retained upload; it does not connect to a live device.

## Deterministic regression checks

The ranking and ingestion contract has a bounded mutation baseline. Run it after
changing either orchestration or reference-ranking behavior:

```sh
irradiate run src/personal_algorithm/ranking.py src/personal_algorithm/ingest.py \
  --tests-dir tests --isolate --report json --output mutation-report.raw.json
python tools/validate_mutation_report.py mutation-report.raw.json \
  --normalize-to mutation-report.json \
  --require-file personal_algorithm/ranking.py \
  --require-file personal_algorithm/ingest.py
```

The normalized report uses the pinned official Stryker Mutation Testing Elements
schema v2. The raw report is retained for provenance. The validator rejects
ambiguous duplicate IDs; it only reconciles the known irradiate 0.4.3
zero-duration `NoCoverage` export duplicate and records that decision in the
report configuration. It does not impose a score gate, leaving equivalent or
noisy mutants available for characterization before a future ratchet is
considered.

## Why keep orchestration thin?

Source adapters should be independently testable and replaceable. The ranker should not know where candidates came from. The store should not perform network access. This layer composes those capabilities without collapsing their boundaries.
