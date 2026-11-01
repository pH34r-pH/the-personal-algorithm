# Platform discovery and user-owned ranking

Status: research and proposed design; the selected Takeout parsers and on-demand
YouTube discovery prototype are implemented locally but not verified in a hosted
deployment. Provider behavior,
availability, API access, and policies must be checked again before implementation.

## Goal and boundaries

The owner decides what to discover and how to rank it. Third-party recommendation
systems are optional discovery channels whose responses can be observed and influenced;
their predictions are not the product's objective. The private feed should retain the
minimum permitted references and provenance required to rank and reopen an item.
Provider-hosted players and ordinary links are preferred over copying third-party media.
An embed or WebView does not by itself establish permission, availability, or a right
to retrieve a provider's personalized feed. Some providers prohibit framing or require
specific players; fall back to opening the canonical link.

## Jobs by source

| Source | Owner's intended job | Candidate path | Explicit limitation |
| --- | --- | --- | --- |
| Facebook | Local events and friends' birthdays. | Investigate owner-accessible event and birthday surfaces and the contents of a selective personal export; link back to source. | Availability and reuse rights for private friends' data are unverified. Do not treat general feed popularity as event relevance. |
| TikTok | Early discovery of breaking news and trends. | User-selected links, approved embeds, and optional selected history or exports; record discovery time and uncertainty. | The Display API covers an authorized creator's own public videos, not the personalized For You feed. Portability API coverage is limited to users in the EEA and UK. |
| X / Twitter | A second source of breaking news and trends. | User-chosen accounts or lists; approved links and public embeds where available. | Account access, API eligibility, cost and terms are unverified for this instance. |
| YouTube | Followed creators, longer-form context, and related interests. | Import the owner's subscriptions and playlists, then investigate live discovery and official player integration. | OAuth and API credentials are not evidence of a deployed source. Policy restrictions on derived and combined API data require review. |

The owner's downloaded Google export contains a subscriptions CSV, playlist CSVs,
and HTML watch and search history. The Google importer supports selected HTML activity
and subscription CSV alongside JSON activity formats. Import only the selected
personal signals and references; skip media members. Keep the raw archive outside the
repository and make ingestion an explicit owner action.

## Model provider response as a black box

Treat the provider as an evolving, partially observed system, not as an identified
formula. Document each hypothesis, source of evidence, confidence and date. Separate:

1. Owner objective: for example, timely local events or reliable early news.
2. Observable outcome: the items visible in a defined surface and observation window.
3. Owner action: a real, intentional watch, follow, skip, like, or explicit preference.
4. Proposed provider response: a testable, uncertain change in later observations.
5. Owner value: useful, irrelevant, repetitive, misleading, or already known.

TikTok describes watch time, skips, likes, follows and other signals as factors, and
YouTube identifies watch history, subscriptions, likes and explicit negative feedback.
Neither disclosure establishes stable weights or a causal effect for this account.
[TikTok recommendations](https://support.tiktok.com/en/using-tiktok/exploring-videos/how-tiktok-recommends-content)
and [YouTube recommendations](https://support.google.com/youtube/answer/16089387?hl=en)
are starting evidence, not claims about hidden implementation.

### First experiments

- Record a small, owner-reviewed baseline of visible items at consistent times. Capture
  only minimal permitted metadata and item links, not media, cookies, credentials or
  unrelated private messages. Provider access and terms must be checked first.
- Pick one objective and one genuine user action at a time. For example, compare the
  fraction of personally judged useful local-event items before and after the owner
  changes an available preference. Record the surface, time, action and context.
- Keep an untreated comparison window when feasible; record concurrent events such as
  breaking news, account changes, time of day and normal viewing. Avoid claims of
  causality from a single account or a shifting news cycle.
- Review differences in useful-item rate, novelty, source diversity, repetition and
  time to useful discovery. For news, track independent corroboration and corrections;
  speed of appearance is not evidence of accuracy.
- Repeat small experiments, state uncertainty, and revert a preference when the owner
  wants to. Do not automate fake engagement, impersonation, account actions or
  undeclared collection. A local feedback action must not silently become an external
  like, follow, comment, repost or message.

## Candidate, event and decision boundaries

- Candidate references: provider, canonical URL or native ID, optional title/author,
  publication and observation timestamps, job, access method and provenance. A
  birthday or private event needs a separate sensitive record and should not be
  inferred from third-party profile data without an explicit authorized source.
- Local feedback: an explicit judgment, its job and timestamp; keep incidental watch
  behavior separate from explicit preferences. The owner controls retention and deletion.
- Platform observations: surface, sample window, measurement version, counts and
  limitations. Default to aggregates and short retention, with raw references optional.
- Platform actions: describe the proposed action and expected provider effect; make
  user initiation and external effects visible. No background mutation of accounts.
- Ranking: job-specific, inspectable policy with recency, locality, source affinity,
  novelty and diversity as configurable features. Distinguish an item relevant to a
  local event from a news claim requiring corroboration. Follow provider restrictions
  before combining or calculating scores from provider API data.

## Delivery sequence

1. Validate and deploy the bounded YouTube subscriptions CSV and HTML history parsers.
   Select only references and personal signals. Verify with synthetic fixtures; never
   put the owner's data in tests or commits.
2. Validate and deploy the on-demand YouTube discovery prototype for one selected
   channel at a time. Review API policy, including derived metrics, provenance, refresh
   and deletion requirements before introducing persistent or ranked API results.
3. Design separate local-events and birthday jobs and identify consent-preserving
   sources before importing other people's information. Test owner-controlled discovery
   and explicit feedback without modifying Facebook.
4. Build a news discovery lane with source labels, timestamp, independent confirmation
   and correction tracking. Evaluate TikTok and X link-based entry points separately.
5. Add an experiment ledger and opt-in observations before considering any provider
   feedback assistance. User review precedes any external account action.

## Source references

- [TikTok Display API](https://developers.tiktok.com/docs/en/display-api-overview)
  and [video embeds](https://developers.tiktok.com/docs/en/embed-videos).
- [TikTok Data Portability API eligibility](https://developers.tiktok.com/products/data-portability-api/).
- [X Lists](https://help.x.com/en/using-x/x-lists) and
  [public embedded timelines](https://help.x.com/en/using-x/embed-x-feed).
- [YouTube subscriptions](https://developers.google.com/youtube/data/guides/implementation/subscriptions),
  [player parameters](https://developers.google.com/youtube/player_parameters), and
  [developer policies](https://developers.google.com/youtube/terms/developer-policies).
