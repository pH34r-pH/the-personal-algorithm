import json
from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from personal_algorithm.application import ApplicationSettings, create_private_app
from personal_algorithm.connections import ConnectionStore
from personal_algorithm.models import PersonalEvent
from personal_algorithm.providers import Connection, ConnectionStatus
from personal_algorithm.store import Store

CHANNEL = 'UCabcdefghijklmnopqrstuv'


class Credentials:
    def get(self, ref):
        assert ref == 'memory://google'
        return json.dumps({'access_token': 'private-token',
                           'expires_at': (datetime.now(UTC) + timedelta(hours=1)).isoformat()})


def test_private_youtube_discovery_uses_only_imported_channel(tmp_path):
    requests = []

    def response(request):
        requests.append(request)
        assert request.headers['authorization'] == 'Bearer private-token'
        if request.url.path.endswith('/channels'):
            assert request.url.params['id'] == CHANNEL
            return httpx.Response(200, json={'items': [{'id': CHANNEL, 'contentDetails': {
                'relatedPlaylists': {'uploads': 'UUabcdefghijklmnopqrstuv'}}}]})
        assert request.url.path.endswith('/playlistItems')
        return httpx.Response(200, json={'items': [
            {'snippet': {'title': '<script>Example</script>', 'publishedAt': '2026-01-01T00:00:00Z'},
             'contentDetails': {'videoId': 'abcdefghijk'}},
            {'snippet': {'title': 'Invalid'}, 'contentDetails': {'videoId': 'invalid/path'}},
        ]})

    settings = ApplicationSettings(
        database=str(tmp_path / 'db.sqlite'), owner_object_id='owner',
        google_client_id='id', google_client_secret='secret',
        google_redirect_uri='https://example.com/connections/google/callback',
        archive_dir=str(tmp_path / 'archives'),
    )
    http = httpx.Client(transport=httpx.MockTransport(response))
    app = create_private_app(settings, credentials=Credentials(), youtube_http=http)
    store = Store(settings.database)
    store.put_personal_event(PersonalEvent(
        id=f'youtube:subscription:{CHANNEL}', source='youtube', event_type='subscription',
        subject='Example', canonical_url=f'https://www.youtube.com/channel/{CHANNEL}',
        payload={'channel_id': CHANNEL},
    ))
    ConnectionStore(store).put(Connection(
        provider_id='google', account_label='Google', status=ConnectionStatus.CONNECTED,
        granted_scopes=('openid', 'https://www.googleapis.com/auth/youtube.readonly'),
        connected_at=datetime.now(UTC), credential_ref='memory://google', metadata={},
    ))
    client = TestClient(app)
    headers = {'x-ms-client-principal-id': 'owner'}
    assert client.get('/youtube/subscriptions').status_code == 401
    assert client.get('/youtube/').status_code == 401
    subs = client.get('/youtube/subscriptions', headers=headers)
    assert subs.status_code == 200
    assert subs.headers['cache-control'] == 'no-store'
    assert subs.json()['subscriptions'][0]['channel_id'] == CHANNEL
    assert client.get('/youtube/recent', params={'channel_id': 'UC' + 'z' * 22},
                      headers=headers).status_code == 404
    result = client.get('/youtube/recent', params={'channel_id': CHANNEL}, headers=headers)
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'no-store'
    assert result.json()['videos'] == [{
        'id': 'abcdefghijk', 'title': '<script>Example</script>',
        'published_at': '2026-01-01T00:00:00Z',
        'url': 'https://www.youtube.com/watch?v=abcdefghijk',
        'embed_url': 'https://www.youtube.com/embed/abcdefghijk',
    }]
    assert len(requests) == 2
    assert 'private-token' not in result.text
    assert store.connection.execute('SELECT COUNT(*) FROM candidates').fetchone()[0] == 0
    assert 'textContent=video.title' in client.get('/youtube/', headers=headers).text
