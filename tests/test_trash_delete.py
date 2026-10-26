"""Deleting for good from the Trash folder, and nowhere else."""
import shutil

import pytest


@pytest.fixture
def trash(files_dir):
    t = files_dir / 'file types' / 'trash'
    t.mkdir(parents=True, exist_ok=True)
    for old in t.iterdir():
        shutil.rmtree(old) if old.is_dir() else old.unlink()
    return t


def _del(client, auth_headers, **body):
    return client.post('/api/lego/delete', json=body, headers=auth_headers)


def test_trash_is_a_place(client, auth_headers, files_dir, trash):
    d = client.get('/api/lego/browse', query_string={'path': ''}, headers=auth_headers).get_json()
    assert any(p['name'] == 'Trash' for p in d['places']) and d['trash']


def test_delete_files_in_trash(client, auth_headers, files_dir, trash):
    (trash / 'a.txt').write_text('a'); (trash / 'b.txt').write_text('b')
    r = _del(client, auth_headers, paths=['file types/trash/a.txt', 'file types/trash/b.txt'])
    assert r.get_json() == {'ok': True, 'deleted': 2, 'failed': 0}
    assert not list(trash.iterdir())


def test_delete_refuses_files_outside_trash(client, auth_headers, files_dir, trash):
    (files_dir / 'keep.txt').write_text('keep')
    r = _del(client, auth_headers, paths=['keep.txt', 'file types/trash/../../keep.txt', '../keep.txt'])
    assert r.get_json()['deleted'] == 0
    assert (files_dir / 'keep.txt').read_text() == 'keep'


def test_delete_removes_folders_in_trash_but_not_trash_itself(client, auth_headers, files_dir, trash):
    (trash / 'sub' / 'deeper').mkdir(parents=True)
    (trash / 'sub' / 'deeper' / 'f.txt').write_text('x')
    r = _del(client, auth_headers, paths=['file types/trash', 'file types/trash/sub'])
    assert r.get_json()['deleted'] == 1 and r.get_json()['failed'] == 1
    assert trash.is_dir() and not (trash / 'sub').exists()


def test_empty_trash(client, auth_headers, files_dir, trash):
    for i in range(3):
        (trash / f'f{i}.bin').write_bytes(b'x')
    (files_dir / 'safe.txt').write_text('safe')
    assert _del(client, auth_headers, empty=True).get_json()['deleted'] == 3
    assert not list(trash.iterdir()) and (files_dir / 'safe.txt').exists()


def test_delete_needs_a_login(client, trash):
    assert client.post('/api/lego/delete', json={'empty': True}).status_code in (401, 403)


def _trash(client, auth_headers, path):
    return client.post('/api/lego/trash', json={'path': path}, headers=auth_headers)


def test_trash_a_folder_and_get_it_back_intact(client, auth_headers, files_dir, trash):
    (files_dir / 'old' / 'inner').mkdir(parents=True, exist_ok=True)
    (files_dir / 'old' / 'inner' / 'a.txt').write_text('hello')
    r = _trash(client, auth_headers, 'old')
    assert r.status_code == 200
    assert not (files_dir / 'old').exists()
    assert (trash / 'old' / 'inner' / 'a.txt').read_text() == 'hello'
    # a second folder with the same name gets a new name instead of replacing
    (files_dir / 'old').mkdir()
    assert _trash(client, auth_headers, 'old').status_code == 200 and (trash / 'old_1').is_dir()


def test_cannot_trash_the_trash_or_its_parents(client, auth_headers, files_dir, trash):
    assert _trash(client, auth_headers, 'file types/trash').status_code == 400
    assert _trash(client, auth_headers, 'file types').status_code == 400


def test_cannot_trash_the_root_by_walking_out_of_it(client, auth_headers, files_dir, trash):
    for p in ('..', '../', 'inbox/../..', '.'):
        r = _trash(client, auth_headers, p)
        assert r.status_code in (400, 403), p
    assert files_dir.is_dir()
