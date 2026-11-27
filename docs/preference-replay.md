# Offline preference replay

Use a private, reviewed snapshot for offline reference replay. The command
reads local files and writes a new local checkpoint or candidate. For newly
reviewed inputs, use the [governed format](#governed-source-use). Version 1
preserves exact replay of historical snapshots.

The input JSON object has six fields.

| Field | Value |
| --- | --- |
| `schema_version` | Integer `1`. |
| `evaluated_at` | A fixed timezone-aware ISO-8601 evaluation time. |
| `starting_policy` | An exact reviewed [Policy](contracts.md#policy). |
| `candidates` | A nonempty list of frozen [Candidate](contracts.md#candidate) objects. |
| `interactions` | A list of frozen feedback records described below. |
| `affinity_step` | A finite positive topic adjustment per explicit preference action. |

Each feedback record carries `event_id`, `policy_sha256`, `origin`,
`candidate_id`, `kind`, `occurred_at`, and the Boolean `explicit` flag.
Use a stable producer event ID across retries. Use the starting policy's
content digest from its reviewed ranking run. Set `origin` to
`owner_feedback` for explicit actions and `implicit_telemetry` for implicit
observations. Archive and provider sync history uses the existing PersonalEvent
contract and is outside this feedback stream.

Replay normalizes timestamps to UTC, deduplicates exact events by producer ID,
and sorts by timestamp and then event ID. Conflicting ID reuse blocks the
whole input. Two distinct IDs represent two actions even when their other
values match. Each explicit `more_like_this` or `less_like_this` contributes
one positive or negative count to each distinct topic on its candidate.
The candidate policy adds those signed counts times `affinity_step` to the
starting topic affinities. The existing reference ranker supplies scores,
positions, and explanations for both policies at the same evaluation time.

Use reviewed SHA-256 values for the frozen file and any retained checkpoint.
Run the command from a private workspace on a filesystem with hard-link support.

```sh
python -m personal_algorithm.preference_replay \
  --input "$frozen_input" --input-sha256 "$reviewed_input_sha" \
  --output "$checkpoint" --stop-after 10
python -m personal_algorithm.preference_replay \
  --input "$frozen_input" --input-sha256 "$reviewed_input_sha" \
  --resume "$checkpoint" --resume-sha256 "$retained_checkpoint_sha" \
  --output "$candidate"
```

Choose a stop position within the number of distinct events. A run with no
stop position consumes the full stream. Each output carries its input file
digest and the normalized replay identity. Resume revalidates the retained
prefix before applying the remaining events. A newly captured late event
changes the normalized identity and requires a full replay of the newly
reviewed snapshot.

The command writes complete output to a new path and reports its SHA-256.
Keep the checkpoint and candidate private. The candidate carries derived
preferences and ranking evidence for operator review. Evaluation against
observed outcomes and promotion require separate evidence and approvals.

## Governed source use

Version 2 uses the same feedback and ranking semantics. Set
`schema_version` to `2` and add `source_use_sha256` to bind the exact bytes
of a separately reviewed source-use manifest. Supply both reviewed file
digests from trusted records. Use the same pins when resuming a checkpoint.

```sh
python -m personal_algorithm.preference_replay \
  --input "$frozen_input" --input-sha256 "$reviewed_input_sha" \
  --source-use "$source_manifest" --source-use-sha256 "$reviewed_source_sha" \
  --output "$candidate"
```

The source-use manifest has integer `schema_version: 1`,
`evaluator_implementation`, `caller_id`, and `sources`. Set the evaluator
identity and caller from the separately reviewed policy evaluation.
Each row in `sources` has these fields.

| Field | Value |
| --- | --- |
| `kind` | `candidate` or `interaction`. |
| `source`, `source_id` | The candidate's source-native pair, or the interaction's `origin` and `event_id`. |
| `input_sha256` | The exact ranking candidate snapshot SHA-256 from `ranking_runs.candidate_snapshot`, or the SHA-256 of the normalized interaction. |
| `trust_class`, `data_class` | Reviewed Long-Haul authority class and data classification. Use `public`, `owner-private`, or `restricted` for the latter. |
| `rights_reference`, `fresh_until` | The reviewed access evidence identity and a timezone-aware expiry. |
| `policy_fingerprint` | The reviewed Long-Haul Resource Policy fingerprint. |
| `decisions` | A map of action to its Long-Haul `request` and `decision` objects. |

Hash an interaction after converting `occurred_at` to UTC with an explicit
`+00:00` offset. Encode the seven feedback fields as sorted, compact UTF-8
JSON followed by one LF. Use one row per distinct normalized producer
event. The policy resource ID for each source is the URN
`urn:personal-algorithm:replay:<kind>:sha256:<input_sha256>`.

Produce evidence with the reviewed
[Long-Haul resource policy evaluator][long-haul-policy]. Store the request
and its decision using their JSON model projections. Each source needs an
explicit `evaluate` allowance for `personal-algorithm-preference-replay`
with audience `private`. Use the replay's frozen evaluation instant for
the request and decision. Its UTC instant must precede `fresh_until`.
Optional `index`, `ai-augment`, `ai-training`, and `publish` decisions
remain independent evidence. An omitted decision supplies no permission.

Review current rights and provenance before admitting or replaying real
inputs, including any source decisions that changed since the frozen
evaluation time. The offline command checks the pinned, as-of receipts.
Its output retains the source-use digest without copying private decision
contents. Promotion remains a separate operator action.

[long-haul-policy]: https://github.com/pH34r-pH/long-haul/blob/4d5ae441869d9459ed597294b1090fc3f19141c1/docs/resource-policy.md
