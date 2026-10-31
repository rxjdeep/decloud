"""Files: new folder, rename, copy / move."""
import os

import pytest


@pytest.fixture
def box(files_dir):
    b = files_dir / 'ops'
    if b.exists():
        import shutil
        shutil.rmtree(b)
    (b / 'sub').mkdir(parents=True)
    (b / 'a.txt').write_text('A')
    (b / 'sub' / 'in.txt').write_text('in')
    return b


def _post(client, auth_headers, url, body):
    return client.post(url, json=body, headers=auth_headers)


def test_mkdir(client, auth_headers, box):
    r = _post(client, auth_headers, '/api/files/mkdir', {'dir': 'ops', 'name': 'new one'})
    assert r.status_code == 200 and r.get_json()['path'] == 'ops/new one'
    assert (box / 'new one').is_dir()
    assert _post(client, auth_headers, '/api/files/mkdir', {'dir': 'ops', 'name': 'new one'}).status_code == 409
    for bad in ('', '.', '..', 'a/b', '.hidden'):
        assert _post(client, auth_headers, '/api/files/mkdir', {'dir': 'ops', 'name': bad}).status_code == 400
    assert _post(client, auth_headers, '/api/files/mkdir', {'dir': 'nope', 'name': 'x'}).status_code in (403, 404)


def test_rename(client, auth_headers, box):
    r = _post(client, auth_headers, '/api/files/rename', {'path': 'ops/a.txt', 'name': 'b.txt'})
    assert r.status_code == 200 and r.get_json()['path'] == 'ops/b.txt'
    assert not (box / 'a.txt').exists() and (box / 'b.txt').read_text() == 'A'
    (box / 'c.txt').write_text('C')
    assert _post(client, auth_headers, '/api/files/rename', {'path': 'ops/b.txt', 'name': 'c.txt'}).status_code == 409
    assert (box / 'c.txt').read_text() == 'C'                                   # nothing replaced
    assert _post(client, auth_headers, '/api/files/rename', {'path': 'ops/b.txt', 'name': 'x/y'}).status_code == 400
    assert _post(client, auth_headers, '/api/files/rename', {'path': 'ops/missing', 'name': 'z'}).status_code == 404
    assert _post(client, auth_headers, '/api/files/rename', {'path': 'ops/../ops/b.txt', 'name': 'z'}).status_code == 400


def test_rename_refuses_symlinks_and_main_folders(client, auth_headers, box, files_dir):
    os.symlink(box / 'a.txt', box / 'link')
    assert _post(client, auth_headers, '/api/files/rename', {'path': 'ops/link', 'name': 'l2'}).status_code == 400
    assert (box / 'a.txt').exists()
    trash = files_dir / 'file types' / 'trash'
    trash.mkdir(parents=True, exist_ok=True)
    assert _post(client, auth_headers, '/api/files/rename', {'path': 'file types/trash', 'name': 'bin'}).status_code == 403
    assert trash.is_dir()


def test_copy_and_move(client, auth_headers, box):
    r = _post(client, auth_headers, '/api/files/paste', {'paths': ['ops/a.txt', 'ops/sub'], 'dir': 'ops/sub', 'move': False}).get_json()
    assert r['ok'] and r['done'] == 1                                            # the folder cannot go into itself
    assert [x for x in r['results'] if 'error' in x][0]['path'] == 'ops/sub'
    assert (box / 'a.txt').exists() and (box / 'sub' / 'a.txt').read_text() == 'A'
    r = _post(client, auth_headers, '/api/files/paste', {'paths': ['ops/a.txt'], 'dir': 'ops/sub', 'move': True}).get_json()
    assert r['results'][0]['to'] == 'ops/sub/a (2).txt'                          # name taken: numbered, nothing replaced
    assert not (box / 'a.txt').exists() and (box / 'sub' / 'a.txt').read_text() == 'A'


def test_copy_a_folder(client, auth_headers, box):
    (box / 'dest').mkdir()
    r = _post(client, auth_headers, '/api/files/paste', {'paths': ['ops/sub'], 'dir': 'ops/dest', 'move': False})
    assert r.status_code == 200
    assert (box / 'dest' / 'sub' / 'in.txt').read_text() == 'in' and (box / 'sub' / 'in.txt').exists()
    again = _post(client, auth_headers, '/api/files/paste', {'paths': ['ops/sub'], 'dir': 'ops/dest', 'move': False}).get_json()
    assert again['results'][0]['to'] == 'ops/dest/sub (2)'


def test_move_in_place_is_a_noop_and_errors_are_reported(client, auth_headers, box):
    r = _post(client, auth_headers, '/api/files/paste', {'paths': ['ops/a.txt'], 'dir': 'ops', 'move': True})
    assert r.status_code == 200 and (box / 'a.txt').exists() and not (box / 'a (2).txt').exists()
    r = _post(client, auth_headers, '/api/files/paste', {'paths': ['ops/nope'], 'dir': 'ops', 'move': True})
    assert r.status_code == 400 and r.get_json()['error'] == 'Not found'
    assert _post(client, auth_headers, '/api/files/paste', {'paths': [], 'dir': 'ops'}).status_code == 400
    assert _post(client, auth_headers, '/api/files/paste', {'paths': ['ops/a.txt'], 'dir': '.ssh'}).status_code == 403


def test_move_refuses_main_folders(client, auth_headers, box, files_dir):
    (files_dir / 'file types' / 'trash').mkdir(parents=True, exist_ok=True)
    r = _post(client, auth_headers, '/api/files/paste', {'paths': ['file types/trash'], 'dir': 'ops', 'move': True})
    assert r.status_code == 400 and (files_dir / 'file types' / 'trash').is_dir()


def test_browse_reports_disk_space(client, auth_headers, box):
    d = client.get('/api/files/browse?path=ops', headers=auth_headers).get_json()['disk']
    assert d['total'] > 0 and d['free'] >= 0
