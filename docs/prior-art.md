# Prior-art notes

This is a deliberately short research pass. The goal is to learn from adjacent systems without turning The Personal Algorithm into a clone of any one of them.

## HPI / Human Programming Interface

HPI treats personal digital traces as local-first data that should be available through ordinary programmatic interfaces. Its strongest lessons for us are:

- keep personal data separate from reusable code;
- make adapters independently extensible;
- tolerate heterogeneous source representations rather than forcing everything into one giant canonical database;
- local synchronization can be more resilient than making every read depend on a live cloud API.

**Adopt:** local-first state, modular source adapters, optional historical-data bridge.

**Do not inherit blindly:** The Personal Algorithm's primary abstraction is not "all of my data"; it is the candidate -> rank -> explain -> feedback loop.

## Dogsheep / Datasette

Dogsheep demonstrates the usefulness of importing disparate personal exports into simple queryable SQLite databases.

**Adopt:** SQLite as an excellent default for a single-user deployment; preserve provenance; make data inspectable with ordinary tools.

**Do not inherit blindly:** analytics/search and recommendation are different products. The event store should support the recommender rather than define its architecture.

## IndieWeb readers / Microsub / POSSE

IndieWeb has long explored a personal reader integrated with one's own site, including explicitly filtering and prioritizing posts using algorithms of one's own choosing. POSSE separately establishes a useful "own the center, interoperate with silos" philosophy.

**Adopt:** personal site as the control surface; open feed protocols; clean separation between aggregation and presentation; interoperability over platform replacement.

**Extension here:** make ranking policy, explanation, feedback, and exploration first-class rather than treating filtering/prioritization as a reader feature.

## Existing local personal recommendation projects

There are open-source experiments combining YouTube/RSS/browser/podcast/local-file candidates with embeddings, reranking, heuristics, and bandit-style exploration.

**Adopt:** two-stage retrieval/ranking where scale requires it; model-agnostic interfaces; simple heuristics as credible baselines.

**Differentiate:** low-compute reproducibility, policy transparency, cross-source provenance, user-editable objectives, explicit non-engagement goals, and explanations are core requirements rather than implementation details.

## Recommendation-system libraries

General recommender libraries contain mature implementations of collaborative filtering, ranking, evaluation, and experimentation.

**Adopt selectively:** evaluation methodology and interchangeable ranker contracts.

**Avoid initially:** collaborative filtering assumes a population. Our defining case is one person, where content semantics, explicit objectives, temporal context, and exploration are more important starting signals.

## Resulting design decisions

1. Start with open/prosaic sources so source acquisition does not dominate the experiment.
2. Treat `Candidate`, `RankedCandidate`, `Explanation`, and `Interaction` as public contracts.
3. Keep personal state outside the code repository.
4. Make a deterministic ranker the reference implementation before adding learned ranking.
5. Store score contributions, not just final scores.
6. Preserve source URL and provenance; default consumption happens at the source.
7. Build exploration into policy explicitly.
8. Keep historical personal-data systems pluggable rather than mandatory.
9. Optimize onboarding for a single self-hoster before designing federation.
10. Evaluate success by usefulness, novelty, learning, and user intent—not session length.

## Research links

- HPI: https://github.com/karlicoss/HPI
- Dogsheep: https://github.com/dogsheep
- IndieWeb reader: https://indieweb.org/reader
- IndieWeb POSSE: https://indieweb.org/POSSE
- Microsoft/Recommenders community repository: https://github.com/recommenders-team/recommenders
