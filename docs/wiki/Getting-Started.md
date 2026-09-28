# Getting started

The shortest useful development path uses a local virtual environment, JSON instance configuration, and SQLite.

## Install

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

Copy the example configuration:

```sh
cp config.example.json config.json
```

`config.json` is instance state. Configure only feeds and settings you are permitted to use, and keep credentials and private profile data out of source control.

## Ingest and rank

```sh
personal-algorithm-ingest --config config.json
```

A run acquires configured candidates, maps them into the common Candidate contract, stores them, ranks them under the selected policy, persists explanation traces, and prints the resulting order as JSON.

The one-shot shape is intentional: scheduling is a deployment decision, not hidden application behavior.

## Development application

```sh
uvicorn personal_algorithm.api:app --reload
```

The development API is not a public-Internet security boundary. Do not expose an unauthenticated development instance.

## Where to go next

Read [Contracts and ranking](Contracts-and-Ranking.md) before adding a ranker or changing public data shapes. Read [Sources and providers](Sources-and-Providers.md) before adding authenticated access or historical imports. For deployment-specific identity and secret handling, use the repository's deployment documentation rather than copying development defaults.

Authoritative setup details: [docs/getting-started.md](https://github.com/pH34r-pH/the-personal-algorithm/blob/main/docs/getting-started.md).
