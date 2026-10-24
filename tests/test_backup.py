"""Phone backup intake: its own token, fixed folders, no overwrite, no escaping the backup folder."""
import os
import pytest

from routes import backup

TOKEN = 'T' * 32


@pytest.fixture()
def bk(tmp_path, monkeypatch):
    monkeypatch.setenv('DECLOUD_BACKUP_TOKEN', TOKEN)
    monkeypatch.setenv('DECLOUD_BACKUP_DIR', str(tmp_path / 'bk'))
    backup._fails.clear()
    return tmp_path / 'bk'


def H(tok=TOKEN, **extra):
    return dict({'Authorization': 'Bearer ' + tok}, **extra)


def put(client, rel, data, m=None, headers=None):
    q = {'p': rel}
    if m is not None:
        q['m'] = str(m)
    return client.put('/api/backup/file', query_string=q, data=data, headers=headers or H())


def test_off_without_a_token(client, monkeypatch):
    monkeypatch.setenv('DECLOUD_BACKUP_TOKEN', '')
    assert client.post('/api/backup/check', json={'files': []}, headers=H()).status_code == 404
    monkeypatch.setenv('DECLOUD_BACKUP_TOKEN', 'short')
    assert client.post('/api/backup/check', json={'files': []}, headers=H('short')).status_code == 404


def test_wrong_and_missing_token(client, bk):
    assert client.post('/api/backup/check', json={'files': []}).status_code == 401
    assert client.post('/api/backup/check', json={'files': []}, headers=H('x' * 32)).status_code == 401


def test_login_session_is_not_enough(client, bk, auth_headers):
    assert client.post('/api/backup/check', json={'files': []}, headers=auth_headers).status_code == 401


def test_lockout_after_ten_wrong_tokens(client, bk):
    for _ in range(10):
        assert client.post('/api/backup/check', json={'files': []}, headers=H('x' * 32)).status_code == 401
    assert client.post('/api/backup/check', json={'files': []}, headers=H('x' * 32)).status_code == 429
    assert client.post('/api/backup/check', json={'files': []}, headers=H()).status_code == 429


def test_check_lists_only_missing(client, bk):
    assert put(client, 'Camera/a.jpg', b'one').status_code == 200
    r = client.post('/api/backup/check', json={'files': [['Camera/a.jpg', 3], ['Camera/b.jpg', 9], ['Nope/c', 1], ['Camera/../x', 1]]}, headers=H())
    assert r.get_json() == {'need': ['Camera/b.jpg'], 'bad': 2}


def test_upload_roundtrip_and_mtime(client, bk):
    r = put(client, 'Screenshots/sub dir/ü shot.png', b'PNGDATA', m=1_700_000_000_000)
    assert r.status_code == 200 and r.get_json() == {'ok': True, 'existed': False}
    f = bk / 'Screenshots' / 'sub dir' / 'ü shot.png'
    assert f.read_bytes() == b'PNGDATA'
    assert int(f.stat().st_mtime) == 1_700_000_000
    assert not [p for p in f.parent.iterdir() if p.name.startswith('.part-')]


def test_never_overwrites(client, bk):
    put(client, 'Documents/n.txt', b'first')
    r = put(client, 'Documents/n.txt', b'second')
    assert r.get_json() == {'ok': True, 'existed': True}
    assert (bk / 'Documents' / 'n.txt').read_bytes() == b'first'


@pytest.mark.parametrize('rel', ['../evil', 'Camera/../../evil', '/etc/passwd', 'Camera', 'Camera//x', 'Camera/./x',
                                 'Other/x.jpg', 'Camera\\x', 'Camera/x\x00.jpg', '', 'Camera/' + 'a' * 300])
def test_bad_paths_rejected(client, bk, rel):
    assert put(client, rel, b'x').status_code == 400
    assert not bk.exists() or not any(bk.rglob('*'))


def test_symlink_cannot_lead_out(client, bk, tmp_path):
    outside = tmp_path / 'outside'; outside.mkdir()
    (bk / 'Camera').parent.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, bk / 'Camera')
    assert put(client, 'Camera/x.jpg', b'x').status_code == 400
    assert not list(outside.iterdir())


def test_truncated_upload_is_discarded(client, bk):
    # the connection says 10 bytes are coming but only 3 arrive (phone lost signal mid-upload)
    r = client.put('/api/backup/file', query_string={'p': 'Camera/t.mp4'}, data=b'abc', headers=H(),
                   environ_overrides={'CONTENT_LENGTH': '10'})
    assert r.status_code == 400
    assert not (bk / 'Camera' / 't.mp4').exists()
    assert not [p for p in (bk / 'Camera').iterdir()]            # and no leftover .part file


def test_size_cap(client, bk, monkeypatch):
    monkeypatch.setenv('DECLOUD_BACKUP_MAX_MB', '1')
    assert put(client, 'Camera/big.bin', b'z' * (1024 * 1024 + 1)).status_code == 413
    assert put(client, 'Camera/ok.bin', b'z' * 1024).status_code == 200


def test_large_body_beats_the_app_wide_cap(client, bk, monkeypatch):
    monkeypatch.setenv('DECLOUD_BACKUP_MAX_MB', '300')
    assert put(client, 'Camera/video.mp4', b'v' * (101 * 1024 * 1024)).status_code == 200      # > the 100 MB cap other routes keep
    assert (bk / 'Camera' / 'video.mp4').stat().st_size == 101 * 1024 * 1024
