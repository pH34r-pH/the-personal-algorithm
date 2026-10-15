import io
import stat
import zipfile
from datetime import UTC, datetime

import pytest

from personal_algorithm import reddit_import


CSV = 'id,permalink,date,body\nabc,/r/example/comments/post/title/abc/,2026-10-01 12:00:00 UTC,"Hello, café\nsecond line"\n'


def test_comment_fields_provenance_and_repeat_identity():
    event, = reddit_import._events(io.StringIO(CSV), "comments.csv", "archive-a", "comments.csv")
    other, = reddit_import._events(io.StringIO(CSV), "comments.csv", "archive-b", "comments.csv")
    assert event.subject == "Hello, café\nsecond line"
    assert event.occurred_at == datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert event.canonical_url == "https://www.reddit.com/r/example/comments/post/title/abc/"
    assert event.provenance["reddit_id"] == "t1_abc"
    assert event.provenance["archive_sha256"] == "archive-a"
    assert other.provenance["archive_sha256"] == "archive-b"
    assert event.id == other.id
    edited, = reddit_import._events(
        io.StringIO(CSV.replace("Hello", "Changed")), "comments.csv", "archive-b", "comments.csv"
    )
    assert edited.id != event.id


@pytest.mark.parametrize("payload", [
    "wrong,body\na,b\n", "id,body,body\na,b,c\n", "id,body\na,b,c\n", "id,body\na\n",
    "id,body\n../x,b\n",
])
def test_rejects_invalid_schema_or_row(payload):
    with pytest.raises(ValueError):
        list(reddit_import._events(io.StringIO(payload), "comments.csv", "archive", "comments.csv"))


@pytest.mark.parametrize("value", ["", "not-a-date", "2026-10-01T12:00:00"])
def test_unknown_time_is_not_fabricated(value):
    assert reddit_import._timestamp(value) is None


@pytest.mark.parametrize("value", [
    "javascript:alert(1)", "https://reddit.com.evil.example/test", "//evil.example/test",
    "https://name@reddit.com/test", "https://[bad",
])
def test_rejects_unsafe_link(value):
    assert reddit_import._reddit_url(value) is None


def test_zip_bom_and_other_members(tmp_path):
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("export/comments.csv", "\ufeff" + CSV)
        archive.writestr("messages.csv", "private fixture not imported")
    rows = []
    for member, table, stream in reddit_import._csv_sources(path):
        rows.extend(reddit_import._events(stream, table, "archive", member))
    assert len(rows) == 1
    assert rows[0].provenance["member"] == "export/comments.csv"


@pytest.mark.parametrize("name", ["../comments.csv", "/comments.csv", "..\\comments.csv"])
def test_zip_traversal_rejected_without_extraction(tmp_path, name):
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, CSV)
    with pytest.raises(ValueError):
        list(reddit_import._csv_sources(path))
    assert not (tmp_path / "comments.csv").exists()


def test_zip_link_rejected(tmp_path):
    path = tmp_path / "export.zip"
    entry = zipfile.ZipInfo("comments.csv")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(entry, "target")
    with pytest.raises(ValueError):
        list(reddit_import._csv_sources(path))


def test_decompressed_size_limit(tmp_path, monkeypatch):
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("comments.csv", CSV)
    monkeypatch.setattr(reddit_import, "MAX_MEMBER_BYTES", 10)
    with pytest.raises(ValueError):
        list(reddit_import._csv_sources(path))


def test_post_and_empty_comment_table():
    post, = reddit_import._events(
        io.StringIO("id,title,body\nt3_def,A title,Some text\n"), "posts.csv", "archive", "posts.csv"
    )
    assert post.event_type == "post_snapshot"
    assert post.subject == "A title"
    assert post.payload["body"] == "Some text"
    assert list(reddit_import._events(
        io.StringIO("id,body\n"), "comments.csv", "archive", "comments.csv"
    )) == []
