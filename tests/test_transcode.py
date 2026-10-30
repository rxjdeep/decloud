"""Streaming quality: lower-bitrate MP3 copies, cached, served with Range so seeking works."""
import pytest

import routes.music as music


class _Up:
    status_code = 200
    headers = {'Content-Type': 'audio/mpeg'}

    def __init__(self, body):
        self.body = body

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]

    def close(self):
        pass


@pytest.fixture
def nd(monkeypatch, tmp_path):
    calls = []
    body = bytes(range(256)) * 200                       # 51,200 bytes
    monkeypatch.setattr(music, '_nd_conf', lambda: ('http://nd.invalid/navidrome', 'u', 'p'))
    monkeypatch.setattr(music, '_TC_DIR', tmp_path / 'tc')

    def fake_get(url, params=None, **kw):
        calls.append(dict(params))
        return _Up(body)
    monkeypatch.setattr(music.requests, 'get', fake_get)
    return calls, body


def test_quality_copy_is_made_once_and_can_be_seeked(client, auth_headers, nd):
    calls, body = nd
    r = client.get('/api/music/stream/abc123', query_string={'q': 128}, headers=auth_headers)
    assert r.status_code == 200 and r.data == body and r.mimetype == 'audio/mpeg'
    assert calls[0]['maxBitRate'] == 128 and calls[0]['format'] == 'mp3'
    r2 = client.get('/api/music/stream/abc123', query_string={'q': 128}, headers={**auth_headers, 'Range': 'bytes=1000-1099'})
    assert r2.status_code == 206 and r2.data == body[1000:1100]
    assert len(calls) == 1                               # the second request came from the cache


def test_unknown_quality_is_ignored(client, auth_headers, nd):
    calls, _ = nd
    client.get('/api/music/stream/abc123', query_string={'q': 7}, headers=auth_headers)
    assert all(c.get('format') == 'raw' for c in calls if c.get('id') == 'abc123')          # (the library is asked for too, to see what kind of file it is)


def test_cache_is_trimmed(client, auth_headers, nd, monkeypatch):
    monkeypatch.setenv('RECLMR_TRANSCODE_CACHE_MB', '0')
    client.get('/api/music/stream/one', query_string={'q': 64}, headers=auth_headers)
    client.get('/api/music/stream/two', query_string={'q': 64}, headers=auth_headers)
    left = [f.name for f in music._TC_DIR.glob('*.mp3')]
    assert left == ['two-64.mp3']                          # the older copy went, the one just made stays


def test_prewarm_needs_login_and_a_valid_rate(client, auth_headers, nd):
    assert client.post('/api/music/prewarm', json={'id': 'x', 'q': 128}).status_code in (401, 403)
    assert client.post('/api/music/prewarm', json={'id': 'x', 'q': 5}, headers=auth_headers).status_code == 400
    assert client.post('/api/music/prewarm', json={'id': 'x', 'q': 128}, headers=auth_headers).status_code == 202


def test_session_keeps_playing_context():
    from routes import music
    assert music._clean_ctx({'cx': {'k': 'playlist', 'v': 'abc'}}) == {'k': 'playlist', 'v': 'abc'}
    assert music._clean_ctx({'cx': {'k': 'evil', 'v': 'abc'}}) is None
    assert music._clean_ctx({'cx': {'k': 'album', 'v': 5}}) is None
    assert music._clean_ctx({}) is None
