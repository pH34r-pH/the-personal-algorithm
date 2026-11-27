import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import personal_algorithm.store as store_module
from personal_algorithm.models import Candidate, PersonalEvent, Policy
from personal_algorithm.ranking_runs import create_ranking_run, ranking_run_to_dict


class LockedThenReady:
    def __init__(self) -> None:
        self.calls = 0
        self.busy_timeout = None

    def execute(self, sql: str):
        self.busy_timeout = sql
        return self

    def executescript(self, _sql: str):
        self.calls += 1
        if self.calls < 3:
            raise sqlite3.OperationalError("database is locked")
        return self


def test_initialize_schema_retries_transient_lock(monkeypatch):
    connection = LockedThenReady()
    sleeps = []
    monkeypatch.setattr(store_module.time, "sleep", sleeps.append)

    store_module._initialize_schema(connection, attempts=3)

    assert connection.calls == 3
    assert connection.busy_timeout == "PRAGMA busy_timeout = 30000"
    assert sleeps == [0.25, 0.5]


def test_initialize_schema_does_not_hide_other_sqlite_errors(monkeypatch):
    class Broken(LockedThenReady):
        def executescript(self, _sql: str):
            raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store_module.time, "sleep", lambda _: None)

    try:
        store_module._initialize_schema(Broken(), attempts=3)
    except sqlite3.OperationalError as exc:
        assert "disk I/O error" in str(exc)
    else:
        raise AssertionError("expected OperationalError")


def test_commit_writes_atomic_snapshot_and_restore_uses_it(tmp_path):
    live = tmp_path / "live.sqlite3"
    snapshot = tmp_path / "persistent" / "state.sqlite3"
    first = store_module.Store(live, snapshot_path=snapshot)
    first.connection.execute("CREATE TABLE durable_probe (value TEXT NOT NULL)")
    first.connection.execute("INSERT INTO durable_probe(value) VALUES ('kept')")
    first.connection.commit()

    assert snapshot.exists()

    restored = store_module.Store(tmp_path / "restored.sqlite3", snapshot_path=snapshot)
    row = restored.connection.execute("SELECT value FROM durable_probe").fetchone()
    assert row == ("kept",)



def test_personal_events_are_upserted_without_becoming_candidates():
    store = store_module.Store()
    event = PersonalEvent(
        id="spotify:stream:1",
        source="spotify",
        event_type="stream",
        occurred_at=datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
        subject="Example Track",
        canonical_url="spotify:track:example",
        payload={"ms_played": 12345},
        provenance={"archive_sha256": "abc", "member": "history.json", "index": 1},
    )

    store.put_personal_event(event)
    store.put_personal_event(event)

    assert store.count_personal_events() == 1
    assert store.connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    row = store.connection.execute(
        "SELECT source, event_type, subject FROM personal_events WHERE id = ?",
        (event.id,),
    ).fetchone()
    assert row == ("spotify", "stream", "Example Track")


def test_restore_never_overwrites_divergent_local_database(tmp_path):
    live = tmp_path / "live.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    with sqlite3.connect(live) as connection:
        connection.execute("CREATE TABLE probe (value TEXT)")
        connection.execute("INSERT INTO probe VALUES ('local')")
    with sqlite3.connect(snapshot) as connection:
        connection.execute("CREATE TABLE probe (value TEXT)")
        connection.execute("INSERT INTO probe VALUES ('snapshot')")

    with pytest.raises(RuntimeError, match="recovery required"):
        store_module.Store(live, snapshot_path=snapshot)
    with sqlite3.connect(live) as connection:
        assert connection.execute("SELECT value FROM probe").fetchone() == ("local",)
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT value FROM probe").fetchone() == ("snapshot",)


def test_restore_accepts_logically_equal_existing_database(tmp_path):
    live = tmp_path / "live.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    first = store_module.Store(live, snapshot_path=snapshot)
    first.connection.execute("CREATE TABLE probe (value TEXT)")
    first.connection.execute("INSERT INTO probe VALUES ('kept')")
    first.connection.commit()
    first.connection.close()

    restored = store_module.Store(live, snapshot_path=snapshot)
    assert restored.connection.execute("SELECT value FROM probe").fetchone() == ("kept",)
    restored.connection.close()


def test_restore_refuses_corrupt_snapshot_without_overwriting_local(tmp_path):
    live = tmp_path / "live.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    with sqlite3.connect(live) as connection:
        connection.execute("CREATE TABLE probe (value TEXT)")
        connection.execute("INSERT INTO probe VALUES ('kept')")
    snapshot.write_bytes(b"not a sqlite database")

    with pytest.raises(sqlite3.DatabaseError):
        store_module.Store(live, snapshot_path=snapshot)
    with sqlite3.connect(live) as connection:
        assert connection.execute("SELECT value FROM probe").fetchone() == ("kept",)


def test_interrupted_restore_does_not_publish_partial_database(tmp_path, monkeypatch):
    live = tmp_path / "live.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    snapshot.write_bytes(b"original fixture input")

    def interrupted_copy(source, destination):
        destination.write(source.read(4))
        raise OSError("fixture copy interrupted")

    monkeypatch.setattr(store_module.shutil, "copyfileobj", interrupted_copy)
    with pytest.raises(OSError, match="copy interrupted"):
        store_module.Store(live, snapshot_path=snapshot)
    assert not live.exists()
    assert snapshot.read_bytes() == b"original fixture input"
    assert not list(tmp_path.glob(".live.sqlite3.*.restore"))


def test_corrupt_snapshot_cannot_seed_a_missing_local_database(tmp_path):
    live = tmp_path / "live.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    snapshot.write_bytes(b"not a SQLite database")
    with pytest.raises(sqlite3.DatabaseError):
        store_module.Store(live, snapshot_path=snapshot)
    assert not live.exists()
    assert not list(tmp_path.glob(".live.sqlite3.*.restore"))


def test_restore_preserves_a_local_database_created_during_staging(tmp_path, monkeypatch):
    live = tmp_path / "live.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    with sqlite3.connect(snapshot) as connection:
        connection.execute("CREATE TABLE probe (value TEXT)")
        connection.execute("INSERT INTO probe VALUES ('snapshot')")
    link = store_module.os.link

    def publish_during_restore(staged, target):
        connection = sqlite3.connect(live)
        try:
            connection.execute("CREATE TABLE probe (value TEXT)")
            connection.execute("INSERT INTO probe VALUES ('concurrent local')")
            connection.commit()
        finally:
            connection.close()
        link(staged, target)

    monkeypatch.setattr(store_module.os, "link", publish_during_restore)
    with pytest.raises(RuntimeError, match="recovery required"):
        store_module.Store(live, snapshot_path=snapshot)
    with sqlite3.connect(live) as connection:
        assert connection.execute("SELECT value FROM probe").fetchone() == ("concurrent local",)
    assert not list(tmp_path.glob(".live.sqlite3.*.restore"))


def _staging_database(path, rows):
    connection = sqlite3.connect(path)
    try:
        connection.execute("""CREATE TABLE events (id TEXT, source TEXT, event_type TEXT, occurred_at TEXT,
                           subject TEXT, canonical_url TEXT, payload_json TEXT, provenance_json TEXT)""")
        connection.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        connection.commit()
    finally:
        connection.close()


def test_staged_merge_rolls_back_entire_member_and_detaches_after_sql_error(tmp_path):
    stage = tmp_path / "stage.sqlite3"
    good = ("one", "spotify", "stream", None, "one", None, "{}", "{}")
    bad = ("two", "spotify", None, None, "two", None, "{}", "{}")
    _staging_database(stage, [good, bad])
    store = store_module.Store()
    with pytest.raises(sqlite3.IntegrityError):
        store.merge_staged_personal_events(stage, 2)
    assert store.count_personal_events() == 0
    assert [row[1] for row in store.connection.execute("PRAGMA database_list").fetchall()] == ["main"]
    store.connection.execute("CREATE TABLE after_failure (value TEXT)")
    store.connection.commit()


def test_staged_merge_waits_for_other_connection_transaction(tmp_path):
    stage = tmp_path / "stage.sqlite3"
    _staging_database(stage, [("one", "spotify", "stream", None, "one", None, "{}", "{}")])
    store = store_module.Store()
    store.connection.execute("CREATE TABLE concurrent_probe (value TEXT)")
    store.connection.commit()
    store.connection.execute("INSERT INTO concurrent_probe VALUES ('pending')")
    started, completed = threading.Event(), threading.Event()
    failures = []

    def merge():
        started.set()
        try:
            store.merge_staged_personal_events(stage, 1)
        except (sqlite3.Error, RuntimeError, TimeoutError, OSError) as exc:
            failures.append(exc)
        finally:
            completed.set()

    thread = threading.Thread(target=merge)
    thread.start()
    try:
        assert started.wait(2)
        assert not completed.wait(0.1)
        store.connection.commit()
        assert completed.wait(5)
    finally:
        if not completed.is_set():
            store.connection.rollback()
        thread.join(5)
    assert not thread.is_alive()
    assert failures == []
    assert store.count_personal_events() == 1
    assert store.connection.execute("SELECT value FROM concurrent_probe").fetchone() == ("pending",)


def test_staged_merge_is_included_in_restorable_snapshot(tmp_path):
    stage = tmp_path / "stage.sqlite3"
    _staging_database(stage, [("one", "spotify", "stream", None, "one", None, "{}", "{}")])
    snapshot = tmp_path / "snapshot" / "state.sqlite3"
    store = store_module.Store(tmp_path / "live.sqlite3", snapshot_path=snapshot)
    assert store.merge_staged_personal_events(stage, 1) == 1
    restored = store_module.Store(tmp_path / "restored.sqlite3", snapshot_path=snapshot)
    assert restored.connection.execute("SELECT id FROM personal_events").fetchall() == [("one",)]


@pytest.mark.parametrize("failure", ["copy", "replace"])
def test_failed_snapshot_publish_preserves_previous_snapshot_and_unrelated_temp(
    tmp_path, monkeypatch, failure
):
    live = tmp_path / "live.sqlite3"
    snapshot = tmp_path / "persistent" / "state.sqlite3"
    store = store_module.Store(live, snapshot_path=snapshot)
    store.connection.execute("CREATE TABLE snapshot_probe (value TEXT)")
    store.connection.execute("INSERT INTO snapshot_probe VALUES ('previous')")
    store.connection.commit()
    original = snapshot.read_bytes()
    unrelated = snapshot.with_suffix(snapshot.suffix + ".tmp")
    unrelated.write_bytes(b"unrelated temporary file")
    store.connection.execute("INSERT INTO snapshot_probe VALUES ('local-only')")

    def fail_publish(*_args, **_kwargs):
        raise OSError("injected snapshot failure")

    if failure == "copy":
        monkeypatch.setattr(store_module.shutil, "copyfileobj", fail_publish)
    else:
        monkeypatch.setattr(store_module.os, "replace", fail_publish)
    with pytest.raises(OSError, match="injected snapshot failure"):
        store.connection.commit()
    assert snapshot.read_bytes() == original
    assert unrelated.read_bytes() == b"unrelated temporary file"
    assert list(snapshot.parent.glob(f".{snapshot.name}.*.tmp")) == []
    assert store.connection.execute("SELECT value FROM snapshot_probe ORDER BY value").fetchall() == [
        ("local-only",), ("previous",)
    ]


def test_overlapping_snapshots_use_distinct_staging_files(tmp_path, monkeypatch):
    snapshot = tmp_path / "persistent" / "state.sqlite3"
    first = store_module.Store(tmp_path / "first.sqlite3", snapshot_path=snapshot)
    second = store_module.Store(tmp_path / "second.sqlite3", snapshot_path=snapshot)
    for index, store in enumerate((first, second)):
        store.connection.execute("CREATE TABLE snapshot_probe (value TEXT)")
        store.connection.execute("INSERT INTO snapshot_probe VALUES (?)", (str(index),))
    barrier = threading.Barrier(2, timeout=5)
    original_copy = store_module.shutil.copyfileobj
    original_replace = store_module.os.replace
    staged = []
    failures = []
    replace_lock = threading.Lock()

    def synchronized_copy(source, destination):
        barrier.wait()
        return original_copy(source, destination)

    def capture_replace(source, destination):
        with replace_lock:
            staged.append(Path(source))
            return original_replace(source, destination)

    def commit(store):
        try:
            store.connection.commit()
        except (OSError, sqlite3.Error, threading.BrokenBarrierError) as exc:
            failures.append(exc)

    monkeypatch.setattr(store_module.shutil, "copyfileobj", synchronized_copy)
    monkeypatch.setattr(store_module.os, "replace", capture_replace)
    threads = [threading.Thread(target=commit, args=(store,)) for store in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert len(staged) == 2 and len(set(staged)) == 2
    assert all(path.parent == snapshot.parent for path in staged)
    assert list(snapshot.parent.glob(f".{snapshot.name}.*.tmp")) == []
    with sqlite3.connect(snapshot) as check:
        assert check.execute("SELECT value FROM snapshot_probe").fetchone() in (("0",), ("1",))


def _ranking_candidate(identifier, source_id, *, now):
    return Candidate(
        identifier, "synthetic", source_id, f"https://example.invalid/{source_id}",
        source_id, now, "test",
    )


def test_partial_ranking_run_rolls_back_inputs_rankings_and_membership(tmp_path):
    store = store_module.Store(tmp_path / "ranking.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    candidates = [_ranking_candidate(name, "same-source-id", now=now) for name in ("a", "b")]
    run = create_ranking_run(candidates, Policy("synthetic-v1", {"recency": 1}), now=now)
    with pytest.raises(sqlite3.IntegrityError):
        store.put_ranking_run(run)
    for table in ("candidates", "rankings", "ranking_inputs", "ranking_runs", "ranking_run_items", "impressions"):
        assert store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_ranked_run_reloads_frozen_inputs_and_rejects_malformed_members(tmp_path):
    path = tmp_path / "ranking.sqlite3"
    store = store_module.Store(path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    candidate = _ranking_candidate("a", "a", now=now)
    policy = Policy("synthetic-v1", {"recency": 1})
    run = create_ranking_run([candidate], policy, now=now)
    store.put_ranking_run(run)
    store.put_candidate(replace(candidate, title="current metadata changed"))
    store.put_ranking_run(create_ranking_run([candidate], policy, now=now))
    assert store.connection.execute("SELECT COUNT(*) FROM ranking_inputs").fetchone()[0] == 2
    reloaded = store_module.Store(path)
    assert ranking_run_to_dict(reloaded.get_ranking_run(run.id)) == ranking_run_to_dict(run)
    assert reloaded.get_ranking_run(run.id).members[0].ranked.candidate.title == "a"
    row_id, payload = reloaded.connection.execute(
        """SELECT ranking.id, ranking.explanation_json FROM rankings AS ranking
           JOIN ranking_run_items AS item ON item.ranking_id = ranking.id
           WHERE item.run_id = ?""", (run.id,),
    ).fetchone()
    malformed = json.loads(payload)
    malformed["final_score"] = True
    reloaded.connection.execute(
        "UPDATE rankings SET explanation_json = ? WHERE id = ?", (json.dumps(malformed), row_id),
    )
    reloaded.connection.commit()
    with pytest.raises(ValueError, match="requires finite numbers"):
        reloaded.get_ranking_run(run.id)
    reloaded.connection.execute("UPDATE rankings SET explanation_json = ? WHERE id = ?", (payload, row_id))
    reloaded.connection.execute("DELETE FROM ranking_run_items WHERE run_id = ?", (run.id,))
    reloaded.connection.commit()
    with pytest.raises(ValueError, match="membership is incomplete"):
        reloaded.get_ranking_run(run.id)
    with pytest.raises(ValueError, match="membership is incomplete"):
        reloaded.record_impression(run.id, ("a",), presented_at=now)
    assert reloaded.connection.execute("SELECT COUNT(*) FROM impressions").fetchone()[0] == 0


def test_concurrent_runs_keep_separate_membership_on_one_store(tmp_path):
    store = store_module.Store(tmp_path / "ranking.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    candidates = [_ranking_candidate(name, name, now=now) for name in ("a", "b")]
    policy = Policy("synthetic-v1", {"recency": 1})
    runs = [create_ranking_run(candidates, policy, now=now) for _ in range(6)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(store.put_ranking_run, runs))
    assert all(store.get_ranking_run(run.id) == run for run in runs)
    assert store.connection.execute("SELECT COUNT(*) FROM ranking_inputs").fetchone()[0] == 3
    assert store.connection.execute("SELECT COUNT(*) FROM ranking_run_items").fetchone()[0] == 12


def test_ranking_waits_for_an_existing_writer_without_committing_its_work(tmp_path, monkeypatch):
    store = store_module.Store(tmp_path / "ranking.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    run = create_ranking_run(
        [_ranking_candidate("ranked", "ranked", now=now)], Policy("fixture", {}), now=now,
    )
    waiting = threading.Event()
    resume = threading.Event()

    def wait_for_writer(_):
        waiting.set()
        assert resume.wait(5)

    monkeypatch.setattr(store_module.time, "sleep", wait_for_writer)
    store.connection.execute("BEGIN IMMEDIATE")
    store._upsert_candidate(store.connection, _ranking_candidate("pending", "pending", now=now))
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(store.put_ranking_run, run)
            try:
                assert waiting.wait(5)
                assert store.connection.in_transaction
                store.connection.rollback()
            finally:
                resume.set()
            future.result(timeout=5)
        assert store.get_ranking_run(run.id) == run
        assert store.connection.execute("SELECT id FROM candidates").fetchall() == [("ranked",)]
    finally:
        store.connection.close()
