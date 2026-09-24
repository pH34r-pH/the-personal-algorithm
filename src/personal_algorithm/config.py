"""Small JSON configuration boundary for self-hosted instances."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import Policy
from .serialization import ValidationError, policy_from_dict
from .source_config import SourceConfig, source_from_dict


@dataclass(frozen=True, slots=True)
class InstanceConfig:
    sources: tuple[SourceConfig, ...]
    policy: Policy
    database: str = "data/personal-algorithm.sqlite3"


def load_config(path: str | Path) -> InstanceConfig:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read configuration: {exc}") from exc

    raw_sources = data.get("sources")
    if raw_sources is None and "feeds" in data:
        # Backward-compatible M1 shorthand.
        raw_sources = [{"type": "rss", "url": url} for url in data["feeds"]]
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValidationError("sources must be a non-empty list")
    try:
        sources = tuple(source_from_dict(item) for item in raw_sources)
    except (TypeError, AttributeError) as exc:
        raise ValidationError("each source must be an object") from exc

    database = data.get("database", "data/personal-algorithm.sqlite3")
    if not isinstance(database, str) or not database:
        raise ValidationError("database must be a non-empty path")

    return InstanceConfig(
        sources=sources,
        policy=policy_from_dict(data.get("policy", {})),
        database=database,
    )
