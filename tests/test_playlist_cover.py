"""A playlist can have a picture of its own: an uploaded photo or a Spotify playlist link, kept as a square JPEG and served as the playlist's cover."""
import io

import pytest
from PIL import Image

import routes.music as music


def png(w, h, colour=(200, 30, 30)):
    b = io.BytesIO()
    Image.new('RGB', (w, h), colour).save(b, 'PNG')
    return b.getvalue()


@pytest.fixture
def pl(monkeypatch, tmp_path):
    monkeypatch.setattr(music, '_ART_DIR', tmp_path / 'art')
    monkeypatch.setattr(music, '_nd_conf', lambda: ('http://nd.invalid/navidrome', 'u', 'p'))
    known = {'p1'}

    def fake_get(endpoint, **kw):
        if endpoint == 'getPlaylist' and kw.get('id') not in known:
            raise RuntimeError('not found')
        return {'playlist': {}}
    monkeypatch.setattr(music, '_nd_get', fake_get)
    monkeypatch.setattr(music, '_invalidate', lambda: None)
    return tmp_path / 'art'


def upload(client, headers, pid, data, name='x.png'):
    return client.post(f'/api/music/playlist/{pid}/cover', data={'file': (io.BytesIO(data), name)}, headers=headers, content_type='multipart/form-data')


def test_uploaded_photo_becomes_a_square_cover_that_is_served(client, auth_headers, pl):
    r = upload(client, auth_headers, 'p1', png(400, 200))
    assert r.status_code == 200 and r.get_json()['v'] > 0
    assert Image.open(pl / 'pl_p1-avatar.jpg').size == (200, 200)             # cut square from the middle
    got = client.get('/api/music/artwork/pl-p1_abc123', query_string={'size': 100}, headers=auth_headers)    # Navidrome's id for it carries a suffix
    assert got.status_code == 200 and got.mimetype == 'image/jpeg' and Image.open(io.BytesIO(got.data)).size[0] <= 100


def test_not_a_picture_unknown_playlist_and_oversize_are_refused(client, auth_headers, pl, monkeypatch):
    assert upload(client, auth_headers, 'p1', b'this is not an image').status_code == 400
    assert upload(client, auth_headers, 'nope', png(10, 10)).status_code == 404
    monkeypatch.setattr(music, '_PL_COVER_MAX', 100)
    assert upload(client, auth_headers, 'p1', png(50, 50)).status_code == 413
    assert not (pl / 'pl_p1-avatar.jpg').exists()


def test_spotify_link_uses_the_pages_picture(client, auth_headers, pl, monkeypatch):
    seen = {}
    monkeypatch.setattr(music, '_spotify_page', lambda sid, kind='artist': seen.update(sid=sid, kind=kind) or {'avatar': 'https://i.scdn.co/image/abc', 'name': 'x'})
    monkeypatch.setattr(music, '_sc_fetch_image', lambda url, hosts=None: seen.update(url=url, hosts=hosts) or png(300, 300))
    r = client.post('/api/music/playlist/p1/cover', json={'link': 'https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=zz'}, headers=auth_headers)
    assert r.status_code == 200 and seen['sid'] == '37i9dQZF1DXcBWIGoYBM5M' and seen['kind'] == 'playlist' and seen['hosts'] == ('.scdn.co',)
    assert (pl / 'pl_p1-avatar.jpg').exists()
    bad = client.post('/api/music/playlist/p1/cover', json={'link': 'https://example.com/playlist/x'}, headers=auth_headers)
    assert bad.status_code == 400


def test_remove_and_delete_take_the_picture_away(client, auth_headers, pl):
    upload(client, auth_headers, 'p1', png(100, 100))
    client.get('/api/music/artwork/pl-p1', query_string={'size': 48}, headers=auth_headers)
    assert client.post('/api/music/playlist/p1/cover/clear', headers=auth_headers).status_code == 200
    assert not (pl / 'pl_p1-avatar.jpg').exists() and not list((pl / '.thumbs').glob('pl_p1-*'))
    upload(client, auth_headers, 'p1', png(100, 100))
    assert client.post('/api/music/playlist/p1/delete', headers=auth_headers).status_code == 200
    assert not (pl / 'pl_p1-avatar.jpg').exists()
    assert client.post('/api/music/playlist/p1/cover', json={'link': 'x'}).status_code in (401, 403)


def test_overrides_list_the_playlist_picture_so_the_app_can_tag_its_url(client, auth_headers, pl):
    upload(client, auth_headers, 'p1', png(100, 100))
    assert 'pl_p1' in client.get('/api/music/art/overrides', headers=auth_headers).get_json()['v']
