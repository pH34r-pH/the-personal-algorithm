"""Small JSON configuration boundary for self-hosted instances."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import Policy
from .serialization import ValidationError, policy_from_dict


@dataclass(frozen=True, slots=True)
class InstanceConfig:
    feeds: tuple[str, ...]
    policy: Policy
    database: str = "data/personal-algorithm.sqlite3"


def load_config(path: str | Path) -> InstanceConfig:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read configuration: {exc}") from exc

    feeds = data.get("feeds")
    if not isinstance(feeds, list) or not feeds or not all(
        isinstance(url, str) and url.startswith(("http://", "https://")) for url in feeds
    ):
        raise ValidationError("feeds must be a non-empty list of HTTP(S) URLs")

    database = data.get("database", "data/personal-algorithm.sqlite3")
    if not isinstance(database, str) or not database:
        raise ValidationError("database must be a non-empty path")

    return InstanceConfig(
        feeds=tuple(feeds),
        policy=policy_from_dict(data.get("policy", {})),
        database=database,
    )
