import io
import json
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_algorithm.archive_api import create_archive_router
from personal_algorithm.archives import ArchiveInbox
from personal_algorithm.store import Store


def _zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return buffer.getvalue()


def test_google_activity_import_and_retry(tmp_path):
    store = Store()
    app = FastAPI()
    app.include_router(create_archive_router(ArchiveInbox(store, tmp_path)))
    client = TestClient(app)
    rows = [
        {"title": "Watched sample", "titleUrl": "https://youtube.com/watch?v=example",
         "time": "2024-01-01T12:00:00Z", "products": ["YouTube"]},
        {"title": "Unknown time", "time": "not a timestamp"},
    ]
    archive = _zip({"Takeout/My Activity/YouTube/MyActivity.json": json.dumps(rows),
                    "Takeout/Other/private.json": "unrelated"})
    uploaded = client.post('/archives/upload', content=archive,
                           headers={'x-archive-filename': 'takeout.zip',
                                    'x-provider-hint': 'google'}).json()
    for _ in range(2):
        response = client.post(f'/archives/{uploaded["sha256"]}/import')
        assert response.status_code == 200
        assert response.json()['summary']['unknown_timestamps'] == 1
    assert store.count_personal_events() == 2


def test_unrelated_or_unsafe_archive_never_imports(tmp_path):
    store = Store()
    app = FastAPI()
    app.include_router(create_archive_router(ArchiveInbox(store, tmp_path)))
    client = TestClient(app)
    for files, expected in (({'Takeout/Nest/something.json': '[]'}, 200),
                            ({'../My Activity/MyActivity.json': '[]'}, 422)):
        response = client.post('/archives/upload', content=_zip(files),
                               headers={'x-archive-filename': 'takeout.zip',
                                        'x-provider-hint': 'google'})
        imported = client.post(f'/archives/{response.json()["sha256"]}/import')
        assert imported.status_code == expected
        if expected == 200:
            assert imported.json()['status'] == 'stored'
    assert store.count_personal_events() == 0


def test_takeout_html_and_subscriptions_csv_are_imported_without_media(tmp_path):
    store = Store()
    app = FastAPI()
    app.include_router(create_archive_router(ArchiveInbox(store, tmp_path)))
    client = TestClient(app)
    html = '''<html><body><div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">
      Watched <a href="https://www.youtube.com/watch?v=example">A &amp; B</a><br>
      <a href="https://www.youtube.com/channel/UCexample">Channel</a><br>
      Oct 21, 2026, 10:00:00 PM PDT<br></div>
      <div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1 mdl-typography--text-right">
      ignored</div>
      <div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">
      Searched example<br>not a timestamp<br></div></body></html>'''
    csv_text = ('Channel Id,Channel Url,Channel Title\n'
                'UCexample,http://www.youtube.com/channel/UCexample,"Channel, One"\n')
    archive = _zip({
        'Takeout/YouTube and YouTube Music/history/watch-history.html': html,
        'Takeout/YouTube and YouTube Music/subscriptions/subscriptions.csv': csv_text,
        'Takeout/YouTube and YouTube Music/videos/movie.mp4': 'not parsed',
    })
    uploaded = client.post('/archives/upload', content=archive,
                           headers={'x-archive-filename': 'takeout.zip',
                                    'x-provider-hint': 'google'}).json()
    for _ in range(2):
        response = client.post(f'/archives/{uploaded["sha256"]}/import')
        assert response.status_code == 200
        assert response.json()['summary']['files_scanned'] == 2
        assert response.json()['summary']['rows_seen'] == 3
        assert response.json()['summary']['unknown_timestamps'] == 1
    rows = store.connection.execute(
        'SELECT event_type, occurred_at, subject, canonical_url FROM personal_events'
    ).fetchall()
    assert len(rows) == 3
    assert any(row[1] == '2026-10-21T22:00:00-07:00' and row[2] == 'Watched A & B'
               for row in rows)
    assert any(row[0] == 'subscription' and row[2] == 'Channel, One'
               and row[3] == 'https://www.youtube.com/channel/UCexample' for row in rows)


def test_subscription_rejects_mismatched_or_untrusted_url(tmp_path):
    store = Store()
    app = FastAPI()
    app.include_router(create_archive_router(ArchiveInbox(store, tmp_path)))
    client = TestClient(app)
    csv_text = ('Channel Id,Channel Url,Channel Title\n'
                'UCexample,https://evil.example/channel/UCexample,Example\n')
    archive = _zip({'Takeout/YouTube and YouTube Music/subscriptions/subscriptions.csv': csv_text})
    uploaded = client.post('/archives/upload', content=archive,
                           headers={'x-archive-filename': 'takeout.zip',
                                    'x-provider-hint': 'google'}).json()
    response = client.post(f'/archives/{uploaded["sha256"]}/import')
    assert response.status_code == 422
    assert store.count_personal_events() == 0


def test_same_activity_in_two_history_files_is_deduplicated(tmp_path):
    store = Store()
    app = FastAPI()
    app.include_router(create_archive_router(ArchiveInbox(store, tmp_path)))
    client = TestClient(app)
    html = ('<div class="content-cell mdl-cell--6-col">'
            'Watched <a href="https://youtube.com/watch?v=example">Example</a><br>'
            'Oct 21, 2026, 10:00:00 PM PDT<br></div>')
    archive = _zip({
        'Takeout/YouTube and YouTube Music/history/watch-history.html': html,
        'Takeout/YouTube and YouTube Music/history/search-history.html': html,
    })
    uploaded = client.post('/archives/upload', content=archive,
                           headers={'x-archive-filename': 'takeout.zip',
                                    'x-provider-hint': 'google'}).json()
    response = client.post(f'/archives/{uploaded["sha256"]}/import')
    assert response.status_code == 200
    assert response.json()['summary']['rows_seen'] == 2
    assert store.count_personal_events() == 1
