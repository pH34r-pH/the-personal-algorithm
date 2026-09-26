# Data onboarding strategy

The immediate objective is **maximum useful personal history with minimum setup friction**. Ranking and policy work should wait until the instance has enough real owner data to exercise.

## Friction budget

| Class | Owner action | Preferred use |
| --- | --- | --- |
| zero-login | upload a local export/history file | browser/device/local data |
| one-connect | one OAuth grant | current account state + future sync |
| one-export | one provider export request | deep historical bootstrap |
| multi-step | multiple grants or manual operations | only when materially more data is available |

A provider having an API is not sufficient reason to build OAuth. Prefer the archive when it contains much more history.

## Initial order

1. Google / YouTube: one read-only Google connection for current YouTube state plus one broad Google Takeout for deep history.
2. Spotify: Extended Streaming History archive before live API work.
3. GitHub: one-click connection and immediate finite repository bootstrap.
4. X and LinkedIn: rich requested archives.
5. Meta Accounts Center and Reddit: requested archives.
6. Browser/local history: zero-login uploads.

## Storage boundary

Raw provider archives are not recommendation candidates and are not copied into the SQLite snapshot. They are retained content-addressed in the archive inbox. SQLite stores only archive metadata, resumable import state, normalized PersonalEvent records, and the recommendation/interaction state that already belongs there.

## PersonalEvent

Historical facts use a neutral event contract rather than Candidate:

- stable event ID;
- source/provider;
- source-native event type;
- timestamp when available;
- optional subject and canonical URL;
- structured payload;
- provenance identifying archive hash/member/row or API source.

Provider-specific parsers should preserve source detail first. Later algorithm passes may derive affinities, candidates, or features from PersonalEvent without requiring the owner to re-export data.
