import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from personal_algorithm.application import ApplicationSettings, create_private_app
from personal_algorithm.delegated_auth import SPECS, DelegatedAuthorization
from personal_algorithm.oauth_state import DurableAuthorizationStateStore
from personal_algorithm.store import Store


class MemoryCredentials:
    def __init__(self):
        self.values = {}

    def put(self, provider_id, account_id, secret):
        ref = f"memory://{provider_id}/{account_id}"
        self.values[ref] = secret
        return ref

    def get(self, ref):
        return self.values[ref]


@pytest.mark.parametrize('provider_id', ['google', 'spotify'])
def test_oauth_state_token_storage_and_refresh(provider_id):
    spec = SPECS[provider_id]
    credentials = MemoryCredentials()
    states = DurableAuthorizationStateStore(Store())
    calls = []

    def handle(request):
        calls.append(request)
        if str(request.url) == spec.identity:
            assert request.headers['authorization'] == 'Bearer access'
            return httpx.Response(200, json={'sub': 'abc', 'id': 'abc'})
        if b'grant_type=refresh_token' in request.content:
            return httpx.Response(200, json={'access_token': 'new', 'expires_in': 3600})
        assert str(request.url) == spec.token
        data = parse_qs(request.content.decode())
        verifier = data['code_verifier'][0]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        assert challenge == query['code_challenge'][0]
        if provider_id == 'spotify':
            assert request.headers['authorization'].startswith('Basic ')
        return httpx.Response(200, json={'access_token': 'access', 'refresh_token': 'refresh',
                                         'expires_in': 3600, 'scope': ' '.join(spec.scopes)})

    auth = DelegatedAuthorization(spec, 'client', 'secret',
                                  f'https://example.test/connections/{provider_id}/callback',
                                  credentials, states)
    auth.http = httpx.Client(transport=httpx.MockTransport(handle))
    query = parse_qs(urlsplit(auth.begin()).query)
    assert query['code_challenge_method'] == ['S256']
    if provider_id == 'google':
        assert query['access_type'] == ['offline']
    connection = auth.complete(code='example', state=query['state'][0])
    assert connection.provider_id == provider_id
    assert 'refresh' not in str(connection)
    assert json.loads(credentials.get(connection.credential_ref))['refresh_token'] == 'refresh'
    assert 'refresh' not in '\n'.join(states.store.connection.iterdump())
    with pytest.raises(ValueError):
        auth.complete(code='example', state=query['state'][0])
    auth.refresh(connection)
    secret = json.loads(credentials.get(connection.credential_ref))
    assert secret['access_token'] == 'new'
    assert secret['refresh_token'] == 'refresh'
    assert len(calls) == 3


def test_cross_provider_state_cannot_be_exchanged():
    states = DurableAuthorizationStateStore(Store())
    credentials = MemoryCredentials()
    google = DelegatedAuthorization(SPECS['google'], 'id', 'secret',
                                    'https://example.test/connections/google/callback',
                                    credentials, states)
    spotify = DelegatedAuthorization(SPECS['spotify'], 'id', 'secret',
                                     'https://example.test/connections/spotify/callback',
                                     credentials, states)
    state = parse_qs(urlsplit(google.begin()).query)['state'][0]
    with pytest.raises(ValueError, match='different callback'):
        spotify.complete(code='code', state=state)
    assert credentials.values == {}


def test_configured_routes_are_private_and_unconfigured_routes_fail_closed(tmp_path):
    settings = ApplicationSettings(database=str(tmp_path/'state.db'), owner_object_id='owner',
                                   archive_dir=str(tmp_path/'archives'), google_client_id='id',
                                   google_client_secret='secret',
                                   google_redirect_uri='https://example.test/connections/google/callback')
    client = TestClient(create_private_app(settings, credentials=MemoryCredentials()))
    assert client.get('/connections/google/connect', follow_redirects=False).status_code == 401
    owner = {'x-ms-client-principal-id': 'owner'}
    assert client.get('/connections/google/connect', headers=owner, follow_redirects=False).status_code == 307
    assert client.get('/connections/spotify/connect', headers=owner).status_code == 503
