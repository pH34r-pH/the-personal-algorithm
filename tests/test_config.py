import json

import pytest

from personal_algorithm.config import load_config
from personal_algorithm.serialization import ValidationError


def test_example_shaped_config_loads(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "database": "data/test.sqlite3",
                "feeds": ["https://example.invalid/feed.xml"],
                "policy": {
                    "version": "test-v1",
                    "weights": {"recency": 1},
                    "topic_affinity": {},
                },
            }
        )
    )

    config = load_config(path)
    assert config.feeds == ("https://example.invalid/feed.xml",)
    assert config.policy.weights == {"recency": 1.0}
    assert config.database == "data/test.sqlite3"


@pytest.mark.parametrize(
    "feeds",
    [[], ["file:///tmp/feed.xml"], ["javascript:bad"], "https://example.invalid/feed.xml"],
)
def test_config_rejects_invalid_feed_lists(tmp_path, feeds):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "feeds": feeds,
                "policy": {"version": "test-v1", "weights": {}},
            }
        )
    )
    with pytest.raises(ValidationError):
        load_config(path)
