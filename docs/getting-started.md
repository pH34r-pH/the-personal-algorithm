# Getting started with real feeds

M1's first self-hosted path is intentionally plain: a JSON configuration, SQLite, and a one-shot ingestion command.

## Install

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

## Configure

```sh
cp config.example.json config.json
```

Edit `config.json` with feeds you are permitted to retrieve and the initial policy you want to inspect.

`config.json` is instance state and should not be committed. The repository contains only the example.

## Ingest

```sh
personal-algorithm-ingest --config config.json
```

The command:

1. fetches configured RSS/Atom feeds;
2. maps entries into Candidates;
3. persists them in the configured SQLite database;
4. ranks them under the configured versioned policy;
5. persists explanation traces;
6. prints the ordered rankings as JSON.

This is a one-shot operation. Scheduling is deliberately deferred so the acquisition cadence remains an explicit deployment choice rather than hidden behavior.

## Development API

The existing local API remains available:

```sh
uvicorn personal_algorithm.api:app --reload
```

Do not expose the unauthenticated development API to the public Internet.
