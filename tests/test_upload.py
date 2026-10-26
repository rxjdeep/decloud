"""Uploading a file from a device into a Files folder."""
import os

import pytest


@pytest.fixture
def folder(files_dir):
    (files_dir / 'inbox').mkdir(exist_ok=True)
    for old in (files_dir / 'inbox').iterdir():
        old.unlink()
    return files_dir / 'inbox'


def _put(client, auth_headers, name, data, dir='inbox'):
    return client.put('/api/files/upload', query_string={'dir': dir, 'name': name}, data=data,
                      headers={**auth_headers, 'Content-Type': 'application/octet-stream'})


def test_upload_stores_the_file(client, auth_headers, folder):
    r = _put(client, auth_headers, 'hello.txt', b'hello world')
    assert r.status_code == 200 and r.get_json()['name'] == 'hello.txt'
    assert (folder / 'hello.txt').read_bytes() == b'hello world'
    assert not [p for p in os.listdir(folder) if p.startswith('.upload-')]      # no temp file left behind


def test_upload_never_replaces(client, auth_headers, folder):
    (folder / 'a.txt').write_text('old')
    r = _put(client, auth_headers, 'a.txt', b'new')
    assert r.get_json()['name'] == 'a (2).txt'
    assert (folder / 'a.txt').read_text() == 'old' and (folder / 'a (2).txt').read_text() == 'new'
    assert _put(client, auth_headers, 'a.txt', b'third').get_json()['name'] == 'a (3).txt'


@pytest.mark.parametrize('name', ['', '.', '..', '../x', 'a/b', 'a\\b', '.hidden', 'x\x00y'])
def test_upload_rejects_bad_names(client, auth_headers, folder, name):
    assert _put(client, auth_headers, name, b'x').status_code == 400


@pytest.mark.parametrize('dir', ['../', 'inbox/../..', '/etc', '..'])
def test_upload_cannot_leave_the_files_root(client, auth_headers, folder, files_dir, dir):
    r = _put(client, auth_headers, 'evil.txt', b'x', dir=dir)
    assert r.status_code in (200, 403, 404)
    assert not os.path.exists('/etc/evil.txt') and not os.path.exists(os.path.join(os.path.dirname(str(files_dir)), 'evil.txt'))
    if r.status_code == 200:                                      # a path that tried to escape was pulled back inside the root
        assert any(files_dir.rglob('evil.txt'))
        for f in files_dir.rglob('evil.txt'):
            f.unlink()


def test_upload_needs_a_login(client, folder):
    r = client.put('/api/files/upload', query_string={'dir': 'inbox', 'name': 'x.txt'}, data=b'x')
    assert r.status_code in (401, 403)


def test_upload_missing_folder(client, auth_headers, folder):
    assert _put(client, auth_headers, 'x.txt', b'x', dir='nope').status_code == 404


def test_upload_size_limit(client, auth_headers, folder, monkeypatch):
    monkeypatch.setenv('RECLMR_UPLOAD_MAX_MB', '1')
    assert _put(client, auth_headers, 'big.bin', b'x' * (1024 * 1024 + 1)).status_code == 413


@pytest.mark.parametrize('dir', ['.ssh', 'inbox/.config', '.hidden/sub'])
def test_upload_refuses_hidden_folders(client, auth_headers, folder, dir):
    assert _put(client, auth_headers, 'x.txt', b'x', dir=dir).status_code == 403
