# Documentation boundary

`docs/` is the explanatory source for the application's contracts and operational
boundaries. `docs/wiki/` is the canonical source synchronized to the GitHub Wiki by the
existing workflow; it is not a second runtime.

- Update architecture and data-flow claims in [`architecture.md`](architecture.md),
  [`application.md`](application.md), and [`contracts.md`](contracts.md).
- Update source/provider behavior in [`sources.md`](sources.md), [`providers.md`](providers.md),
  and [`ingestion.md`](ingestion.md).
- Keep [`instance-auth.md`](instance-auth.md), [`github-auth.md`](github-auth.md), and
  [`azure-key-vault.md`](azure-key-vault.md) aligned with the actual trust boundaries.
- `roadmap.md`, `m0.md`, and `prior-art.md` retain milestone/history context. Do not
  erase an old plan when the implementation changes; add a scoped status/evidence note.
- Links within this repository must be relative. External deployment or provider claims
  require a source or explicit current code/config evidence.

Validate from the repository root with `git diff --check`, the focused test commands in
[`../AGENTS.md`](../AGENTS.md), and the repository documentation workflow.
