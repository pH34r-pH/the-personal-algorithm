# Spotify history onboarding

Spotify is intentionally **archive-first** for bootstrap.

The preferred owner flow is:

1. open Spotify Account Privacy;
2. request **Extended Streaming History**;
3. upload the returned ZIP through `/app/onboarding`;
4. the Personal Algorithm detects Spotify and imports the history automatically.

No Spotify OAuth grant is required for the historical bootstrap.

Spotify documents Extended Streaming History as lifetime account history and includes playback timestamp, platform, milliseconds played, country, IP/user-agent fields, track/episode metadata and URI, start/end reason, shuffle, skip, offline and private-session flags. The importer preserves the complete source row in `PersonalEvent.payload` rather than reducing it to a taste summary.

Reference:
- https://support.spotify.com/article/understanding-your-data/
- https://support.spotify.com/article/data-rights-and-privacy-settings/

## Import semantics

Each source row becomes one `PersonalEvent`:

- `track_stream`
- `podcast_stream`
- `audiobook_stream`
- `unknown_stream` when Spotify does not provide enough item metadata

The event ID includes the archive hash, member path, row index and full row payload. This makes re-importing the same archive idempotent without collapsing distinct rows that happen to contain identical playback data.

Raw archives remain retained after import. Provider-specific parser changes can therefore be replayed without requesting the export again.

Supported JSON arrays are read one row at a time and staged in a private
operating-system temporary directory. A complete member is validated before its
events are applied in one database transaction; previously completed members
remain committed if a later member fails. Temporary staging is removed on
normal completion or handled errors. An abrupt process termination can leave
temporary files until the operating system or hosting environment removes them,
so temporary storage must be protected like the source archive. Non-array JSON
still uses the ordinary whole-file decoder and is not covered by the streaming
memory bound.
