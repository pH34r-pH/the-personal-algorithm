# Contributing

The project is intentionally contract-first while the architecture is young.

## Before adding a source

A source adapter should:

- use an official API, open feed/protocol, user-provided export, or otherwise permitted access path;
- emit the shared Candidate contract;
- preserve canonical source URLs and provenance;
- document authentication, rate limits, retention constraints, and relevant source-specific restrictions;
- avoid requiring bypasses of authentication, DRM, anti-bot controls, or rate limits.

## Before adding a ranker

A ranker should document:

- inputs;
- objective;
- compute requirements;
- whether it calls a remote service;
- deterministic/non-deterministic behavior;
- explanation support;
- evaluation method.

The reference path should remain usable without a GPU or paid model API.

## Personal data

Fixtures and tests must use synthetic or explicitly redistributable data. Never commit real user histories, credentials, tokens, private embeddings, or platform exports.
