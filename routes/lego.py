"""Lego file browser (Files) routes."""
from flask import Blueprint, jsonify, request, send_file
import os, json, subprocess, secrets
from pathlib import Path
import random as _random
import shutil as _shutil
from shared import (
    format_size,
    _folder_info_cache,
    safe_join_browse, _generate_thumbnail,
)

import shared
import audit
from shared import limiter
from shared import browse_root as _root
from .gallery import _taken as _gallery_taken

bp = Blueprint('lego', __name__)

@bp.route('/api/lego/browse')
@bp.route('/api/files/browse')
def lego_browse():
    """Browse Files drive. Query: path=<relative>, page=<n>, per_page=<n>"""
    import os as _os, time as _time
    root = _root()
    if 'path' in request.args:
        subpath = request.args.get('path', '')
    else:                                            # no path given: start in the user's home folder when it is inside the root
        try:
            subpath = str(Path.home().resolve().relative_to(root)).replace('\\', '/')
            subpath = '' if subpath == '.' else subpath
        except ValueError:
            subpath = ''
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(5000, request.args.get('per_page', 200, type=int))
    date_sort = request.args.get('date_sort', '')  # '' (by name) | 'newest' | 'oldest'
    target = safe_join_browse(_root(), *subpath.split('/')) if subpath else _root()

    if not target.exists() or not target.is_dir():
        return jsonify({'error': 'not found or not a directory'}), 404

    # Use os.scandir - gets is_dir and stat from the directory entry itself
    # much faster than pathlib iterdir + stat on slow exfat
    all_items = []
    try:
        with _os.scandir(str(target)) as it:
            for entry in it:
                if entry.name.startswith('.'):
                    continue
                try:
                    is_dir = entry.is_dir()
                    rel_path = str(Path(entry.path).relative_to(_root()))
                    if is_dir:
                        try:
                            dir_mtime = entry.stat().st_mtime
                        except OSError:
                            dir_mtime = 0
                        all_items.append({
                            'name': entry.name,
                            'is_dir': True,
                            'size': 0,
                            'size_human': '',
                            'modified': '',
                            'ext': '',
                            'mtime': int(dir_mtime),
                            'path': rel_path,
                            'child_count': -1,
                            'has_images': False,
                            '_sort_name': entry.name.lower(),
                            '_sort_dir': 0,
                            '_mtime': dir_mtime,
                        })
                    else:
                        st = entry.stat()
                        all_items.append({
                            'name': entry.name,
                            'is_dir': False,
                            'size': st.st_size,
                            'size_human': format_size(st.st_size),
                            'modified': _time.strftime('%Y-%m-%d %H:%M', _time.localtime(st.st_mtime)),
                            'ext': Path(entry.name).suffix.lower().lstrip('.'),
                            'mtime': int(st.st_mtime),
                            'taken': _gallery_taken(entry.name, st.st_mtime),
                            'path': rel_path,
                            'child_count': 0,
                            'has_images': False,
                            '_sort_name': entry.name.lower(),
                            '_sort_dir': 1,
                            '_mtime': st.st_mtime,
                        })
                except (PermissionError, OSError):
                    continue
    except PermissionError:
        return jsonify({'error': 'permission denied'}), 403

    # Sort: folders first, then alpha (or by modified date when requested)
    if date_sort in ('newest', 'oldest'):
        sign = -1 if date_sort == 'newest' else 1
        all_items.sort(key=lambda x: (x['_sort_dir'], sign * x['_mtime'], x['_sort_name']))
    else:
        all_items.sort(key=lambda x: (x['_sort_dir'], x['_sort_name']))
    total = len(all_items)

    # Paginate
    start = (page - 1) * per_page
    page_items = all_items[start:start + per_page]
    # Clean up sort keys before sending
    for item in page_items:
        item.pop('_sort_name', None)
        item.pop('_sort_dir', None)
        item.pop('_mtime', None)

    # Build breadcrumb path
    breadcrumbs = [{'name': 'Computer' if str(root) == '/' else 'Files', 'path': ''}]
    if subpath:
        parts = subpath.split('/')
        for i, part in enumerate(parts):
            if part:
                breadcrumbs.append({'name': part, 'path': '/'.join(parts[:i+1])})

    def _rel(pth):
        try:
            r = str(Path(pth).resolve().relative_to(root)).replace('\\', '/')
            return '' if r == '.' else r
        except ValueError:
            return None
    places = []
    home = Path.home()
    cand = [('Home', home, 'home')] + [(n, home / n, n.lower()) for n in ('Desktop', 'Documents', 'Downloads', 'Pictures', 'Music', 'Videos')]
    cand += [('Backup', Path(shared.FILES_DIR), 'backup'), ('Camera', Path(shared.FILES_DIR) / 'Camera', 'camera'), ('Screenshots', Path(shared.FILES_DIR) / 'Screenshots', 'screenshots')]
    trash_dir = Path(shared.FILES_DIR) / 'file types' / 'trash'
    try:
        trash_dir.mkdir(parents=True, exist_ok=True)        # so the place always opens, even before anything was trashed
    except OSError:
        pass
    cand.append(('Trash', trash_dir, 'trash'))
    cfg = _places_cfg()
    for nm, pth, ic in cand:
        r = _rel(pth) if pth.is_dir() else None
        if r is not None and not (nm != 'Trash' and (nm in cfg['hidden'] or r in cfg['hidden'])):
            places.append({'name': nm, 'path': r, 'icon': ic, 'fixed': nm == 'Trash'})
    have = {p['path'] for p in places}
    for a in cfg['added']:
        try:
            tgt = safe_join_browse(root, *a['path'].split('/')) if a['path'] else root
            if tgt.is_dir() and a['path'] not in have:
                places.append({'name': a['name'], 'path': a['path'], 'icon': 'folder', 'custom': True})
                have.add(a['path'])
        except Exception:
            pass
    try:
        du = _shutil.disk_usage(target)
        disk = {'free': du.free, 'total': du.total}
    except OSError:
        disk = None
    return jsonify({
        'places': places, 'backup': _rel(shared.FILES_DIR), 'trash': _rel(trash_dir), 'is_root': str(root) == '/',
        'items': page_items, 'disk': disk,
        'path': subpath,
        'breadcrumbs': breadcrumbs,
        'item_count': total,
        'page': page,
        'per_page': per_page,
        'has_more': start + per_page < total,
    })

# In-memory cache for folder info (child_count, has_images)
_folder_info_cache = {}  # path -> (mtime, child_count, has_images)

@bp.route('/api/lego/folder_info')
def lego_folder_info():
    """Lazy get folder child_count + has_images. Query: path=<relative>"""
    subpath = request.args.get('path', '')
    if not subpath:
        return jsonify({'error': 'path required'}), 400
    target = safe_join_browse(_root(), *subpath.split('/'))
    if not target.exists() or not target.is_dir():
        return jsonify({'error': 'not found'}), 404

    try:
        mtime = target.stat().st_mtime
    except OSError:
        mtime = 0

    cache_key = str(target)
    cached = _folder_info_cache.get(cache_key)
    if cached and cached[0] == mtime:
        return jsonify({'child_count': cached[1], 'has_images': cached[2]})

    child_count = 0
    has_images = False
    img_exts = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic', '.heif'}
    try:
        for child in target.iterdir():
            child_count += 1
            if not has_images and child.suffix.lower() in img_exts:
                has_images = True
    except (PermissionError, OSError):
        pass

    _folder_info_cache[cache_key] = (mtime, child_count, has_images)
    return jsonify({'child_count': child_count, 'has_images': has_images})

def format_size(size):
    """Format bytes as human-readable."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'

@bp.route('/api/lego/download')
@limiter.exempt
def lego_download():
    """Download a file from Files. Query: path=<relative path>"""
    subpath = request.args.get('path', '')
    if not subpath:
        return jsonify({'error': 'path required'}), 400
    target = safe_join_browse(_root(), *subpath.split('/'))
    if not target.exists() or not target.is_file():
        return jsonify({'error': 'not found'}), 404
    return send_file(str(target), as_attachment=True, download_name=target.name)

@bp.route('/api/lego/thumbnail')
@limiter.exempt
def lego_thumbnail():
    """Serve a cached WebP thumbnail. Query: path=<relative path>"""
    subpath = request.args.get('path', '')
    if not subpath:
        return jsonify({'error': 'path required'}), 400
    target = safe_join_browse(_root(), *subpath.split('/'))
    if not target.exists():
        return jsonify({'error': 'not found'}), 404

    # If it's a directory, find the first image inside it
    if target.is_dir():
        img_exts = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic', '.heif'}
        try:
            for child in sorted(target.iterdir(), key=lambda x: x.name.lower()):
                if child.suffix.lower() in img_exts and child.is_file():
                    thumb = _generate_thumbnail(child)
                    if thumb and thumb.exists():
                        resp = send_file(str(thumb), mimetype='image/webp')
                        resp.headers['Cache-Control'] = 'public, max-age=86400'
                        return resp
        except (PermissionError, OSError):
            pass
        return jsonify({'error': 'no images in folder'}), 404

    # File: generate thumbnail from it
    if not target.is_file():
        return jsonify({'error': 'not found'}), 404
    ext = target.suffix.lower()
    if ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
        thumb = _generate_thumbnail(target)
        if thumb and thumb.exists():
            resp = send_file(str(thumb), mimetype='image/webp')
            resp.headers['Cache-Control'] = 'public, max-age=86400'
            return resp
        # Fallback to raw if thumbnail generation fails
        return send_file(str(target), mimetype=f'image/{ext.lstrip(".")}')
    if ext in ('.heic', '.heif'):
        thumb = _generate_thumbnail(target)
        if thumb and thumb.exists():
            resp = send_file(str(thumb), mimetype='image/webp')
            resp.headers['Cache-Control'] = 'public, max-age=86400'
            return resp
        return jsonify({'error': 'cannot thumbnail heic'}), 400
    return jsonify({'error': 'not an image'}), 400


# ─── API: Lego Swipe Mode ──────────────────────────────────
import random as _random

@bp.route('/api/lego/random_image')
def lego_random_image():
    """Get a random image file from a folder (non-recursive, fast). Query: path=<relative>"""
    subpath = request.args.get('path', '')
    target = safe_join_browse(_root(), *subpath.split('/')) if subpath else _root()
    if not target.exists() or not target.is_dir():
        return jsonify({'error': 'not found'}), 404

    img_exts = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic', '.heif'}
    images = []
    try:
        for entry in target.iterdir():
            if entry.is_file() and entry.suffix.lower() in img_exts:
                images.append(entry)
    except (PermissionError, OSError):
        pass

    if not images:
        return jsonify({'error': 'no images found'}), 404

    pick = _random.choice(images)
    return jsonify({
        'path': str(pick.relative_to(_root())),
        'name': pick.name,
        'total_images': len(images),
    })


def _inside(path, root):
    """True if path is root or somewhere below it. Real-path containment, not a string prefix: with a root of '/' the prefix
    '/' + '/' is '//' and nothing matches, which made every file look "outside Files"."""
    return path == root or path.is_relative_to(root)


# ─── Places: the folders listed in the sidebar (the built-in ones can be hidden, and you can add your own) ──
def _places_cfg():
    try:
        d = json.loads(shared.SETTINGS_FILE.read_text()).get('files_places') or {}
    except Exception:
        d = {}
    added = [a for a in d.get('added', []) if isinstance(a, dict) and isinstance(a.get('path'), str) and isinstance(a.get('name'), str)]
    hidden = [h for h in d.get('hidden', []) if isinstance(h, str)]
    return {'added': added, 'hidden': hidden}


def _save_places_cfg(cfg):
    try:
        settings = json.loads(shared.SETTINGS_FILE.read_text()) if shared.SETTINGS_FILE.exists() else {}
    except Exception:
        settings = {}
    settings['files_places'] = cfg
    shared.SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


@bp.route('/api/files/places', methods=['POST'])
def files_places():
    """{"action": "add", "path": "<folder>", "name": "<label>"} | {"action": "remove", "path": "<folder>", "name": "<label>"} | {"action": "reset"}"""
    data = request.get_json(silent=True) or {}
    action, path = data.get('action'), str(data.get('path') or '')
    cfg = _places_cfg()
    if action == 'reset':
        cfg = {'added': [], 'hidden': []}
    elif action == 'add':
        if not path:
            return jsonify({'error': 'Pick a folder inside the drive'}), 400
        tgt = safe_join_browse(_root(), *path.split('/'))
        if not tgt.is_dir():
            return jsonify({'error': 'Not a folder'}), 404
        name = (str(data.get('name') or '').strip() or tgt.name)[:60]
        cfg['hidden'] = [h for h in cfg['hidden'] if h != path]                     # a built-in one you hid comes back
        if not any(a['path'] == path for a in cfg['added']):
            cfg['added'].append({'name': name, 'path': path})
    elif action == 'remove':
        before = len(cfg['added'])
        cfg['added'] = [a for a in cfg['added'] if a['path'] != path]
        if len(cfg['added']) == before:                                              # a built-in place: hide it
            nm = str(data.get('name') or '')
            if nm == 'Trash':
                return jsonify({'error': 'Trash stays'}), 400
            if nm and nm not in cfg['hidden']:
                cfg['hidden'].append(nm)
    else:
        return jsonify({'error': 'bad action'}), 400
    _save_places_cfg(cfg)
    return jsonify({'ok': True})


# ─── Upload: put a file from this device into a Files folder ─────────────────
UPLOAD_RESERVE = 2 * 1024 ** 3            # never fill the disk: keep 2 GiB free
UPLOAD_CHUNK = 1 << 20


def _upload_limit():
    try:
        return int(os.environ.get('RECLMR_UPLOAD_MAX_MB', '20480')) * 1024 * 1024
    except ValueError:
        return 20480 * 1024 * 1024


def _clean_upload_name(name):
    """A bare file name, or None: no folders, no hidden or dot-only names, no control characters."""
    if not isinstance(name, str):
        return None
    name = name.strip()
    if (not name or name in ('.', '..') or name.startswith('.') or '/' in name or '\\' in name
            or any(ord(c) < 32 for c in name) or len(name.encode('utf-8', 'surrogatepass')) > 240):
        return None
    return name


@bp.route('/api/files/upload', methods=['PUT'])
@limiter.exempt
def files_upload():
    """Store one file in a Files folder. Query: dir=<folder relative to the root>, name=<file name>. The body is the raw file.
    Never replaces anything: if the name is taken the new file becomes 'name (2).ext'."""
    name = _clean_upload_name(request.args.get('name', ''))
    if name is None:
        return jsonify({'error': 'Bad file name'}), 400
    rel = request.args.get('dir', '')
    if any(part.startswith('.') and part not in ('', '.') for part in rel.split('/')):
        return jsonify({'error': 'Uploads cannot go into hidden folders'}), 403      # ~/.ssh, ~/.config and the like
    root = _root()
    folder = safe_join_browse(root, *rel.split('/')) if rel else root
    if not folder.is_dir():
        return jsonify({'error': 'Folder not found'}), 404
    size, limit = request.content_length, _upload_limit()
    if size is None:
        return jsonify({'error': 'Content-Length required'}), 411
    if size > limit:
        return jsonify({'error': 'File too large'}), 413
    request.max_content_length = limit + UPLOAD_CHUNK       # the app-wide 100 MB cap is for everything else
    try:
        if _shutil.disk_usage(folder).free < size + UPLOAD_RESERVE:
            return jsonify({'error': 'Not enough free disk space'}), 507
    except OSError as e:
        return jsonify({'error': str(e)}), 500
    tmp = folder / ('.upload-' + secrets.token_hex(8))
    got = 0
    try:
        with open(tmp, 'wb') as f:
            while True:
                chunk = request.stream.read(UPLOAD_CHUNK)
                if not chunk:
                    break
                got += len(chunk)
                if got > size:
                    return jsonify({'error': 'Body longer than Content-Length'}), 400
                f.write(chunk)
        if got != size:
            return jsonify({'error': 'Upload was cut short'}), 400
        stem, ext = os.path.splitext(name)
        dest = folder / name
        for n in range(2, 1000):
            try:
                os.link(tmp, dest)                          # atomic, and refuses to replace an existing file
                break
            except FileExistsError:
                dest = folder / f'{stem} ({n}){ext}'
        else:
            return jsonify({'error': 'Too many files with that name'}), 409
        audit.log('file_upload', str(dest))
        return jsonify({'ok': True, 'name': dest.name, 'size': got})
    except OSError as e:
        return jsonify({'error': str(e)}), 500
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# ─── File operations: new folder, rename, copy / move ────────────────────────
PASTE_LIMIT = 4 * 1024 ** 3                    # one request copies at most this much (a cross-disk move is a copy)


class _FmErr(Exception):
    pass


def _fm_rel(p):
    try:
        return str(p.relative_to(_root())).replace('\\', '/')
    except ValueError:
        return str(p)


def _fm_dir(rel):
    """(folder, error) for a folder named relative to the root. Hidden folders (~/.ssh and the like) are refused."""
    rel = str(rel or '')
    if any(part.startswith('.') and part not in ('', '.') for part in rel.split('/')):
        return None, 'Hidden folders are off limits'
    root = _root()
    folder = safe_join_browse(root, *rel.split('/')) if rel else root
    if not folder.is_dir():
        return None, 'Folder not found'
    return folder, ''


def _fm_entry(rel):
    """(path, error) for an existing file or folder named relative to the root. The last part is NOT resolved, so a
    symlink is seen as one (and refused) instead of acting on whatever it points to."""
    parts = [x for x in str(rel or '').split('/') if x not in ('', '.')]
    if not parts or '..' in parts or any(x.startswith('.') for x in parts):
        return None, 'Bad path'
    root = _root()
    parent = safe_join_browse(root, *parts[:-1])
    if parent != root.joinpath(*parts[:-1]).resolve():
        return None, 'Outside Files'
    p = parent / parts[-1]
    if p.is_symlink():
        return None, 'Shortcuts (symlinks) cannot be changed here'
    if not p.exists():
        return None, 'Not found'
    return p, ''


def _fm_fixed(p):
    """Why this folder must stay where it is (home, the main folders, Trash, anything that holds them), or ''."""
    if not p.is_dir():
        return ''
    home = Path.home().resolve()
    trash = (Path(shared.FILES_DIR) / 'file types' / 'trash').resolve()
    main = {Path(shared.FILES_DIR).resolve(), trash} | {home / n for n in ('Desktop', 'Documents', 'Downloads', 'Pictures', 'Music', 'Videos')}
    if p in main or home.is_relative_to(p) or trash.is_relative_to(p) or p.parent == p:
        return 'That is one of your main folders'
    return ''


def _fm_tree_size(p, cap):
    if not p.is_dir():
        return p.lstat().st_size
    total = 0
    for dp, _dn, fn in os.walk(p):
        for f in fn:
            try:
                total += os.lstat(os.path.join(dp, f)).st_size
            except OSError:
                pass
        if total > cap:
            break
    return total


def _fm_free_name(folder, name, is_dir):
    dest = folder / name
    stem, ext = (name, '') if is_dir else os.path.splitext(name)
    n = 2
    while os.path.lexists(dest):
        dest = folder / f'{stem} ({n}){ext}'
        n += 1
        if n > 1000:
            raise _FmErr('Too many items with that name')
    return dest


@bp.route('/api/files/mkdir', methods=['POST'])
def files_mkdir():
    """{"dir": "<folder>", "name": "<new folder name>"}"""
    data = request.get_json(silent=True) or {}
    name = _clean_upload_name(data.get('name'))
    if name is None:
        return jsonify({'error': 'Bad folder name'}), 400
    folder, err = _fm_dir(data.get('dir'))
    if err:
        return jsonify({'error': err}), 403 if err.startswith('Hidden') else 404
    dest = folder / name
    try:
        dest.mkdir()
    except FileExistsError:
        return jsonify({'error': 'Something with that name already exists'}), 409
    except OSError as e:
        return jsonify({'error': e.strerror or str(e)}), 500
    audit.log('file_mkdir', str(dest))
    return jsonify({'ok': True, 'name': name, 'path': _fm_rel(dest)})


@bp.route('/api/files/rename', methods=['POST'])
def files_rename():
    """{"path": "<file or folder>", "name": "<new name>"}: stays in the same folder, never replaces anything."""
    data = request.get_json(silent=True) or {}
    name = _clean_upload_name(data.get('name'))
    if name is None:
        return jsonify({'error': 'Bad name'}), 400
    src, err = _fm_entry(data.get('path'))
    if err:
        return jsonify({'error': err}), 404 if err == 'Not found' else 400
    why = _fm_fixed(src)
    if why:
        return jsonify({'error': why}), 403
    dest = src.with_name(name)
    if dest != src:
        if os.path.lexists(dest) and not (dest.exists() and dest.samefile(src)):
            return jsonify({'error': 'Something with that name already exists'}), 409
        try:
            os.rename(src, dest)
        except OSError as e:
            return jsonify({'error': e.strerror or str(e)}), 500
        audit.log('file_rename', f'{src} -> {dest}')
    return jsonify({'ok': True, 'name': name, 'path': _fm_rel(dest)})


@bp.route('/api/files/paste', methods=['POST'])
def files_paste():
    """{"paths": [...], "dir": "<target folder>", "move": true|false}: copies or moves into a folder. A name that is taken
    becomes 'name (2)'. Answers with one result per path: {path, to} or {path, error}."""
    data = request.get_json(silent=True) or {}
    paths, move = data.get('paths'), bool(data.get('move'))
    if not isinstance(paths, list) or not paths or len(paths) > 500 or not all(isinstance(x, str) for x in paths):
        return jsonify({'error': 'Nothing to paste'}), 400
    folder, err = _fm_dir(data.get('dir'))
    if err:
        return jsonify({'error': err}), 403 if err.startswith('Hidden') else 404
    results, budget = [], PASTE_LIMIT
    for rel in paths:
        src, err = _fm_entry(rel)
        if err:
            results.append({'path': rel, 'error': err})
            continue
        try:
            if move and _fm_fixed(src):
                raise _FmErr('That is one of your main folders')
            if src.is_dir() and (folder == src or folder.is_relative_to(src)):
                raise _FmErr("A folder can't go inside itself")
            if move and src.parent == folder:
                results.append({'path': rel, 'to': rel})            # already there
                continue
            dest = _fm_free_name(folder, src.name, src.is_dir())
            copying = not move or os.stat(src).st_dev != os.stat(folder).st_dev
            if copying:
                size = _fm_tree_size(src, budget)
                if size > budget:
                    raise _FmErr('Too big to copy in one go (over 4 GB)')
                if _shutil.disk_usage(folder).free < size + UPLOAD_RESERVE:
                    raise _FmErr('Not enough free disk space')
                budget -= size
            if move:
                _shutil.move(str(src), str(dest))
            elif src.is_dir():
                try:
                    _shutil.copytree(src, dest, symlinks=True)
                except Exception:
                    _shutil.rmtree(dest, ignore_errors=True)         # the half-made copy is ours: not there before
                    raise
            else:
                _shutil.copy2(src, dest)
            audit.log('file_move' if move else 'file_copy', f'{src} -> {dest}')
            results.append({'path': rel, 'to': _fm_rel(dest)})
        except _FmErr as e:
            results.append({'path': rel, 'error': str(e)})
        except OSError as e:
            results.append({'path': rel, 'error': e.strerror or str(e)})
    done = [r for r in results if 'to' in r]
    if not done:
        return jsonify({'error': results[0]['error'], 'results': results}), 400
    return jsonify({'ok': True, 'done': len(done), 'results': results})


CROSS_DISK_FOLDER_LIMIT = 2 * 1024 ** 3        # a folder from another disk is copied into Trash first: refuse big ones


def _folder_trash_refusal(folder, trash):
    """Why this folder must not be moved to Trash, or '' if it can. The folder is already resolved."""
    home = Path.home().resolve()
    if folder == trash or trash.is_relative_to(folder) or folder.is_relative_to(trash):
        return 'That folder holds the Trash'
    if folder == home or home.is_relative_to(folder) or folder.parent == folder:
        return 'That folder holds your home folder'
    try:
        names = {Path(shared.FILES_DIR).resolve()} | {home / n for n in ('Desktop', 'Documents', 'Downloads', 'Pictures', 'Music', 'Videos')}
    except OSError:
        names = set()
    if folder in names:
        return 'That is one of your main folders: remove its contents instead'
    try:
        if os.stat(folder).st_dev != os.stat(trash).st_dev:      # another disk: shutil.move would copy everything, then delete
            total = 0
            for dp, _dn, fn in os.walk(folder):
                for f in fn:
                    try:
                        total += os.lstat(os.path.join(dp, f)).st_size
                    except OSError:
                        pass
                if total > CROSS_DISK_FOLDER_LIMIT:
                    return 'This folder is on another disk and too big to copy into Trash (over 2 GB)'
    except OSError as e:
        return str(e)
    return ''


@bp.route('/api/lego/trash', methods=['POST'])
def lego_trash():
    """Move a file to /file types/trash/ inside Files."""
    data = request.get_json(silent=True) or {}
    subpath = data.get('path', '')
    if not subpath:
        return jsonify({'error': 'path required'}), 400

    target = safe_join_browse(_root(), *subpath.split('/'))
    files_root_resolved = _root()
    target_resolved = target.resolve()

    if not _inside(target_resolved, files_root_resolved):
        return jsonify({'error': 'path outside Files'}), 403
    if target.is_symlink():
        return jsonify({'error': 'cannot trash symlinks'}), 400
    is_dir = target_resolved.is_dir()
    if not is_dir and not target_resolved.is_file():
        return jsonify({'error': 'not a file or folder'}), 400

    trash_dir = (shared.FILES_DIR / 'file types' / 'trash')
    trash_dir.mkdir(parents=True, exist_ok=True)
    if is_dir:
        if target_resolved == files_root_resolved.resolve() or files_root_resolved.resolve().is_relative_to(target_resolved):
            return jsonify({'error': 'cannot trash the drive root or a folder that holds it'}), 400      # a "../" path is pulled back to the root: never trash that
        why = _folder_trash_refusal(target_resolved, trash_dir.resolve())
        if why:
            return jsonify({'error': why}), 400
    dest = trash_dir / target_resolved.name

    if dest.exists():
        stem = target_resolved.name if is_dir else target_resolved.stem
        suffix = '' if is_dir else target_resolved.suffix
        i = 1
        while dest.exists():
            dest = trash_dir / f'{stem}_{i}{suffix}'
            i += 1

    try:
        _shutil.move(str(target_resolved), str(dest))
        audit.log('file_trash', str(target_resolved))
        try:
            moved_to = str(dest.relative_to(_root()))
        except ValueError:                                  # the trash folder lives outside a narrower browse root
            moved_to = str(dest)
        return jsonify({'ok': True, 'moved_to': moved_to})
    except Exception as e:
        return jsonify({'error': f'trash error: {str(e)}'}), 500


@bp.route('/api/lego/delete', methods=['POST'])
def lego_delete():
    """Delete files and folders for good, but only ones that are already in the Trash folder. Body: {"paths": [...]} or {"empty": true}."""
    data = request.get_json(silent=True) or {}
    trash = (Path(shared.FILES_DIR) / 'file types' / 'trash')
    try:
        trash_real = trash.resolve()
    except OSError:
        return jsonify({'error': 'no trash folder'}), 404
    if data.get('empty'):
        if not trash_real.is_dir():
            return jsonify({'ok': True, 'deleted': 0, 'failed': 0})
        targets = [c for c in trash_real.iterdir() if not c.is_symlink()]
    else:
        paths = data.get('paths')
        if not isinstance(paths, list) or not paths or len(paths) > 2000 or not all(isinstance(x, str) and x for x in paths):
            return jsonify({'error': 'paths required'}), 400
        targets = []
        for sub in paths:
            t = safe_join_browse(_root(), *sub.split('/'))
            targets.append(t)
    deleted = failed = 0
    for t in targets:
        try:
            real = t.resolve()
            if t.is_symlink() or not (real.is_file() or real.is_dir()) or real == trash_real or not real.is_relative_to(trash_real):
                failed += 1                                  # not a file or folder inside the Trash folder: never touched
                continue
            if real.is_dir():
                _shutil.rmtree(real)
            else:
                real.unlink()
            audit.log('file_delete', str(real))
            deleted += 1
        except OSError:
            failed += 1
    return jsonify({'ok': True, 'deleted': deleted, 'failed': failed})


# ─── API: Lego Shred & Poof ────────────────────────────────
import shutil as _shutil

@bp.route('/api/lego/shred', methods=['POST'])
def lego_shred():
    """Securely shred a single file using shred -uvz. Locked down to one file inside Files."""
    data = request.get_json(silent=True) or {}
    subpath = data.get('path', '')
    if not subpath:
        return jsonify({'error': 'path required'}), 400

    target = safe_join_browse(_root(), *subpath.split('/'))

    # Hard safety checks
    files_root_resolved = _root()
    target_resolved = target.resolve()

    # Must be inside Files (strict prefix match)
    if not _inside(target_resolved, files_root_resolved):
        return jsonify({'error': 'path outside Files'}), 403

    # Must be a file, not a directory
    if not target_resolved.is_file():
        return jsonify({'error': 'can only shred files, not directories'}), 400

    # Must not be a symlink (prevent following links outside Files)
    if target.is_symlink():
        return jsonify({'error': 'cannot shred symlinks'}), 400

    try:
        result = subprocess.run(
            ['shred', '-uvz', '-n', '3', str(target_resolved)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            return jsonify({'error': f'shred failed: {result.stderr.strip()}'}), 500
        audit.log('file_shred', str(target_resolved))
        return jsonify({'ok': True, 'shredded': target_resolved.name})
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'shred timed out'}), 500
    except Exception as e:
        return jsonify({'error': f'shred error: {str(e)}'}), 500


@bp.route('/api/lego/poof', methods=['POST'])
def lego_poof():
    """Move a file to /file types/poof/ inside Files."""
    data = request.get_json(silent=True) or {}
    subpath = data.get('path', '')
    if not subpath:
        return jsonify({'error': 'path required'}), 400

    target = safe_join_browse(_root(), *subpath.split('/'))

    files_root_resolved = _root()
    target_resolved = target.resolve()

    # Must be inside Files (strict prefix match)
    if not _inside(target_resolved, files_root_resolved):
        return jsonify({'error': 'path outside Files'}), 403

    # Must be a file
    if not target_resolved.is_file():
        return jsonify({'error': 'can only poof files'}), 400

    # Must not be a symlink
    if target.is_symlink():
        return jsonify({'error': 'cannot poof symlinks'}), 400

    # Must not already be in the poof directory
    poof_dir = (shared.FILES_DIR / 'file types' / 'poof')
    poof_dir.mkdir(parents=True, exist_ok=True)
    dest = poof_dir / target_resolved.name

    # If name collision, append number
    if dest.exists():
        stem = target_resolved.stem
        suffix = target_resolved.suffix
        i = 1
        while dest.exists():
            dest = poof_dir / f'{stem}_{i}{suffix}'
            i += 1

    try:
        _shutil.move(str(target_resolved), str(dest))
        audit.log('file_poof', str(target_resolved))
        return jsonify({'ok': True, 'moved_to': str(dest.relative_to(_root()))})
    except Exception as e:
        return jsonify({'error': f'poof error: {str(e)}'}), 500


@bp.route('/api/lego/mark_done', methods=['POST'])
def lego_mark_done():
    """Rename a folder by appending ' (MANUALLY DONE)' to its name."""
    data = request.get_json(silent=True) or {}
    subpath = data.get('path', '')
    if not subpath:
        return jsonify({'error': 'path required'}), 400

    target = safe_join_browse(_root(), *subpath.split('/'))

    files_root_resolved = _root()
    target_resolved = target.resolve()

    # Must be inside Files
    if not _inside(target_resolved, files_root_resolved):
        return jsonify({'error': 'path outside Files'}), 403

    # Must be a directory
    if not target_resolved.is_dir():
        return jsonify({'error': 'can only mark folders'}), 400

    # Must not be a symlink
    if target.is_symlink():
        return jsonify({'error': 'cannot mark symlinks'}), 400

    try:
        target_resolved.relative_to(Path(shared.FILES_DIR).resolve())
    except ValueError:
        return jsonify({'error': 'only folders inside the backup drive can be marked done'}), 400

    # Must not be the root
    if target_resolved == files_root_resolved:
        return jsonify({'error': 'cannot mark root'}), 400

    # Already marked?
    if ' (MANUALLY DONE)' in target_resolved.name:
        return jsonify({'error': 'already marked done'}), 400

    new_name = target_resolved.name + ' (MANUALLY DONE)'

    # Move into /manually done/ folder
    done_dir = shared.FILES_DIR / 'manually done'
    done_dir.mkdir(parents=True, exist_ok=True)
    dest = done_dir / new_name

    # Name collision check
    if dest.exists():
        return jsonify({'error': 'a folder with that name already exists in manually done/'}), 409

    try:
        _shutil.move(str(target_resolved), str(dest))
        audit.log('file_done', str(target_resolved))
        new_rel = str(dest.relative_to(_root()))
        return jsonify({'ok': True, 'new_path': new_rel, 'new_name': new_name})
    except Exception as e:
        return jsonify({'error': f'move/rename error: {str(e)}'}), 500
