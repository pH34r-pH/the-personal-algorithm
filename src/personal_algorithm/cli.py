"""Command-line entry point for a self-hosted feed ingestion run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .ingest import ingest_ranking_run
from .ranking_runs import ranking_run_to_dict
from .store import Store


def run(config_path: str) -> list[dict]:
    config = load_config(config_path)
    database = Path(config.database)
    database.parent.mkdir(parents=True, exist_ok=True)
    store = Store(database)
    try:
        run = ingest_ranking_run(config.sources, policy=config.policy, store=store)
        if run is None:
            return []
        return [{**item, "run_id": run.id} for item in ranking_run_to_dict(run)["candidates"]]
    finally:
        store.connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest and rank configured personal feeds.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="instance JSON configuration (default: config.json)",
    )
    args = parser.parse_args()
    print(json.dumps(run(args.config), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
