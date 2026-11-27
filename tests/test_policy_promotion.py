import json
import sys
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
import test_replay_outcomes as fixtures

from personal_algorithm import cli, policy_promotion, preference_replay, replay_outcomes
from personal_algorithm.config import load_config
from personal_algorithm.ingest import ingest_ranking_run
from personal_algorithm.ranking_runs import create_ranking_run
from personal_algorithm.serialization import candidate_from_dict
from personal_algorithm.store import Store

frozen = fixtures.frozen
reviewed_sources = fixtures.reviewed_sources
comparison = fixtures.comparison


def _fixture_derive(evaluation, decided, status):
    request = {
        **evaluation["request"], "action": "derive", "purpose": policy_promotion.ACTIVATION_PURPOSE,
        "now": decided, "request_id": "fixture-activation",
    }
    raw = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    decision = {
        **evaluation["decision"], "request_id": request["request_id"], "evaluated_at": decided,
        "request_fingerprint": "resource-use-v1:" + preference_replay._digest(raw), "status": status,
        "matched_permissions": ["private-ranking"] if status == "ALLOW" else [],
        "matched_prohibitions": ["private-ranking-denied"] if status == "DENY" else [],
    }
    return {"request": request, "decision": decision}


@pytest.fixture
def release(comparison, frozen, tmp_path):
    files = dict(zip(fixtures.FLAGS, fixtures.inputs(comparison, tmp_path), strict=True))
    config_path, database = tmp_path / "config.json", tmp_path / "rankings.sqlite3"
    config = fixtures.pin(config_path, preference_replay._json({
        "feeds": ["https://example.invalid/feed.xml"], "database": str(database),
        "policy": frozen["starting_policy"],
    }))
    report = replay_outcomes.compare_outcomes(
        *(files[key] for key in policy_promotion.COMPARISON_FILES), forecasts=files["forecasts"],
    )
    files["comparison"] = fixtures.pin(tmp_path / "comparison.json", preference_replay._json(report))
    decoded = files["candidate"].read()
    now = max(datetime.now(UTC), datetime.fromisoformat(fixtures.THROUGH)) + timedelta(seconds=1)
    expiry = (now + timedelta(days=1)).isoformat()
    approval = {
        "schema_version": 1, "action": "promote", "operator_id": fixtures.CALLER,
        "decided_at": fixtures.THROUGH, "instance_config_sha256": config.sha256, "database": str(database.resolve()),
        "previous_approval_sha256": None, "previous_policy_sha256": decoded["baseline_ranking"]["policy_sha256"],
        "policy_sha256": decoded["counterfactual_ranking"]["policy_sha256"],
        "candidate_sha256": files["candidate"].sha256, "comparison_sha256": files["comparison"].sha256,
    }
    review = files["source-use"].read()
    for row in review["sources"]:
        row["fresh_until"] = expiry
        row["decisions"] = {"derive": _fixture_derive(row["decisions"]["evaluate"], fixtures.THROUGH, "ALLOW")}
    files["activation-source-use"] = fixtures.pin(tmp_path / "activation.json", preference_replay._json(review))
    approval["activation_source_use_sha256"] = files["activation-source-use"].sha256
    return config_path, approval, files, now


def _approve(path, approval):
    return fixtures.pin(path, preference_replay._json(approval))


def _cli_args(config, action, approval, files):
    args = ["policy_promotion", "--config", str(config), action, "--approval", str(approval.path), approval.sha256]
    for name, pinned in files.items():
        args.extend((f"--{name}", str(pinned.path), pinned.sha256))
    return args


def _rollback(approval, promotion_sha):
    return {
        **{key: approval[key] for key in policy_promotion.COMMON},
        "action": "rollback", "previous_approval_sha256": promotion_sha, "restore_approval_sha256": None,
        "previous_policy_sha256": approval["policy_sha256"], "policy_sha256": approval["previous_policy_sha256"],
    }


def test_owner_cli_promotes_for_ingestion_and_rolls_back_exact_previous_policy(release, frozen, tmp_path,
                                                                          monkeypatch, capsys):
    config, approval, files, _ = release
    selected = load_config(config)
    candidates = tuple(map(candidate_from_dict, frozen["candidates"]))
    monkeypatch.setattr(cli, "ingest_ranking_run", partial(ingest_ranking_run, fetcher=lambda _: candidates))
    assert [row["candidate_id"] for row in cli.run(str(config))] == ["b", "a"]
    pinned = _approve(tmp_path / "approve.json", approval)
    monkeypatch.setattr(sys, "argv", _cli_args(config, "promote", pinned, files))
    assert policy_promotion.main() == 0
    assert json.loads(capsys.readouterr().out)["result"] == "recorded"
    assert policy_promotion.main() == 0
    assert json.loads(capsys.readouterr().out)["result"] == "already-recorded"
    assert [row["candidate_id"] for row in cli.run(str(config))] == ["a", "b"]
    rollback = _approve(tmp_path / "rollback.json", _rollback(approval, pinned.sha256))
    monkeypatch.setattr(sys, "argv", _cli_args(config, "rollback", rollback, {}))
    assert policy_promotion.main() == 0
    assert [row["candidate_id"] for row in cli.run(str(config))] == ["b", "a"]
    store = Store(selected.database)
    try:
        assert store.active_ranking_policy(selected.policy) == selected.policy
        changes = store.connection.execute(
            "SELECT action, approval_sha256 FROM ranking_policy_changes ORDER BY sequence",
        ).fetchall()
        assert changes == [("promote", pinned.sha256), ("rollback", rollback.sha256)]
        assert store.connection.execute("SELECT count(*) FROM ranking_runs").fetchone()[0] == 3
    finally:
        store.connection.close()


def test_derived_use_denial_and_wrong_instance_leave_policy_unchanged(release, tmp_path):
    config, approval, files, now = release
    selected = load_config(config)
    Store(selected.database).connection.close()
    review = files["activation-source-use"].read()
    alternative = {**review["sources"][0]["decisions"]}
    review["sources"][0]["decisions"] = {"evaluate": alternative["derive"]}
    files["activation-source-use"] = fixtures.pin(
        files["activation-source-use"].path, preference_replay._json(review),
    )
    approval["activation_source_use_sha256"] = files["activation-source-use"].sha256
    with pytest.raises(ValueError, match="independent derive decision"):
        pinned = _approve(tmp_path / "approve.json", approval)
        policy_promotion._change(config, "promote", pinned, files, now)
    review["sources"][0]["decisions"] = alternative
    alternative["derive"]["decision"]["status"] = "DENY"
    files["activation-source-use"] = fixtures.pin(
        files["activation-source-use"].path, preference_replay._json(review),
    )
    with pytest.raises(ValueError, match="identify the local database"):
        pinned = _approve(tmp_path / "approve.json", {**approval, "database": str(tmp_path / "other.sqlite3")})
        policy_promotion._change(config, "promote", pinned, files, now)
    approval["activation_source_use_sha256"] = files["activation-source-use"].sha256
    with pytest.raises(ValueError, match="explicitly allow"):
        pinned = _approve(tmp_path / "approve.json", approval)
        policy_promotion._change(config, "promote", pinned, files, now)
    store = Store(selected.database)
    try:
        assert store.ranking_policy_status(selected.policy)["approval_sha256"] is None
        assert store.connection.execute("SELECT count(*) FROM ranking_inputs").fetchone()[0] == 0
    finally:
        store.connection.close()


def test_repinning_a_changed_comparison_cannot_authorize_promotion(release, tmp_path):
    config, approval, files, now = release
    path = files["comparison"].path
    report = files["comparison"].read()
    report["judged_candidates"] = 250
    files["comparison"] = fixtures.pin(path, preference_replay._json(report))
    approval["comparison_sha256"] = files["comparison"].sha256
    pinned = _approve(tmp_path / "approve.json", approval)
    with pytest.raises(ValueError, match="exact reproduced comparison"):
        policy_promotion._change(config, "promote", pinned, files, now)
    assert not (tmp_path / "rankings.sqlite3").exists()


def test_stale_approval_and_in_flight_ranking_cannot_replace_active_policy(release, frozen, tmp_path):
    config, approval, files, now = release
    selected = load_config(config)
    old_run = create_ranking_run(map(candidate_from_dict, frozen["candidates"]), selected.policy)
    pinned = _approve(tmp_path / "approve.json", approval)
    stale = fixtures.pin(tmp_path / "second-approval.json", pinned.path.read_bytes() + b"\n")
    policy_promotion._change(config, "promote", pinned, files, now)
    with pytest.raises(ValueError, match="starting policy is no longer active"):
        policy_promotion._change(config, "promote", stale, files, now)
    store = Store(selected.database)
    try:
        with pytest.raises(ValueError, match="before the run was stored"):
            store.put_ranking_run(old_run, configured_policy=selected.policy)
        assert store.connection.execute("SELECT count(*) FROM ranking_runs").fetchone()[0] == 0
        assert store.ranking_policy_status(selected.policy)["approval_sha256"] == pinned.sha256
    finally:
        store.connection.close()


def test_ingestion_rejects_policy_changed_during_acquisition(release, frozen, tmp_path, monkeypatch):
    config, approval, files, now = release
    promotion = _approve(tmp_path / "approve.json", approval)
    policy_promotion._change(config, "promote", promotion, files, now)
    rollback = _approve(tmp_path / "rollback.json", _rollback(approval, promotion.sha256))

    def fetcher(_):
        policy_promotion._change(config, "rollback", rollback, {}, now)
        return tuple(map(candidate_from_dict, frozen["candidates"]))

    monkeypatch.setattr(cli, "ingest_ranking_run", partial(ingest_ranking_run, fetcher=fetcher))
    with pytest.raises(ValueError, match="active ranking policy changed before the run was stored"):
        cli.run(str(config))
    store = Store(load_config(config).database)
    try:
        assert store.connection.execute("SELECT count(*) FROM ranking_runs").fetchone()[0] == 0
    finally:
        store.connection.close()


def test_atomic_write_failure_then_expired_policy_can_be_recovered(release, tmp_path, monkeypatch):
    config, approval, files, now = release
    selected = load_config(config)
    pinned = _approve(tmp_path / "approve.json", approval)
    original = Store._insert_ranking_input

    def interrupt(connection, snapshot):
        original(connection, snapshot)
        if snapshot.sha256 == approval["policy_sha256"]:
            raise OSError("synthetic write interruption")

    with monkeypatch.context() as override:
        override.setattr(Store, "_insert_ranking_input", staticmethod(interrupt))
        with pytest.raises(OSError, match="interruption"):
            policy_promotion._change(config, "promote", pinned, files, now)
    store = Store(selected.database)
    try:
        assert store.connection.execute("SELECT count(*) FROM ranking_inputs").fetchone()[0] == 0
        assert store.ranking_policy_status(selected.policy)["approval_sha256"] is None
        policy_promotion._change(config, "promote", pinned, files, now)
        expiry = datetime.fromisoformat(store.ranking_policy_status(selected.policy)["valid_until"])
        with pytest.raises(ValueError, match="has expired"):
            store.active_ranking_policy(selected.policy, now=expiry)
        rollback = _approve(tmp_path / "rollback.json", _rollback(approval, pinned.sha256))
        policy_promotion._change(config, "rollback", rollback, {}, expiry)
        assert store.active_ranking_policy(selected.policy, now=expiry) == selected.policy
    finally:
        store.connection.close()
