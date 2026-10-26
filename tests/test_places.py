"""Adding and removing folders in the Files sidebar."""
import json
import shutil

import pytest

import shared


@pytest.fixture(autouse=True)
def _clean(files_dir):
    (files_dir / 'projects' / 'deep').mkdir(parents=True, exist_ok=True)
    try:
        s = json.loads(shared.SETTINGS_FILE.read_text())
        s.pop('files_places', None)
        shared.SETTINGS_FILE.write_text(json.dumps(s))
    except Exception:
        pass
    yield


def _places(client, auth_headers):
    return client.get('/api/lego/browse', query_string={'path': ''}, headers=auth_headers).get_json()['places']


def _post(client, auth_headers, **body):
    return client.post('/api/files/places', json=body, headers=auth_headers)


def test_add_and_remove_a_folder(client, auth_headers, files_dir):
    assert _post(client, auth_headers, action='add', path='projects/deep', name='Deep work').status_code == 200
    mine = [p for p in _places(client, auth_headers) if p.get('custom')]
    assert mine == [{'name': 'Deep work', 'path': 'projects/deep', 'icon': 'folder', 'custom': True}]
    assert _post(client, auth_headers, action='add', path='projects/deep').status_code == 200          # adding twice does not duplicate
    assert len([p for p in _places(client, auth_headers) if p.get('custom')]) == 1
    _post(client, auth_headers, action='remove', path='projects/deep')
    assert not [p for p in _places(client, auth_headers) if p.get('custom')]


def test_add_needs_a_real_folder(client, auth_headers, files_dir):
    (files_dir / 'f.txt').write_text('x')
    assert _post(client, auth_headers, action='add', path='f.txt').status_code == 404
    assert _post(client, auth_headers, action='add', path='nope/nothing').status_code == 404
    assert _post(client, auth_headers, action='add', path='').status_code == 400


def test_built_in_places_can_be_hidden_and_reset(client, auth_headers, files_dir):
    names = [p['name'] for p in _places(client, auth_headers)]
    assert 'Trash' in names
    victim = next(n for n in names if n not in ('Trash',))
    assert _post(client, auth_headers, action='remove', path='x', name=victim).status_code == 200
    assert victim not in [p['name'] for p in _places(client, auth_headers)]
    _post(client, auth_headers, action='reset')
    assert victim in [p['name'] for p in _places(client, auth_headers)]


def test_trash_cannot_be_removed(client, auth_headers, files_dir):
    assert _post(client, auth_headers, action='remove', path='x', name='Trash').status_code == 400
    assert 'Trash' in [p['name'] for p in _places(client, auth_headers)]


def test_places_need_a_login(client):
    assert client.post('/api/files/places', json={'action': 'reset'}).status_code in (401, 403)
