# Source adapters

A source adapter's job is intentionally narrow: acquire or parse source-native material and emit the shared `Candidate` contract with provenance.

## RSS / Atom

RSS and Atom are the first M1 source because they are open publishing protocols, broadly deployed, low-friction for self-hosters, and do not require a platform account.

The adapter:

- supports RSS and Atom through one contract;
- generates stable local IDs from feed URL + source-native entry ID;
- preserves the canonical entry URL;
- maps categories/tags into candidate topics;
- preserves feed URL and detected format as provenance;
- separates parsing from network fetching so tests are hermetic;
- follows ordinary HTTP redirects and fails closed on HTTP/parser errors.

The adapter does not mirror linked page contents. It ranks feed metadata and points consumption back to the canonical source.

Future source adapters should preserve this boundary wherever practical.
