# GitHub provider

GitHub is the first authenticated provider implementation.

## Connection boundary

The current code implements **connection finalization**, not yet the browser redirect/callback itself:

1. the web layer obtains an authorized GitHub token;
2. the token is validated against `GET /user`;
3. the stable GitHub user ID becomes the credential-store account key;
4. the token is stored behind the configured CredentialStore;
5. the returned Connection contains only an opaque credential reference.

With Azure Key Vault this becomes an `akv://...` reference.

## Bootstrap

The first finite bootstrap importer walks the connected account's `/user/repos` view in explicit pages of up to 100 repositories. Each call processes one page and returns a checkpoint cursor.

The bootstrap coordinator—not the provider—decides whether/when to request the next page. This keeps long imports resumable and makes progress visible.

Repository records can then become Candidates with:

- canonical GitHub URL;
- repository name and description;
- topics;
- language;
- stars;
- fork/private flags;
- push/update time;
- endpoint and repository-ID provenance.

Private-repository metadata is personal instance data and must never be included in public exports by default.

## Authorization direction

The browser authorization implementation should use a GitHub App rather than asking users to paste personal access tokens. The application should request only permissions required for enabled capabilities and keep connection permissions visible in the Connections UI.

The access-token argument in `connect_github` is an internal callback boundary, not intended as the user-facing onboarding mechanism.
