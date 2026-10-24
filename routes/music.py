"""Music player routes."""
from flask import Blueprint, jsonify, request, send_file, Response, stream_with_context
import subprocess
from pathlib import Path
from shared import MUSIC_EXTS
import hashlib
import json
import secrets
import datetime
import requests
import os
import re
import threading
import time

import shared
from shared import limiter

bp = Blueprint('music', __name__)

# Cache directory for extracted + cropped artwork.
# Resolved at request time so path changes apply immediately.
def _artwork_cache():
    cache = shared.MUSIC_DIR / '.artwork_cache'
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError):
        pass  # read-only fs. Music app will show empty state
    return cache


def _artwork_cache_path(filename):
    """Return cached square artwork path, creating it if needed."""
    key = hashlib.md5(filename.encode()).hexdigest()[:12]
    return _artwork_cache() / f'{key}.png'


@bp.route('/api/music/list')
def music_list():
    """List all music files in the music directory."""
    songs = []
    if shared.MUSIC_DIR.exists():
        for f in sorted(shared.MUSIC_DIR.iterdir()):
            if f.suffix.lower() in MUSIC_EXTS:
                size_mb = f.stat().st_size / (1024 * 1024)
                songs.append({
                    'name': f.stem,
                    'filename': f.name,
                    'ext': f.suffix.lower().lstrip('.'),
                    'size_mb': round(size_mb, 1),
                })
    return jsonify(songs)


def _safe_music_path(filename):
    """Resolve a music file path and ensure it stays within MUSIC_DIR."""
    base = shared.MUSIC_DIR.resolve()
    candidate = (shared.MUSIC_DIR / filename).resolve()
    try:
        if candidate.is_relative_to(base):
            return candidate
    except AttributeError:
        if os.path.commonpath([str(base), str(candidate)]) == str(base):
            return candidate
    return None


def _file_stream(filename):
    """Stream a music file. Uses Flask send_file which handles
    HTTP Range requests natively for fast seeking on iOS."""
    filepath = _safe_music_path(filename)
    if not filepath or not filepath.is_file():
        return jsonify({'error': 'Not found'}), 404

    mime_types = {
        '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.flac': 'audio/flac',
        '.ogg': 'audio/ogg', '.m4a': 'audio/mp4', '.aac': 'audio/aac',
    }
    mime = mime_types.get(filepath.suffix.lower(), 'application/octet-stream')

    resp = send_file(str(filepath), mimetype=mime)
    resp.headers['Accept-Ranges'] = 'bytes'
    return resp


def _file_artwork(filename):
    """Extract embedded artwork, crop to square, cache it."""
    filepath = _safe_music_path(filename)
    if not filepath or not filepath.is_file():
        return jsonify({'error': 'Not found'}), 404

    cache_path = _artwork_cache_path(filename)
    none_marker = cache_path.with_suffix('.none')

    def _art_response():
        resp = send_file(str(cache_path), mimetype='image/png')
        # /api/ responses default to no-store; artwork is immutable and heavy
        resp.headers['Cache-Control'] = 'private, max-age=86400'
        return resp

    # Serve from cache if exists
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return _art_response()
    if none_marker.exists():
        resp = jsonify({'error': 'No artwork'})
        resp.headers['Cache-Control'] = 'private, max-age=3600'
        return resp, 404

    # Extract + crop to square in one ffmpeg pass (first video/cover stream)
    try:
        subprocess.run(
            [
                'ffmpeg', '-y', '-i', str(filepath),
                '-map', '0:v:0',
                '-vf', 'crop=min(iw\\,ih):min(iw\\,ih),scale=256:256',
                '-frames:v', '1',
                str(cache_path),
            ],
            capture_output=True, timeout=8
        )
        if cache_path.exists() and cache_path.stat().st_size > 0:
            return _art_response()
        try:
            cache_path.unlink()
        except OSError:
            pass
        try:
            none_marker.touch()
        except OSError:
            pass
        return jsonify({'error': 'No artwork'}), 404
    except Exception:
        return jsonify({'error': 'Failed'}), 500


# ─── Library index (tags, cached) ────────────────────────────────────────
# One recursive scan with mutagen, cached on disk by (mtime, size) so only new
# or changed files are re-read. Falls back to "NN - Artist - Title" filenames.
_APP_DIR = Path(__file__).resolve().parent.parent
_INDEX_FILE = _APP_DIR / 'music_index.json'
_STATE_FILE = _APP_DIR / 'music_state.json'   # favorites + play counts
_index_lock = threading.Lock()
_state_lock = threading.Lock()
_FILENAME_RE = re.compile(r'^(?:\d+\s*-\s*)?(.+?)\s+-\s+(.+)$')


def _first(tags, key):
    v = tags.get(key) if tags else None
    return (v[0] if isinstance(v, list) and v else v or '') if tags else ''


def _read_track(path, rel):
    title = artist = album = genre = year = ''
    track = 0
    dur = 0.0
    try:
        import mutagen
        m = mutagen.File(str(path), easy=True)
        if m is not None:
            dur = float(getattr(m.info, 'length', 0) or 0)
            title = str(_first(m.tags, 'title')).strip()
            artist = str(_first(m.tags, 'artist')).strip()
            album = str(_first(m.tags, 'album')).strip()
            genre = str(_first(m.tags, 'genre')).strip()
            year = str(_first(m.tags, 'date')).strip()[:4]
            tn = str(_first(m.tags, 'tracknumber')).split('/')[0]
            track = int(tn) if tn.isdigit() else 0
    except Exception:
        pass
    if not title or not artist:
        mt = _FILENAME_RE.match(path.stem)
        if mt:
            artist = artist or mt.group(1).strip()
            title = title or mt.group(2).strip()
        else:
            title = title or path.stem
    # "Various Artists"-style albums keep per-track artist; fine as is.
    return {'f': rel, 't': title, 'ar': artist or 'Unknown Artist', 'al': album,
            'g': genre, 'y': year, 'n': track, 'd': round(dur, 1)}


def _build_index():
    base = shared.MUSIC_DIR
    try:
        cache = json.loads(_INDEX_FILE.read_text()) if _INDEX_FILE.exists() else {}
    except Exception:
        cache = {}
    if cache.get('_dir') != str(base):
        cache = {'_dir': str(base)}
    out, fresh = [], {'_dir': str(base)}
    if base.exists():
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for name in files:
                p = Path(root) / name
                if p.suffix.lower() not in MUSIC_EXTS or name.startswith('.'):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                rel = str(p.relative_to(base))
                key = [st.st_mtime, st.st_size]
                hit = cache.get(rel)
                if hit and hit.get('k') == key:
                    rec = hit['r']
                else:
                    rec = _read_track(p, rel)
                rec = dict(rec, a=int(st.st_mtime))
                fresh[rel] = {'k': key, 'r': {k: v for k, v in rec.items() if k != 'a'}}
                out.append(rec)
    try:
        tmp = _INDEX_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(fresh))
        tmp.replace(_INDEX_FILE)
    except OSError:
        pass
    out.sort(key=lambda r: (r['ar'].lower(), r['al'].lower(), r['n'], r['t'].lower()))
    return out


def _load_state():
    try:
        return json.loads(_STATE_FILE.read_text())
    except Exception:
        return {'favs': [], 'plays': {}}


def _save_state(st):
    tmp = _STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(st))
    tmp.replace(_STATE_FILE)



# ─── Navidrome / Subsonic (primary metadata source) ──────────────────────
# Tags, covers, stars and play counts come from Navidrome (more accurate than
# reading files). Credentials stay server-side; streams and covers are proxied
# so the browser only ever talks to DeCloud. Without credentials the file-tag
# index above is used instead.
_nd_cache = {'ts': 0, 'data': None}
_nd_lock = threading.Lock()
_ND_TTL = 20


def _nd_conf():
    user = os.environ.get('DECLOUD_NAVIDROME_USER', '')
    pw = os.environ.get('DECLOUD_NAVIDROME_PASS', '')
    base = os.environ.get('DECLOUD_NAVIDROME_URL', 'http://127.0.0.1:4533/navidrome').rstrip('/')
    return (base, user, pw) if user and pw else None


def _nd_params(conf, extra=None):
    base, user, pw = conf
    salt = secrets.token_hex(6)
    p = {'u': user, 't': hashlib.md5((pw + salt).encode()).hexdigest(), 's': salt,
         'v': '1.16.1', 'c': 'decloud', 'f': 'json'}
    p.update(extra or {})
    return p


def _nd_get(endpoint, **params):
    conf = _nd_conf()
    if not conf:
        raise RuntimeError('Navidrome is not configured')
    r = requests.get(f'{conf[0]}/rest/{endpoint}.view', params=_nd_params(conf, params), timeout=30)
    r.raise_for_status()
    j = r.json().get('subsonic-response', {})
    if j.get('status') != 'ok':
        raise RuntimeError((j.get('error') or {}).get('message', 'Navidrome error'))
    return j


def _epoch(iso):
    if not iso:
        return 0
    try:
        return int(datetime.datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp())
    except ValueError:
        return 0


def _nd_song(s):
    path = s.get('path') or ''
    base = str(shared.MUSIC_DIR).rstrip('/') + '/'
    if path.startswith(base):
        path = path[len(base):]
    genre = s.get('genre') or (s.get('genres') or [{}])[0].get('name', '')
    d = {
        'f': s['id'], 't': s.get('title') or '', 'ar': s.get('artist') or 'Unknown Artist',
        'ai': s.get('artistId') or s.get('artist') or '', 'al': s.get('album') or '',
        'as': [[a['id'], a['name']] for a in (s.get('artists') or []) if a.get('id') and a.get('name')],   # every credited artist, not just the first
        'li': s.get('albumId') or '', 'aa': s.get('displayAlbumArtist') or s.get('albumArtist') or '',
        'y': str(s.get('year') or ''), 'g': genre, 'n': s.get('track') or 0,
        'dn': s.get('discNumber') or 1, 'd': s.get('duration') or 0,
        'a': _epoch(s.get('created')), 'c': s.get('coverArt') or '', 'p': path,
        'fav': bool(s.get('starred')), 'pc': s.get('playCount') or 0,
        'lp': _epoch(s.get('played')), 'br': s.get('bitRate') or 0, 'sx': s.get('suffix') or '',
        'sr': s.get('samplingRate') or 0, 'bd': s.get('bitDepth') or 0, 'sz': s.get('size') or 0, 'ch': s.get('channelCount') or 0,
    }
    if s.get('explicitStatus') == 'explicit':
        d['ex'] = 1                                          # the E badge
    co = [[c['artist']['id'], c['artist']['name'], str(c.get('role') or '')] for c in (s.get('contributors') or [])
          if isinstance(c.get('artist'), dict) and c['artist'].get('id') and c['artist'].get('name')]
    if co:
        d['co'] = co                                         # composer, lyricist...: [artist id, name, role], for the Credits card
    return d


def _nd_library():
    songs, off, page = [], 0, 500
    for q in ('', '""'):
        songs, off = [], 0
        while off < 200000:
            res = _nd_get('search3', query=q, artistCount=0, albumCount=0,
                          songCount=page, songOffset=off).get('searchResult3', {})
            got = res.get('song', [])
            songs.extend(got)
            if len(got) < page:
                break
            off += page
        if songs:
            break
    tracks = [_nd_song(s) for s in songs if s.get('id')]
    try:
        pls = _nd_get('getPlaylists').get('playlists', {}).get('playlist', [])
        playlists = [{'id': p['id'], 'name': p.get('name', ''), 'n': p.get('songCount', 0),
                      'c': p.get('coverArt', ''), 'd': p.get('duration', 0)} for p in pls]
    except Exception:
        playlists = []
    return {'source': 'navidrome', 'tracks': tracks, 'playlists': playlists}


def _file_library():
    with _index_lock:
        recs = _build_index()
    with _state_lock:
        st = _load_state()
    favs, plays = set(st.get('favs', [])), st.get('plays', {})
    tracks = []
    for r in recs:
        folder = r['f'].rsplit('/', 1)[0] if '/' in r['f'] else ''
        n, last = plays.get(r['f'], [0, 0])
        tracks.append({
            'f': r['f'], 't': r['t'], 'ar': r['ar'], 'ai': r['ar'], 'al': r['al'],
            'li': (folder + '||' + r['al'].lower()) if r['al'] else '', 'aa': '',
            'y': r['y'], 'g': r['g'], 'n': r['n'], 'dn': 1, 'd': r['d'], 'a': r['a'],
            'c': r['f'], 'p': r['f'], 'fav': r['f'] in favs, 'pc': n, 'lp': last,
            'br': 0, 'sx': r['f'].rsplit('.', 1)[-1],
        })
    return {'source': 'files', 'tracks': tracks, 'playlists': []}


def _library_data():
    """The whole library (Navidrome if configured, else file tags), cached briefly."""
    now = time.time()
    with _nd_lock:
        if _nd_cache['data'] and now - _nd_cache['ts'] < _ND_TTL:
            return _nd_cache['data']
    error = ''
    data = None
    if _nd_conf():
        try:
            data = _nd_library()
        except Exception as e:
            error = str(e)
    if data is None:
        data = _file_library()
        if error:
            data['error'] = 'Navidrome unavailable (' + error + '). Showing file tags'
    elif not data['tracks']:
        data['error'] = 'Navidrome returned no songs'
    if not error:
        with _nd_lock:
            _nd_cache.update(ts=now, data=data)
    return data


@bp.route('/api/music/library')
def music_library():
    """Whole library in one request."""
    return jsonify(_library_data())


@bp.route('/api/music/meta')
def music_meta():
    """Title, artist, album, length and cover id for a few tracks (used by the desktop media-widget companion)."""
    ids = [i for i in (request.args.get('ids') or '').split(',') if i][:20]
    want = set(ids)
    out = {}
    for t in _library_data().get('tracks', []):
        if t['f'] in want:
            out[t['f']] = {'t': t['t'], 'ar': t['ar'], 'al': t['al'], 'd': t['d'], 'c': t['c']}
    return jsonify(out)


def _invalidate():
    with _nd_lock:
        _nd_cache.update(ts=0, data=None)


@bp.route('/api/music/playlists', methods=['POST'])
def music_playlist_create():
    """Create a Navidrome playlist, optionally seeded with one track."""
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()[:120]
    ident = data.get('id')
    ids = data.get('ids')
    if ids is not None and (not isinstance(ids, list) or len(ids) > 5000 or any(_bad_id(i) for i in ids)):
        return jsonify({'error': 'bad request'}), 400
    if not name or (ident and _bad_id(ident)):
        return jsonify({'error': 'bad request'}), 400
    if not _nd_conf():
        return jsonify({'error': 'Playlists need Navidrome'}), 400
    try:
        params = {'name': name}
        if ids:                                             # a whole list at once (used to bring back a deleted playlist)
            params['songId'] = ids
        elif ident:
            params['songId'] = ident
        pl = _nd_get('createPlaylist', **params).get('playlist') or {}
    except Exception as e:
        return jsonify({'error': str(e)}), 502
    _invalidate()
    return jsonify({'ok': True, 'id': pl.get('id', ''), 'name': name})


@bp.route('/api/music/playlist/<pid>/delete', methods=['POST'])
def music_playlist_delete(pid):
    """Delete a playlist (only the list: the songs stay in the library)."""
    if _bad_id(pid):
        return jsonify({'error': 'bad request'}), 400
    if not _nd_conf():
        return jsonify({'error': 'Playlists need Navidrome'}), 400
    try:
        _nd_get('deletePlaylist', id=pid)
    except Exception as e:
        return jsonify({'error': str(e)}), 502
    _invalidate()
    return jsonify({'ok': True})


def _pl_entries(pid):
    return _nd_get('getPlaylist', id=pid).get('playlist', {}).get('entry', []) or []


@bp.route('/api/music/playlist/<pid>/add', methods=['POST'])
def music_playlist_add(pid):
    """Add a track unless the playlist already has it (`dup: true` then). With `index` it goes in that slot
    (clamped to the end) instead of the end, which is how an undone removal returns to where it was."""
    b = request.get_json(silent=True) or {}
    ident, index = b.get('id'), b.get('index')
    if _bad_id(pid) or _bad_id(ident) or (index is not None and (not isinstance(index, int) or isinstance(index, bool) or index < 0)):
        return jsonify({'error': 'bad id'}), 400
    if not _nd_conf():
        return jsonify({'error': 'Playlists need Navidrome'}), 400
    try:
        cur = [e.get('id') for e in _pl_entries(pid)]
        if ident in cur:
            return jsonify({'ok': True, 'dup': True})
        if index is None or index >= len(cur):
            _nd_get('updatePlaylist', playlistId=pid, songIdToAdd=ident)
            at = len(cur)
        else:
            _nd_get('createPlaylist', playlistId=pid, songId=cur[:index] + [ident] + cur[index:])   # replaces the tracks, in the new order
            at = index
    except Exception as e:
        return jsonify({'error': str(e)}), 502
    _invalidate()
    return jsonify({'ok': True, 'dup': False, 'index': at})


@bp.route('/api/music/playlist/<pid>/remove', methods=['POST'])
def music_playlist_remove(pid):
    """Remove every copy of a track from a playlist (Subsonic removes by position, so look them up first)."""
    ident = (request.get_json(silent=True) or {}).get('id')
    if _bad_id(pid) or _bad_id(ident):
        return jsonify({'error': 'bad id'}), 400
    if not _nd_conf():
        return jsonify({'error': 'Playlists need Navidrome'}), 400
    try:
        idx = [i for i, e in enumerate(_pl_entries(pid)) if e.get('id') == ident]
        if idx:
            _nd_get('updatePlaylist', playlistId=pid, songIndexToRemove=idx)
    except Exception as e:
        return jsonify({'error': str(e)}), 502
    _invalidate()
    return jsonify({'ok': True, 'removed': len(idx), 'indices': idx})


@bp.route('/api/music/playlist/<pid>/order', methods=['POST'])
def music_playlist_order(pid):
    """Save an edited playlist: the tracks in their new order, with any the user removed left out.
    `base` is how many tracks the page started from. Refused (409) if the playlist changed since, or if
    `ids` would add something it doesn't have, so a stale page can never drop or resurrect tracks."""
    b = request.get_json(silent=True) or {}
    ids, base = b.get('ids'), b.get('base')
    if (_bad_id(pid) or not isinstance(ids, list) or len(ids) > 5000 or any(_bad_id(i) for i in ids)
            or not isinstance(base, int) or isinstance(base, bool)):
        return jsonify({'error': 'bad request'}), 400
    if not _nd_conf():
        return jsonify({'error': 'Playlists need Navidrome'}), 400
    try:
        from collections import Counter
        cur = [e.get('id') for e in _pl_entries(pid)]
        if len(cur) != base or Counter(ids) - Counter(cur):
            return jsonify({'error': 'The playlist changed elsewhere', 'stale': True}), 409
        if not ids:
            if cur:
                _nd_get('updatePlaylist', playlistId=pid, songIndexToRemove=list(range(len(cur))))
        else:
            _nd_get('createPlaylist', playlistId=pid, songId=ids)      # with playlistId this replaces the tracks of that playlist
    except Exception as e:
        return jsonify({'error': str(e)}), 502
    _invalidate()
    return jsonify({'ok': True})


@bp.route('/api/music/membership')
def music_membership():
    """Ids of the playlists that contain a track."""
    ident = request.args.get('id', '')
    if _bad_id(ident):
        return jsonify({'error': 'bad id'}), 400
    if not _nd_conf():
        return jsonify({'playlists': []})
    try:
        pls = _nd_get('getPlaylists').get('playlists', {}).get('playlist', []) or []
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            found = list(ex.map(lambda p: p['id'] if any(e.get('id') == ident for e in _pl_entries(p['id'])) else None, pls))
    except Exception as e:
        return jsonify({'error': str(e)}), 502
    return jsonify({'playlists': [i for i in found if i]})


@bp.route('/api/music/playlist/<pid>')
def music_playlist(pid):
    try:
        entries = _nd_get('getPlaylist', id=pid).get('playlist', {}).get('entry', [])
        return jsonify({'ids': [e['id'] for e in entries if e.get('id')]})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


def _bad_id(ident):
    return not isinstance(ident, str) or not ident or len(ident) > 600


@bp.route('/api/music/favorite', methods=['POST'])
def music_favorite():
    data = request.get_json(silent=True) or {}
    ident = data.get('id') or data.get('filename')
    if _bad_id(ident):
        return jsonify({'error': 'bad id'}), 400
    on = bool(data.get('on'))
    if _nd_conf():
        try:
            _nd_get('star' if on else 'unstar', id=ident)
        except Exception as e:
            return jsonify({'error': str(e)}), 502
        _invalidate()
        return jsonify({'ok': True, 'on': on})
    if not _safe_music_path(ident):
        return jsonify({'error': 'bad id'}), 400
    with _state_lock:
        st = _load_state()
        favs = [f for f in st.get('favs', []) if f != ident]
        if on:
            favs.append(ident)
        st['favs'] = favs
        _save_state(st)
    _invalidate()
    return jsonify({'ok': True, 'on': on})


@bp.route('/api/music/played', methods=['POST'])
def music_played():
    data = request.get_json(silent=True) or {}
    ident = data.get('id') or data.get('filename')
    if _bad_id(ident):
        return jsonify({'error': 'bad id'}), 400
    if _nd_conf():
        try:
            _nd_get('scrobble', id=ident, submission='true', time=int(time.time() * 1000))
        except Exception as e:
            return jsonify({'error': str(e)}), 502
        _invalidate()
        return jsonify({'ok': True})
    if not _safe_music_path(ident):
        return jsonify({'error': 'bad id'}), 400
    with _state_lock:
        st = _load_state()
        n, _ = st.setdefault('plays', {}).get(ident, [0, 0])
        st['plays'][ident] = [n + 1, int(time.time())]
        _save_state(st)
    _invalidate()
    return jsonify({'ok': True})


_PASS_HEADERS = ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges', 'Last-Modified', 'ETag')


def _nd_proxy(endpoint, params, extra_headers=None, cache=None, ctype=None):
    """Stream a Navidrome response through DeCloud (Range-aware)."""
    conf = _nd_conf()
    headers = dict(extra_headers or {})
    rng = request.headers.get('Range')
    if rng:
        headers['Range'] = rng
    try:
        up = requests.get(f'{conf[0]}/rest/{endpoint}.view', params=_nd_params(conf, params),
                          headers=headers, stream=True, timeout=(5, 60))
    except requests.RequestException:
        return jsonify({'error': 'Navidrome unreachable'}), 502
    ctype = up.headers.get('Content-Type', '')
    if up.status_code >= 400 or 'json' in ctype or 'xml' in ctype:  # Subsonic reports errors as JSON/XML
        up.close()
        return jsonify({'error': 'Not found'}), 404

    def gen():
        try:
            for chunk in up.iter_content(65536):
                yield chunk
        finally:
            up.close()

    resp = Response(stream_with_context(gen()), status=up.status_code)
    for h in _PASS_HEADERS:
        if h in up.headers:
            resp.headers[h] = up.headers[h]
    if ctype:
        resp.headers['Content-Type'] = ctype
    if cache:
        resp.headers['Cache-Control'] = cache
    return resp


@bp.route('/api/music/stream/<path:ident>')
def music_stream(ident):
    if _nd_conf():
        if _bad_id(ident):
            return jsonify({'error': 'bad id'}), 400
        if request.args.get('fb'):  # browser could not decode the original: transcode (no seeking)
            return _nd_proxy('stream', {'id': ident, 'format': 'mp3', 'maxBitRate': 320}, ctype='audio/mpeg')
        # Navidrome labels WAV audio/x-wav; browsers only reliably accept audio/wav
        ctype = 'audio/wav' if request.args.get('x') == 'wav' else None
        return _nd_proxy('stream', {'id': ident, 'format': 'raw'}, ctype=ctype)
    return _file_stream(ident)


_artist_cache = {}


def _plain(text):
    """Navidrome's biography is HTML (a 'Read more on Last.fm' link, entities): reduce it to plain text."""
    import html as _html, re as _re
    t = _re.sub(r'<[^>]+>', ' ', _html.unescape(text or ''))
    t = _re.sub(r'\s*Read more on Last\.fm\.?\s*$', '', _re.sub(r'\s+', ' ', t).strip())
    return t


@bp.route('/api/music/artist/<aid>')
def music_artist_info(aid):
    """About text and similar artists for the artist page. Images come from /api/music/artwork/ar-<id>."""
    if _bad_id(aid):
        return jsonify({'error': 'bad id'}), 400
    if not _nd_conf():
        return jsonify({'bio': '', 'similar': []})
    now = time.time()
    hit = _artist_cache.get(aid)
    if hit and now - hit[0] < 3600:
        return jsonify(hit[1])
    try:
        info = _nd_get('getArtistInfo2', id=aid, count=12).get('artistInfo2', {}) or {}
    except Exception as e:
        return jsonify({'error': str(e)}), 502
    out = {'bio': _plain(info.get('biography', ''))[:2500],
           'similar': [{'id': x['id'], 'name': x.get('name', '')} for x in (info.get('similarArtist') or []) if x.get('id')][:12]}
    if len(_artist_cache) > 500:
        _artist_cache.clear()
    _artist_cache[aid] = (now, out)
    return jsonify(out)


# Navidrome serves a generic stand-in picture for an artist it found no photo for: the same few bytes for every such artist. At 48 px they are
# these two (95 and 14 of the artists in one library). Treating them as "no photo" lets the page fall back to the album cover instead of showing a generic image.
_AR_PLACEHOLDER = {'fcec14e686ad5159a8ed7c51936116d7', '00b7856aa138fc3996c344801c56ed3b'} | {
    h.strip().lower() for h in os.environ.get('DECLOUD_PLACEHOLDER_ARTIST_MD5', '').split(',') if h.strip()}
_ar_photo = {}


def _artist_has_photo(aid):
    if aid in _ar_photo:
        return _ar_photo[aid]
    conf = _nd_conf()
    ok = True
    try:
        r = requests.get(f'{conf[0]}/rest/getCoverArt.view', params=_nd_params(conf, {'id': 'ar-' + aid, 'size': 48}), timeout=(5, 20))
        ok = r.status_code == 200 and 'image' in r.headers.get('Content-Type', '') and hashlib.md5(r.content).hexdigest() not in _AR_PLACEHOLDER
    except requests.RequestException:
        return True                                       # could not tell: show what Navidrome has, and do not remember the guess
    if len(_ar_photo) > 3000:
        _ar_photo.clear()
    _ar_photo[aid] = ok
    return ok


@bp.route('/api/music/artwork/<path:ident>')
@limiter.exempt
def music_artwork(ident):
    if _nd_conf():
        if _bad_id(ident):
            return jsonify({'error': 'bad id'}), 400
        if ident.startswith('ar-'):
            own = _artist_art_file(ident[3:].split('_')[0], 'banner' if request.args.get('banner') else 'avatar')
            if own:
                size = min(1200, max(48, request.args.get('size', 300, type=int)))
                resp = send_file(str(_art_thumb(own, size)), mimetype='image/jpeg', conditional=True)
                resp.headers['Cache-Control'] = 'private, no-cache'
                return resp
        if ident.startswith('ar-') and not _artist_has_photo(ident[3:].split('_')[0]):
            resp = jsonify({'error': 'no photo'})
            resp.status_code = 404
            resp.headers['Cache-Control'] = 'private, max-age=3600'
            return resp
        size = min(1200, max(48, request.args.get('size', 300, type=int)))
        return _nd_proxy('getCoverArt', {'id': ident, 'size': size}, cache='private, max-age=300' if ident.startswith('ar-') else 'private, max-age=86400')
    return _file_artwork(ident)


# ─── Artist photo / banner from SoundCloud (for artists Navidrome finds nothing for) ───
# You pick the right profile (search results or a pasted link); only that profile's avatar and banner are downloaded and kept in artist_art/.
# SoundCloud has no public API: profile pages carry their data in the HTML, and search needs the public client_id its own web player uses.
_ART_DIR = _APP_DIR / 'artist_art'
_SC_UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0'}
_sc_cid = {'v': None}


def _artist_art_file(aid, kind):
    aid = re.sub(r'[^A-Za-z0-9_-]', '_', aid or '')
    for k in ([kind, 'avatar'] if kind == 'banner' else [kind]):
        f = _ART_DIR / f'{aid}-{k}.jpg'
        if f.exists():
            return f
    return None


def _art_thumb(src, size):
    """The saved picture scaled down to `size` px (cached next to it), so list pages do not download full-size photos."""
    size = (size + 99) // 100 * 100                      # a few steps, so the cache stays small
    try:
        from PIL import Image
        out = _ART_DIR / '.thumbs' / f'{src.stem}-{size}-{int(src.stat().st_mtime)}.jpg'
        if not out.exists():
            im = Image.open(src)
            if max(im.size) <= size:
                return src
            out.parent.mkdir(exist_ok=True)
            for old in out.parent.glob(f'{src.stem}-*.jpg'):
                old.unlink(missing_ok=True)
            im.convert('RGB').resize((round(im.width * size / max(im.size)), round(im.height * size / max(im.size))), Image.LANCZOS).save(out, 'JPEG', quality=84)
        return out
    except Exception:
        return src


def _sc_client_id(refresh=False):
    if _sc_cid['v'] and not refresh:
        return _sc_cid['v']
    html = requests.get('https://soundcloud.com/', headers=_SC_UA, timeout=15).text
    for u in reversed(re.findall(r'https://a-v2\.sndcdn\.com/assets/[^"\']+\.js', html)):
        m = re.search(r'client_id:"([A-Za-z0-9]{32})"', requests.get(u, headers=_SC_UA, timeout=20).text)
        if m:
            _sc_cid['v'] = m.group(1)
            return m.group(1)
    raise RuntimeError('SoundCloud client id not found')


@bp.route('/api/music/sc/search')
def music_sc_search():
    q = (request.args.get('q') or '').strip()[:100]
    if not q:
        return jsonify([])
    for attempt in (0, 1):
        try:
            r = requests.get('https://api-v2.soundcloud.com/search/users', headers=_SC_UA, timeout=15,
                             params={'q': q, 'limit': 8, 'client_id': _sc_client_id(attempt == 1)})
            if r.status_code in (401, 403) and attempt == 0:
                continue
            r.raise_for_status()
            break
        except (requests.RequestException, RuntimeError) as e:
            if attempt == 1:
                return jsonify({'error': 'SoundCloud lookup failed: ' + str(e)[:120]}), 502
    return jsonify([{'name': u.get('username', ''), 'permalink': u.get('permalink', ''), 'followers': u.get('followers_count', 0),
                     'tracks': u.get('track_count', 0), 'avatar': u.get('avatar_url', '')}
                    for u in r.json().get('collection', []) if u.get('permalink')])


def _sc_fetch_image(url, hosts=('.sndcdn.com',)):
    from urllib.parse import urlparse
    from PIL import Image
    import io
    host = urlparse(url).hostname or ''
    if not (url.startswith('https://') and host.endswith(hosts)):
        raise ValueError('unexpected image host')
    r = requests.get(url, headers=_SC_UA, timeout=20, stream=True)
    r.raise_for_status()
    data = r.raw.read(12 * 1024 * 1024 + 1, decode_content=True)
    if len(data) > 12 * 1024 * 1024:
        raise ValueError('image too large')
    im = Image.open(io.BytesIO(data)).convert('RGB')
    out = io.BytesIO()
    im.save(out, 'JPEG', quality=88)
    return out.getvalue()


def _sc_import(aid, permalink):
    """Download a profile's avatar/banner into artist_art/. Returns (user dict, ['avatar','banner'] that were saved)."""
    html = requests.get(f'https://soundcloud.com/{permalink}', headers=_SC_UA, timeout=20).text
    hy = re.search(r'__sc_hydration = (\[.*?\]);</script>', html, re.S)
    user = next((x['data'] for x in json.loads(hy.group(1)) if x.get('hydratable') == 'user'), None) if hy else None
    if not user:
        raise LookupError(permalink)
    got = []
    _ART_DIR.mkdir(exist_ok=True)
    av = user.get('avatar_url') or ''
    if av and 'default_avatar' not in av:
        (_ART_DIR / f'{aid}-avatar.jpg').write_bytes(_sc_fetch_image(av.replace('-large.', '-t500x500.')))
        got.append('avatar')
    vis = user.get('visual_url') or ''
    if vis:
        (_ART_DIR / f'{aid}-banner.jpg').write_bytes(_sc_fetch_image(vis))
        got.append('banner')
    return user, got


@bp.route('/api/music/sc/apply', methods=['POST'])
def music_sc_apply():
    d = request.get_json(silent=True) or {}
    aid = re.sub(r'[^A-Za-z0-9_-]', '_', str(d.get('aid') or ''))
    ref = str(d.get('profile') or '').strip()
    m = re.match(r'^(?:https?://(?:www\.|m\.)?soundcloud\.com/)?([A-Za-z0-9_-]{2,60})/?(?:[?#].*)?$', ref)
    if not aid or not m:
        return jsonify({'error': 'Give a soundcloud.com profile link'}), 400
    try:
        user, got = _sc_import(aid, m.group(1))
    except LookupError:
        return jsonify({'error': 'Profile not found'}), 404
    except Exception as e:
        return jsonify({'error': 'Import failed: ' + str(e)[:120]}), 502
    if not got:
        return jsonify({'error': (user.get('username') or 'That profile') + ' has no photo or banner'}), 404
    return jsonify({'ok': True, 'name': user.get('username', ''), 'got': got})


def _spotify_page(sid):
    """Public artist page: name, photo (og:image) and the wide header image, if it has one."""
    html = requests.get(f'https://open.spotify.com/artist/{sid}', headers={'User-Agent': 'Mozilla/5.0'}, timeout=20).text   # a browser-like UA gets a page without the preview tags
    img = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    nm = re.search(r'<meta property="og:title" content="([^"]*)"', html)
    ds = re.search(r'<meta property="og:description" content="([^"]*)"', html)
    hdr = re.search(r'https://i\.scdn\.co/image/ab6742d30000(?:52|53)b7([0-9a-f]+)', html)
    import html as _h
    return {'name': _h.unescape(nm.group(1)) if nm else '', 'avatar': img.group(1) if img else '',
            'info': _h.unescape(ds.group(1)) if ds else '',
            'banner': ('https://i.scdn.co/image/ab6742d3000053b7' + hdr.group(1)) if hdr else ''}


def _web_spotify_ids(q):
    """open.spotify.com artist ids found by web-searching `q` (Yahoo, then Bing; neither needs a key). Up to 5, best-ranked first."""
    import base64, urllib.parse
    ua = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0', 'Accept-Language': 'en'}
    term = q + ' spotify artist site:open.spotify.com'
    found = []
    for url, param in (('https://search.yahoo.com/search', 'p'), ('https://www.bing.com/search', 'q')):
        try:
            html = requests.get(url, params={param: term}, headers=ua, timeout=20).text.replace('&amp;', '&')
        except requests.RequestException:
            continue
        text = urllib.parse.unquote(html)
        for b in re.findall(r'[?&]u=a1([A-Za-z0-9_-]+)', html):          # Bing wraps result links in base64
            try:
                text += ' ' + base64.urlsafe_b64decode(b + '=' * (-len(b) % 4)).decode('utf8', 'ignore')
            except ValueError:
                pass
        for sid in re.findall(r'open\.spotify\.com/(?:intl-[a-z]+/)?artist/([A-Za-z0-9]{22})', text):
            if sid not in found:
                found.append(sid)
        if len(found) >= 3:
            break
    return found[:5]


@bp.route('/api/music/art/spotify/search')
def music_spotify_search():
    """Find Spotify artist pages for a name via a web search (Spotify's own search needs an API key), then read each public page."""
    q = (request.args.get('q') or '').strip()[:100]
    if not q:
        return jsonify([])
    ids = _web_spotify_ids(q)
    if not ids:
        return jsonify({'error': 'The web search found no Spotify page. Try again, or paste the artist link.'}), 502
    from concurrent.futures import ThreadPoolExecutor

    def one(sid):
        try:
            p = _spotify_page(sid)
            return {'id': sid, 'name': p['name'], 'info': p['info'], 'avatar': p['avatar'], 'banner': bool(p['banner'])}
        except Exception:
            return None
    with ThreadPoolExecutor(5) as ex:
        out = [x for x in ex.map(one, ids) if x and x['name']]
    low = q.lower()
    out.sort(key=lambda x: x['name'].lower() != low)       # exact name first, the search engine's order otherwise
    return jsonify(out)


@bp.route('/api/music/art/deezer/search')
def music_deezer_search():
    """Deezer's public artist search (no key): finds photos for many small artists."""
    q = (request.args.get('q') or '').strip()[:100]
    if not q:
        return jsonify([])
    try:
        r = requests.get('https://api.deezer.com/search/artist', params={'q': q, 'limit': 8}, headers=_SC_UA, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        return jsonify({'error': 'Deezer lookup failed: ' + str(e)[:120]}), 502
    return jsonify([{'id': str(a['id']), 'name': a.get('name', ''), 'fans': a.get('nb_fan', 0), 'avatar': a.get('picture_medium', '')}
                    for a in r.json().get('data', []) if a.get('id')])


@bp.route('/api/music/art/apply', methods=['POST'])
def music_art_apply():
    """Save an artist photo from Deezer (id from the search) or a pasted Spotify artist link (its page's public og:image)."""
    d = request.get_json(silent=True) or {}
    aid = re.sub(r'[^A-Za-z0-9_-]', '_', str(d.get('aid') or ''))
    src, ref = str(d.get('source') or ''), str(d.get('ref') or '').strip()
    banner = False
    if not aid:
        return jsonify({'error': 'bad request'}), 400
    try:
        if src == 'deezer' and re.fullmatch(r'\d{1,12}', ref):
            a = requests.get(f'https://api.deezer.com/artist/{ref}', headers=_SC_UA, timeout=15).json()
            url, name, hosts = a.get('picture_xl') or '', a.get('name', ''), ('.dzcdn.net',)
            if 'images/artist//' in url:
                url = ''
        elif src == 'spotify':
            m = re.search(r'artist[/:]([A-Za-z0-9]{22})', ref) or re.fullmatch(r'()([A-Za-z0-9]{22})', ref)
            sid = m.group(m.lastindex) if m else None
            if not sid:
                return jsonify({'error': 'Give an open.spotify.com/artist/… link'}), 400
            info = _spotify_page(sid)
            url, name, hosts = info['avatar'], info['name'], ('.scdn.co',)
            if info['banner']:
                _ART_DIR.mkdir(exist_ok=True)
                (_ART_DIR / f'{aid}-banner.jpg').write_bytes(_sc_fetch_image(info['banner'], hosts))
                banner = True
        else:
            return jsonify({'error': 'bad request'}), 400
        if not url:
            return jsonify({'error': (name or 'That artist') + ' has no photo'}), 404
        _ART_DIR.mkdir(exist_ok=True)
        (_ART_DIR / f'{aid}-avatar.jpg').write_bytes(_sc_fetch_image(url, hosts))
    except Exception as e:
        return jsonify({'error': 'Import failed: ' + str(e)[:120]}), 502
    return jsonify({'ok': True, 'name': name, 'got': ['photo'] + (['banner'] if banner else [])})


@bp.route('/api/music/art/overrides')
def music_art_overrides():
    """Artists with a saved photo/banner: {'v': {artist id: last-changed time}, 'b': [ids that have a real banner]}.
    The app tags picture URLs with the time so a stale cached picture is never shown."""
    v, b = {}, []
    if _ART_DIR.exists():
        for f in _ART_DIR.glob('*-*.jpg'):
            aid, kind = f.name[:-4].rsplit('-', 1)
            v[aid] = max(v.get(aid, 0), int(f.stat().st_mtime))
            if kind == 'banner':
                b.append(aid)
    return jsonify({'v': v, 'b': b})


@bp.route('/api/music/sc/clear', methods=['POST'])
def music_sc_clear():
    aid = re.sub(r'[^A-Za-z0-9_-]', '_', str((request.get_json(silent=True) or {}).get('aid') or ''))
    for k in ('avatar', 'banner'):
        (_ART_DIR / f'{aid}-{k}.jpg').unlink(missing_ok=True)
    return jsonify({'ok': True})


# ─── Lyrics: from Navidrome (tags / .lrc files) first, then from LRCLIB, a free lyrics site ───
# LRCLIB gets the artist, title, album and length of the song being played; what it returns is cached in lyrics_cache/ so each song is asked once.
_lyr_cache = {}
_LYR_DIR = _APP_DIR / 'lyrics_cache'
_LRC_RE = re.compile(r'^\s*((?:\[\d+:\d+(?:[.:]\d+)?\])+)(.*)$')


def _parse_lrc(text):
    lines = []
    for raw in (text or '').splitlines():
        m = _LRC_RE.match(raw)
        if not m:
            continue
        v = re.sub(r'<\d+:\d+(?:[.:]\d+)?>', '', re.sub(r'^\s*v\d:\s*', '', m.group(2))).strip()
        for mm, ss in re.findall(r'\[(\d+):(\d+(?:[.:]\d+)?)\]', m.group(1)):
            lines.append([int((int(mm) * 60 + float(ss.replace(':', '.'))) * 1000), v])
    lines.sort(key=lambda x: x[0])
    return [l for l in lines if l[1]] or []


def _lrclib(title, artist, album, dur):
    """Best match from lrclib.net: synced lyrics if it has them, else plain ones. [] when nothing fits.
    Songs tagged with an English title often have both the sung lyrics and an English translation on the site (same times, same length);
    when a result carries an original-script title (スローモーションリプレイ), the sung-language lyrics win over the translation."""
    hdr = {'User-Agent': 'DeCloud (self-hosted music player)'}
    hits, ok = [], False
    for url, params in (('https://lrclib.net/api/get', {'track_name': title, 'artist_name': artist, 'album_name': album, 'duration': int(dur)}),
                        ('https://lrclib.net/api/search', {'track_name': title, 'artist_name': artist}),
                        ('https://lrclib.net/api/search', {'q': artist + ' ' + title})):          # the free-text search also finds entries filed under the original-script title
        try:
            r = requests.get(url, params=params, headers=hdr, timeout=8)
            if r.status_code >= 500:                          # the site is busy: wait a moment and ask once more
                time.sleep(1.2)
                r = requests.get(url, params=params, headers=hdr, timeout=8)
        except requests.RequestException:
            continue
        if r.status_code in (200, 404):                       # a real answer (an overloaded site is not "no lyrics")
            ok = True
        if r.status_code == 200:
            try:
                j = r.json()
            except ValueError:
                continue
            hits += j if isinstance(j, list) else [j]
    if not ok:
        return None                                           # could not ask: do not remember an empty answer
    hits = [h for h in hits if abs((h.get('duration') or 0) - dur) <= 4]
    if any(h.get('instrumental') for h in hits) and not any(h.get('syncedLyrics') or h.get('plainLyrics') for h in hits):
        return []
    nonlatin = re.compile(r'[\u0400-\u04ff\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af\u0900-\u097f\u0a00-\u0a7f]')

    def share(txt):
        return len(nonlatin.findall(txt)) / max(1, len(re.sub(r'\W', '', txt)))
    sung_original = any(nonlatin.search(h.get('trackName') or '') for h in hits)         # a non-Latin title exists, so the song's own language is not Latin script
    cands = [(h, _parse_lrc(h.get('syncedLyrics'))) for h in hits]
    cands = [(h, ln) for h, ln in cands if ln]
    if sung_original:
        orig = [(h, ln) for h, ln in cands if share(' '.join(x[1] for x in ln)) >= 0.3]
        cands = orig or cands
    if cands:
        h, ln = min(cands, key=lambda c: abs((c[0].get('duration') or 0) - dur))
        return {'synced': True, 'lines': ln}
    for h in hits:
        plain = [[None, x.strip()] for x in (h.get('plainLyrics') or '').splitlines()]
        if any(x[1] for x in plain):
            return {'synced': False, 'lines': plain}
    return []


_JP_KANA = re.compile(r'[\u3040-\u30ff]')
_JP_ANY = re.compile(r'[\u3040-\u30ff\u4e00-\u9fff]')
_jp = {}


def _romaji_lines(lines):
    """Japanese lyrics as Hepburn romaji (janome splits the words, pykakasi spells them). Only songs with kana in them: a Chinese song stays as written.
    Each line becomes [start, romaji, original]. Returns the lines unchanged when the libraries are missing."""
    if not any(l[1] and _JP_KANA.search(l[1]) for l in lines):
        return lines
    try:
        if 'tk' not in _jp:
            from janome.tokenizer import Tokenizer
            import pykakasi
            _jp['tk'], _jp['kk'] = Tokenizer(), pykakasi.kakasi()
        tk, kk = _jp['tk'], _jp['kk']
    except Exception:
        return lines
    out = []
    for start, text in lines:
        if not text or not _JP_ANY.search(text):
            out.append([start, text])
            continue
        groups = []                                           # [kana or plain text, is Japanese]; verb endings stay glued to their verb: hoshikatta, not hoshikatsu ta
        for tok in tk.tokenize(text):
            if not _JP_ANY.search(tok.surface):
                groups.append([tok.surface, False])
                continue
            rd = tok.phonetic if tok.phonetic not in ('*', '') else tok.reading
            kana = rd if rd and rd != '*' else tok.surface
            pos = tok.part_of_speech.split(',')
            if groups and groups[-1][1] and (pos[0] == '助動詞' or (pos[0] == '動詞' and pos[1] in ('接尾', '非自立'))):
                groups[-1][0] += kana
            else:
                groups.append([kana, True])
        words = [''.join(x['hepburn'] for x in kk.convert(g)) if jp else g for g, jp in groups]
        rom = re.sub(r'\s+', ' ', ' '.join(words)).strip()
        rom = re.sub(r'\s+([,.!?)\u3001\u3002\uff01\uff1f])', r'\1', rom)
        out.append([start, rom[:1].upper() + rom[1:], text])
    return out


@bp.route('/api/music/lyrics/<tid>')
def music_lyrics(tid):
    """{synced, lines: [[start ms or null, text], ...]} for a song; empty when none can be found."""
    if _bad_id(tid):
        return jsonify({'error': 'bad id'}), 400
    hit = _lyr_cache.get(tid)
    if hit and time.time() - hit[0] < 3600:
        return jsonify(hit[1])
    out = {'synced': False, 'lines': []}
    if _nd_conf():
        try:
            lst = (_nd_get('getLyricsBySongId', id=tid).get('lyricsList') or {}).get('structuredLyrics') or []
            best = next((x for x in lst if x.get('synced')), lst[0] if lst else None)
            if best:
                off = int(best.get('offset') or 0)
                synced = bool(best.get('synced'))
                for l in best.get('line') or []:
                    v = re.sub(r'<\d+:\d+(?:[.:]\d+)?>', '', re.sub(r'^\s*v\d:\s*', '', str(l.get('value') or ''))).strip()      # "v1:" is a singer tag, <00:01.2> a per-word time; some lyric files carry both
                    if synced and not v:
                        continue
                    out['lines'].append([max(0, int(l.get('start') or 0) + off) if synced else None, v])
                out['synced'] = synced and bool(out['lines'])
        except Exception:
            return jsonify(out)                             # Navidrome hiccup: not cached, try again next time
    if not out['synced']:                                   # nothing timed in the files: ask LRCLIB (once per song)
        f = _LYR_DIR / (re.sub(r'[^A-Za-z0-9_-]', '_', tid) + '.json')
        got = None
        try:
            c = json.loads(f.read_text())
            if c.get('v') == 2 and (c.get('lines') or time.time() - c.get('at', 0) < 7 * 86400):
                got = {'synced': c.get('synced', False), 'lines': c.get('lines', [])}
        except (OSError, ValueError):
            pass
        if got is None:
            t = next((x for x in _library_data().get('tracks', []) if x['f'] == tid), None)
            if t and t.get('d'):
                artist = (t.get('as') or [[0, t.get('ar', '')]])[0][1]
                res = _lrclib(t['t'], artist, t.get('al', ''), t['d'])
                if res is None:
                    return jsonify(out)
                got = res if res else {'synced': False, 'lines': []}
                try:
                    _LYR_DIR.mkdir(exist_ok=True)
                    f.write_text(json.dumps({'v': 2, 'at': time.time(), **got}, ensure_ascii=False))
                except OSError:
                    pass
        if got and got['lines'] and (got['synced'] or not out['lines']):
            out = got
    if out['lines']:
        out = {**out, 'lines': _romaji_lines(out['lines'])}
    if len(_lyr_cache) > 400:
        _lyr_cache.clear()
    _lyr_cache[tid] = (time.time(), out)
    return jsonify(out)


# ─── Cross-device "now playing" session ──────────────────────────────────
# One shared session (queue, position, mode flags) so another device can resume
# where this one stopped. `updated` comes from the server clock, so devices with
# skewed clocks can't make stale state win. Position-only updates omit the queue.
_SESSION_FILE = _APP_DIR / 'music_session.json'
_session_lock = threading.Lock()


def _load_session():
    try:
        return json.loads(_SESSION_FILE.read_text())
    except Exception:
        return None


@bp.route('/api/music/session', methods=['GET'])
def music_session_get():
    with _session_lock:
        data = _load_session()
    return jsonify({'session': data, 'now': time.time(), 'active': _effective_active(data)})


class _Bad(Exception):
    pass


_GONE_GRACE = 8.0           # seconds a dropped device keeps its role (covers a page refresh)
_ACTIVE_IDLE = 300          # a paused device stays "active" this long, then playback is free to move
_conns = {}                 # device id -> {'ws', 'label', 'lock'}
_conn_lock = threading.RLock()
_gone = {}                  # device id -> Timer (grace period before a dropped device counts as stopped)
_pending = {}               # transfer target -> Timer


def _effective_active(sess):
    """The device that is currently outputting (or recently was), if it is still connected."""
    if not sess:
        return None
    a = sess.get('active')
    if not a or (a not in _conns and a not in _gone):     # a just-dropped device (page refresh) keeps its role briefly
        return None
    st = sess.get('state') or {}
    if not st.get('playing') and time.time() - sess.get('updated', 0) > _ACTIVE_IDLE:
        return None
    return a


def _devices(sess):
    act = _effective_active(sess)
    with _conn_lock:
        items = sorted(((d, c['label']) for d, c in _conns.items() if not c.get('ctl')), key=lambda x: (x[1], x[0]))
    seen, out = {}, []
    for d, label in items:                      # "Chrome on Linux", "Chrome on Linux (2)" ...
        seen[label] = seen.get(label, 0) + 1
        out.append({'id': d, 'label': label if seen[label] == 1 else '%s (%d)' % (label, seen[label]), 'active': d == act})
    return out


def _send(dev, obj):
    with _conn_lock:
        c = _conns.get(dev)
    if not c:
        return False
    try:
        with c['lock']:
            c['ws'].send(json.dumps(obj))
        return True
    except Exception:
        return False


def _state_msg(sess):
    return {'t': 'state', 's': sess, 'devices': _devices(sess), 'active': _effective_active(sess), 'now': time.time()}


def _broadcast(sess=None, skip=None):
    sess = sess if sess is not None else _load_session()
    msg = _state_msg(sess or {})
    with _conn_lock:
        ids = list(_conns)
    for d in ids:
        if d != skip:
            _send(d, msg)


def _validate_report(b):
    try:
        pos = float(b.get('pos', 0))
        qi = int(b.get('qi', 0))
    except (TypeError, ValueError):
        raise _Bad('bad numbers')
    if not (0 <= pos < 1e6) or qi < 0:
        raise _Bad('bad request')
    rp = b.get('rp', 'off')
    if rp not in ('off', 'all', 'one'):
        raise _Bad('bad repeat')
    q = b.get('q')
    if q is not None and (not isinstance(q, list) or not q or len(q) > 2000
                          or any(not isinstance(i, str) or not i or len(i) > 600 for i in q)):
        raise _Bad('bad queue')
    return pos, qi, rp, q


def _report(dev, label, b, force=False):
    """Merge a device's playback report into the shared session.

    Only the active device may update an active session, except that a device which starts
    playing takes over (and the previous one is told to stop). `force` is used for an
    explicit handoff, where the sender is still the active device anyway."""
    pos, qi, rp, q = _validate_report(b)
    stop = None
    with _session_lock:
        cur = _load_session() or {}
        act = _effective_active(cur)
        playing = bool(b.get('playing'))
        if act and act != dev and not playing and not force:
            return {'ok': True, 'ignored': True}        # a non-active device must not clobber the session
        st = dict(cur.get('state') or {})
        if q is not None:
            st['q'] = q
        queue = st.get('q') or []
        if qi >= len(queue):
            return {'ok': False, 'stale': True}
        if q is None and queue[qi] != b.get('cur'):
            return {'ok': False, 'stale': True}
        st.update(qi=qi, pos=round(pos, 1), playing=playing, sh=bool(b.get('sh')), rp=rp)
        if act == dev or playing:
            new_active = dev
        else:
            new_active = None                            # idle report with nobody active: store, but don't claim
        if playing and act and act != dev:
            stop = act
        out = {'state': st, 'updated': time.time(), 'dev': dev, 'label': label, 'active': new_active}
        try:
            tmp = _SESSION_FILE.with_suffix('.tmp')
            tmp.write_text(json.dumps(out))
            tmp.replace(_SESSION_FILE)
        except OSError:
            raise _Bad('could not save')
    if stop:
        _send(stop, {'t': 'cmd', 'a': 'stop', 'from': dev})
    _broadcast(out, skip=dev)
    return {'ok': True, 'updated': out['updated']}


@bp.route('/api/music/session', methods=['POST'])
def music_session_set():
    b = request.get_json(silent=True) or {}
    dev = str(b.get('dev') or '')[:60]
    if not dev:
        return jsonify({'error': 'bad request'}), 400
    try:
        return jsonify(_report(dev, str(b.get('label') or '')[:60], b))
    except _Bad as e:
        return jsonify({'error': str(e)}), 400


# ─── live "Connect" channel (WebSocket) ──────────────────────────────────
# Devices stay connected; the active one reports playback, the others become remotes whose
# commands are forwarded to it. A transfer first asks the old device for its exact position.
def _extrapolated(sess):
    st = dict(sess.get('state') or {})
    if st.get('playing'):
        st['pos'] = round(st.get('pos', 0) + max(0, time.time() - sess.get('updated', time.time())), 1)
    return st


def _finalize_transfer(target):
    t = _pending.pop(target, None)
    if t:
        t.cancel()
    with _session_lock:
        cur = _load_session()
        if not cur or target not in _conns:
            return
        st = _extrapolated(cur) if not cur.get('_fresh') else dict(cur.get('state') or {})
        cur.pop('_fresh', None)
        label = _conns[target]['label']
        out = {'state': st, 'updated': time.time(), 'dev': target, 'label': label, 'active': target}
        _SESSION_FILE.write_text(json.dumps(out))
    _send(target, {'t': 'cmd', 'a': 'takeover', 'state': st, 'play': bool(st.get('playing'))})
    _broadcast(out)


def _mark_gone(dev):
    """A device dropped. After a short grace (page refresh) an active one counts as stopped."""
    _gone.pop(dev, None)
    with _session_lock:
        cur = _load_session()
        if not cur or cur.get('active') != dev or dev in _conns:
            return
        st = _extrapolated(cur)
        st['playing'] = False
        out = dict(cur, state=st, updated=time.time(), active=None)
        _SESSION_FILE.write_text(json.dumps(out))
    _broadcast(out)


def register(sock):
    from shared import ws_is_authenticated

    @sock.route('/api/music/connect')
    def music_connect(ws):
        # The HTTP auth gate already ran on the upgrade request (same-origin WebSockets carry the
        # login cookie, so the session token never has to appear in the URL or the request log).
        if not ws_is_authenticated(getattr(ws, 'environ', {})):
            try:
                ws.close()
            except Exception:
                pass
            return
        dev, label, lock = None, '', threading.Lock()
        try:
            while True:
                raw = ws.receive(timeout=60)
                if raw is None:                      # 60 s of silence: the client pings every 20 s
                    break
                try:
                    m = json.loads(raw)
                except ValueError:
                    continue
                t = m.get('t')
                if t == 'hello':
                    dev = str(m.get('dev') or '')[:60]
                    label = str(m.get('label') or 'Browser')[:60]
                    if not dev:
                        break
                    with _conn_lock:
                        old = _conns.get(dev)
                        # a "controller" (e.g. the KDE media-widget companion) can send commands but never plays audio
                        _conns[dev] = {'ws': ws, 'label': label, 'lock': lock, 'ctl': str(m.get('role') or '') == 'controller'}
                    g = _gone.pop(dev, None)
                    if g:
                        g.cancel()
                    if old and old['ws'] is not ws:
                        try:
                            old['ws'].close()
                        except Exception:
                            pass
                    with _session_lock:
                        sess = _load_session() or {}
                    _send(dev, _state_msg(sess))
                    _broadcast(sess, skip=dev)       # everyone else learns about the new device
                elif not dev:
                    continue
                elif t == 'ping':
                    _send(dev, {'t': 'pong'})
                elif t == 'state':
                    if _conns.get(dev, {}).get('ctl'):
                        continue                       # controllers never report playback
                    try:
                        res = _report(dev, label, m)
                    except _Bad as e:
                        res = {'ok': False, 'error': str(e)}
                    _send(dev, dict(res, t='ack'))
                elif t == 'cmd':
                    with _session_lock:
                        sess = _load_session() or {}
                    act = _effective_active(sess)
                    if act and act != dev and _send(act, {'t': 'cmd', 'a': m.get('a'), 'v': m.get('v'), 'state': m.get('state'), 'from': dev}):
                        continue
                    _send(dev, {'t': 'nodevice', 'cmd': m.get('a')})
                elif t == 'blocked':                 # the active device's browser refused to start audio
                    _send(str(m.get('to') or ''), {'t': 'blocked', 'dev': dev})
                elif t == 'transfer':
                    target = str(m.get('to') or '')
                    with _session_lock:
                        sess = _load_session() or {}
                    act = _effective_active(sess)
                    if target not in _conns or _conns[target].get('ctl') or target == act:
                        continue
                    if act and act in _conns:
                        _send(act, {'t': 'cmd', 'a': 'handoff', 'to': target})
                        tm = threading.Timer(2.5, _finalize_transfer, args=(target,))
                        old = _pending.pop(target, None)
                        if old:
                            old.cancel()
                        _pending[target] = tm
                        tm.start()
                    else:
                        _finalize_transfer(target)
                elif t == 'handoff':                 # exact position from the device that is giving up playback
                    target = str(m.get('to') or '')
                    try:
                        _report(dev, label, m, force=True)
                        with _session_lock:
                            cur = _load_session() or {}
                            cur['_fresh'] = True
                            _SESSION_FILE.write_text(json.dumps(cur))
                    except _Bad:
                        pass
                    if target in _pending:
                        _finalize_transfer(target)
        except Exception:
            pass
        finally:
            if dev:
                with _conn_lock:
                    mine = _conns.get(dev) and _conns[dev]['ws'] is ws
                    if mine:
                        _conns.pop(dev, None)
                if mine:
                    tm = threading.Timer(_GONE_GRACE, _mark_gone, args=(dev,))
                    _gone[dev] = tm
                    tm.start()
                    _broadcast(None)
