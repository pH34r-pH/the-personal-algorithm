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
