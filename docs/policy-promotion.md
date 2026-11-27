# Local ranking policy promotion and rollback

The local ingestion command uses its configured policy until the operator
records a reviewed transition. It then reads the active policy from the
configured SQLite database. Existing ranking runs retain the policy
snapshot they used. Promotion changes later ingestion runs.

Check the current policy and approval digest from the instance's working
directory.

```sh
python -m personal_algorithm.policy_promotion --config "$config" status
```

## Approve a candidate

Start with a [governed replay candidate and observed outcome
comparison](preference-replay.md#compare-later-observed-outcomes). Verify
the ranking evidence and explanations, the coverage of observed outcomes,
and the source policies for the proposed local use. Obtain the exact input
and decision digests from the independent review.

Use a separate source-use manifest covering every replay candidate and
feedback event. Keep the exact source keys and input digests from the
replay review. Obtain Long-Haul `derive` decisions for the purpose
`personal-algorithm-private-ranking-policy` and audience `private`
at the operator's decision time. The reviewed caller must be the approving
operator. The earliest source-review deadline sets the active policy
expiry. Review withdrawals when making this decision and roll back if a
source permission changes.

Freeze an operator approval with these common fields.

- `schema_version` is integer `1` and `action` is `promote`.
- `operator_id` and timezone-aware `decided_at` identify the reviewed
  operator decision. Decide after the evidence observation window.
- `instance_config_sha256` is the digest of the exact instance config file.
  `database` is the resolved absolute path to its local SQLite database.
- `previous_approval_sha256` and `previous_policy_sha256` match
  the current status record. Before the first approval, use null for
  the previous approval.
- `policy_sha256` is the replay's counterfactual ranking policy digest.
- `candidate_sha256`, `comparison_sha256`, and
  `activation_source_use_sha256` bind the exact independently reviewed
  files for this decision.

```sh
python -m personal_algorithm.policy_promotion --config "$config" promote \
  --approval "$approval" "$approval_sha" --input "$frozen_input" "$input_sha" \
  --source-use "$source_manifest" "$source_sha" --candidate "$candidate" "$candidate_sha" \
  --outcomes "$outcomes" "$outcomes_sha" --outcome-source-use "$outcome_sources" "$outcome_sources_sha" \
  --comparison "$comparison" "$comparison_sha" --activation-source-use "$activation" "$activation_sha"
```

Add `--forecasts FILE SHA256` if the reviewed comparison used forecasts.
The command reproduces the comparison and candidate, checks the exact
activation permissions, and appends the transition in one database
transaction. If the active state changed after review, obtain a new
approval. A repeated approved command keeps one transition. An expired
policy stops subsequent ingestion until a current reviewed policy is
activated or the operator rolls back.

## Roll back

Freeze a new approval with `action: rollback`, the same common identity
and configuration fields, the current status as the previous state, and
the intended prior `policy_sha256`. Omit the three promotion evidence fields.
Use `restore_approval_sha256` to identify an earlier retained transition.
Set it to null to restore the original policy snapshot recorded by the first
promotion. A restored policy retains its original validity window. Review
current source rights before restoring a prior derived policy.

```sh
python -m personal_algorithm.policy_promotion --config "$config" rollback \
  --approval "$rollback" "$rollback_sha"
```

The receipt identifies the approval and resulting policy by digest. Keep
the exact approved files and transition history in private storage. Repeat
the status check and verify a subsequent local ingestion run to observe
the restored policy. Hosted deployment qualification remains a separate
operator step.
