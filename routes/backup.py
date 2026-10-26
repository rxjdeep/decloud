"""Phone backup intake: the phone pushes new files here (see android/pusher).

Off unless DECLOUD_BACKUP_TOKEN is set (24+ characters). The phone uses that token, not a login
session, so a leaked phone credential can only add files: it cannot read, list, overwrite or delete.
Files land under DECLOUD_BACKUP_DIR (default <files dir>/Backups/s23) in four fixed folders."""
from flask import Blueprint, jsonify, request
import fcntl, hashlib, hmac, os, secrets, shutil, threading, time
from pathlib import Path

import audit
import shared
from shared import limiter

bp = Blueprint('backup', __name__)

ALLOWED_TOP = ('Camera', 'Screenshots', 'Downloads', 'Documents')
MAX_CHECK = 2000                         # paths per /check call
CHUNK = 1 << 20
FREE_RESERVE = 2 * 1024 ** 3             # never fill the disk: keep 2 GiB free
_FAIL_LIMIT, _FAIL_WINDOW = 10, 600      # wrong tokens per IP before a 10 minute pause
_lock = threading.Lock()
_fails = {}


def _token():
    return os.environ.get('DECLOUD_BACKUP_TOKEN', '').strip()


def _root():
    p = os.environ.get('DECLOUD_BACKUP_DIR')
    return Path(p).expanduser() if p else Path(shared.FILES_DIR) / 'Backups' / 's23'


def _max_file():
    try:
        return int(os.environ.get('DECLOUD_BACKUP_MAX_MB', '20480')) * 1024 * 1024
    except ValueError:
        return 20480 * 1024 * 1024


@bp.before_request
def _gate():
    tok = _token()
    if len(tok) < 24:
        return jsonify({'error': 'Backup intake is not enabled'}), 404
    ip, now = request.remote_addr or '', time.time()
    with _lock:
        recent = [t for t in _fails.get(ip, []) if now - t < _FAIL_WINDOW]
        _fails[ip] = recent
        if len(recent) >= _FAIL_LIMIT:
            return jsonify({'error': 'Too many wrong tokens, try again later'}), 429
    auth = request.headers.get('Authorization', '')
    given = auth[7:].strip() if auth.startswith('Bearer ') else ''
    if not hmac.compare_digest(given.encode(), tok.encode()):
        with _lock:
            _fails.setdefault(ip, []).append(now)
        audit.log('backup_auth_fail', ok=False)
        return jsonify({'error': 'Invalid token'}), 401


def _dest(rel):
    """Where a phone path would be stored, or None if it is not an acceptable relative path."""
    if not isinstance(rel, str) or not rel or len(rel) > 1024 or '\x00' in rel or '\\' in rel:
        return None
    parts = rel.split('/')
    if len(parts) < 2 or parts[0] not in ALLOWED_TOP:
        return None
    if any(p in ('', '.', '..') or len(p.encode('utf-8', 'surrogatepass')) > 240 for p in parts):
        return None
    root = _root().resolve()
    dest = root.joinpath(*parts)
    try:
        parent = dest.parent.resolve()        # follows symlinks that already exist, so none can lead out of the root
    except (OSError, RuntimeError):
        return None
    if parent != root and not parent.is_relative_to(root):
        return None
    return dest


@bp.route('/api/backup/check', methods=['POST'])
def check():
    """{"files": [["Camera/a.jpg", 1234], ...]} -> {"need": [paths the server does not have yet]}"""
    data = request.get_json(silent=True) or {}
    files = data.get('files')
    if not isinstance(files, list) or len(files) > MAX_CHECK:
        return jsonify({'error': 'files must be a list of at most %d entries' % MAX_CHECK}), 400
    need, bad, partial = [], 0, {}
    for item in files:
        rel = item[0] if isinstance(item, (list, tuple)) and item else None
        dest = _dest(rel)
        if dest is None:
            bad += 1
        elif not dest.exists():
            need.append(rel)
            try:                                                # an interrupted upload of this file: how far it got
                have = _part(dest).stat().st_size
                if 0 < have < int(item[1]):
                    partial[rel] = have
            except (OSError, ValueError, IndexError, TypeError):
                pass
    if need:
        audit.log('backup_sync', '%d new of %d' % (len(need), len(files)))
    return jsonify({'need': need, 'bad': bad, 'partial': partial})


def _part(dest):
    """The half-sent copy of a file: a fixed name per destination, so the next attempt finds it and carries on."""
    return dest.parent / ('.part-r-' + hashlib.sha1(str(dest).encode('utf-8', 'surrogatepass')).hexdigest()[:16])


def _put_resumable(dest, total):
    """A phone that sends ?total=<whole size>&off=<bytes already sent> can stop and carry on: what arrived is kept until the file is complete."""
    limit = _max_file()
    if total <= 0 or total > limit:
        return jsonify({'error': 'File too large' if total > limit else 'Bad total'}), 413 if total > limit else 400
    off = request.args.get('off', 0, type=int) or 0
    size = request.content_length
    if size is None:
        return jsonify({'error': 'Content-Length required'}), 411
    if off < 0 or off + size != total:
        return jsonify({'error': 'Offset and length do not add up to the total'}), 400
    request.max_content_length = limit + CHUNK
    part = _part(dest)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(dest.parent).free < size + FREE_RESERVE:
            return jsonify({'error': 'Not enough free disk space'}), 507
        now = time.time()
        for old in dest.parent.glob('.part-r-*'):               # abandoned for a week: gone
            try:
                if now - old.stat().st_mtime > 7 * 86400 and old != part:
                    old.unlink()
            except OSError:
                pass
        have = part.stat().st_size if part.exists() else 0
        if off > 0 and have != off:
            return jsonify({'error': 'Offset does not match what the server has', 'have': have}), 409
        f = open(part, 'ab' if off > 0 else 'wb')
    except OSError as e:
        return jsonify({'error': str(e)}), 500
    with f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)         # another upload of the same file is running
        except OSError:
            return jsonify({'error': 'This file is already being uploaded'}), 409
        got = 0
        while True:
            try:
                chunk = request.stream.read(CHUNK)
            except Exception:                                      # the phone dropped the connection mid-body
                break
            if not chunk:
                break
            got += len(chunk)
            if got > size:
                return jsonify({'error': 'Body longer than Content-Length'}), 400
            f.write(chunk)
        f.flush()
        if got != size:
            return jsonify({'error': 'Upload was cut short', 'have': off + got}), 400      # kept: the next attempt carries on from here
    try:
        m = float(request.args.get('m', '')) / 1000.0
        if 0 < m < 4102444800:
            os.utime(part, (m, m))
    except ValueError:
        pass
    try:
        os.link(part, dest)                                       # atomic, and refuses to replace an existing file
        existed = False
    except FileExistsError:
        existed = True
    finally:
        try:
            part.unlink()
        except OSError:
            pass
    return jsonify({'ok': True, 'existed': existed})


@bp.route('/api/backup/file', methods=['PUT'])
@limiter.exempt
def put_file():
    """Store one file. Never overwrites: an existing path is left alone and reported as existed."""
    dest = _dest(request.args.get('p', ''))
    if dest is None:
        return jsonify({'error': 'Bad path'}), 400
    if dest.exists():
        return jsonify({'ok': True, 'existed': True})
    total = request.args.get('total', type=int)
    if total is not None:
        return _put_resumable(dest, total)
    size, limit = request.content_length, _max_file()
    if size is None:
        return jsonify({'error': 'Content-Length required'}), 411
    if size > limit:
        return jsonify({'error': 'File too large'}), 413
    request.max_content_length = limit + CHUNK          # the app-wide 100 MB cap is for everything else
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(dest.parent).free < size + FREE_RESERVE:
            return jsonify({'error': 'Not enough free disk space'}), 507
    except OSError as e:
        return jsonify({'error': str(e)}), 500
    tmp = dest.parent / ('.part-' + secrets.token_hex(8))
    got = 0
    try:
        with open(tmp, 'wb') as f:
            while True:
                chunk = request.stream.read(CHUNK)
                if not chunk:
                    break
                got += len(chunk)
                if got > size:
                    return jsonify({'error': 'Body longer than Content-Length'}), 400
                f.write(chunk)
        if got != size:
            return jsonify({'error': 'Upload was cut short'}), 400
        try:
            m = float(request.args.get('m', '')) / 1000.0
            if 0 < m < 4102444800:
                os.utime(tmp, (m, m))                      # keep the phone's modified time
        except ValueError:
            pass
        try:
            os.link(tmp, dest)                             # atomic and refuses to replace an existing file
        except FileExistsError:
            return jsonify({'ok': True, 'existed': True})
        return jsonify({'ok': True, 'existed': False})
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
