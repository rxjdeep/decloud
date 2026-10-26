"""The name shown in the home-screen prompt (name@reclmr)."""
import json

import pytest

import shared
from routes.profile import clean_user_name


@pytest.fixture(autouse=True)
def _clean():
    s = shared.load_settings(); s.pop('profile', None); shared.save_settings(s)
    yield


def test_clean_user_name():
    assert clean_user_name('Raj Deep') == 'raj_deep'
    assert clean_user_name('  A  B  ') == 'a_b'
    assert clean_user_name('r@j; rm -rf /') == 'rj_rm_-rf'
    assert clean_user_name('...--__') == ''
    assert clean_user_name('x' * 50) == 'x' * 20
    assert clean_user_name(None) == '' and clean_user_name(5) == ''


def test_profile_round_trip(client, auth_headers):
    assert client.get('/api/profile', headers=auth_headers).get_json() == {'name': ''}
    r = client.post('/api/profile', json={'name': 'Raj Deep'}, headers=auth_headers)
    assert r.status_code == 200 and r.get_json() == {'name': 'raj_deep'}
    assert client.get('/api/profile', headers=auth_headers).get_json() == {'name': 'raj_deep'}
    assert json.loads(shared.SETTINGS_FILE.read_text())['profile']['name'] == 'raj_deep'


def test_profile_rejects_a_name_with_nothing_usable(client, auth_headers):
    assert client.post('/api/profile', json={'name': '@@@'}, headers=auth_headers).status_code == 400
    assert client.post('/api/profile', json={}, headers=auth_headers).status_code == 400


def test_profile_needs_sign_in(client):
    assert client.get('/api/profile').status_code == 401
