# Offline preference replay

Use a private, reviewed snapshot for the offline reference replay. The command
reads local files and writes a new local checkpoint or candidate. Arrange a
source-use review for any real inputs before freezing them.

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
