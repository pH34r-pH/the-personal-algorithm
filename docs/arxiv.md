# arXiv source

arXiv is the second M1 source and deliberately reuses the open Atom ingestion path.

The adapter adds only what is arXiv-specific:

- bounded construction of public API queries;
- `arxiv:<paper-version-id>` candidate identity;
- `paper` content type;
- arXiv query provenance;
- arXiv as the source namespace.

The underlying Atom parser still owns title, canonical URL, timestamps, summary, author text, and categories. This is intentional: source adapters should compose shared protocol machinery instead of creating parallel ingestion stacks.

## Query examples

arXiv's API accepts expressions such as:

```text
all:recommendation
cat:cs.IR AND all:personalization
au:"author name"
```

A deployment should keep queries in its private instance configuration rather than hard-code one person's research interests into the public repository.

The adapter caps a single request at 100 candidates. Pagination/cadence belong to later acquisition policy rather than being hidden inside the adapter.
