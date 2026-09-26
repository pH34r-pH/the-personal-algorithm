# Manual bootstrap coordination

A provider bootstrap is an explicit finite job, not a hidden background sync.

The BootstrapCoordinator persists job status and checkpoints in SQLite. A UI or worker may invoke one `step` at a time and display progress between calls.

## Lifecycle

```text
PENDING -> RUNNING -> RUNNING -> ... -> COMPLETE
                    \-> FAILED
```

A checkpoint contains only resumable progress such as cursor, imported/skipped counts, and small provider metadata.

Provider page payloads are transient. They are converted into Candidates and persisted through the normal candidate store, then removed from the checkpoint before it is saved. This prevents bootstrap state from becoming a second unbounded copy of provider data.

## Resumption

A new process can reconstruct the coordinator around the same SQLite database and continue a RUNNING/FAILED job from its last persisted checkpoint.

Failed jobs retain the previous successful checkpoint. Retrying policy and cancellation UI are later workflow concerns.

## Separation from continuous sync

Completing a bootstrap does not enable continuous synchronization. A future Connections UI should present those as separate controls.


## Archive-first bootstrap

Provider OAuth is not the only bootstrap path. When a platform's downloadable archive contains materially more historical information than its API, the preferred flow is:

1. request the archive once through the provider;
2. upload one or more resulting files to the private archive inbox;
3. retain the raw archive content-addressed outside SQLite;
4. inspect and fingerprint it immediately;
5. run provider-specific resumable parsers into the neutral PersonalEvent store.

This preserves source data while allowing parsers and ranking policy to evolve independently.
