"""Phone backup intake: the phone pushes new files here (see android/pusher).

Off unless DECLOUD_BACKUP_TOKEN is set (24+ characters). The phone uses that token, not a login
session, so a leaked phone credential can only add files: it cannot read, list, overwrite or delete.
Files land under DECLOUD_BACKUP_DIR (default <files dir>/Backups/s23) in four fixed folders."""
from flask import Blueprint, jsonify, request
import hmac, os, secrets, shutil, threading, time
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
    need, bad = [], 0
    for item in files:
        rel = item[0] if isinstance(item, (list, tuple)) and item else None
        dest = _dest(rel)
        if dest is None:
            bad += 1
        elif not dest.exists():
            need.append(rel)
    if need:
        audit.log('backup_sync', '%d new of %d' % (len(need), len(files)))
    return jsonify({'need': need, 'bad': bad})


@bp.route('/api/backup/file', methods=['PUT'])
@limiter.exempt
def put_file():
    """Store one file. Never overwrites: an existing path is left alone and reported as existed."""
    dest = _dest(request.args.get('p', ''))
    if dest is None:
        return jsonify({'error': 'Bad path'}), 400
    if dest.exists():
        return jsonify({'ok': True, 'existed': True})
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
