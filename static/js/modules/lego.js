// ===== Module: lego (Files) =====
// A real file manager: places sidebar with a disk meter, editable path bar, list (resizable columns) and icon views,
// a command bar, rubber-band / keyboard / touch selection, cut / copy / paste, rename, new folder, drag and drop to move,
// a details pane and context menus. Photos and videos open in the shared viewer from gallery.js.
// Selection is kept as a Set of paths (not row numbers), so it survives re-sorting, renaming and reloads.
var legoCurrentPath = '';
var fm = {
  items: [], shown: [], view: 'list', sortKey: 'name', sortDir: 1, q: '', sel: new Set(), anchor: '', cursor: '', seq: 0,
  places: [], backup: '', trash: '', isRoot: false, disk: null, ptype: 'mouse', loading: false, pick: false, info: false,
  clip: null, cw: { d: 170, s: 90, k: 120 }, good: '', busy: false, drag: null, ta: '', taT: 0,
};
(function () {
  try {
    fm.view = localStorage.getItem('fmView') === 'grid' ? 'grid' : 'list';
    var s = (localStorage.getItem('fmSort') || 'name:1').split(':');
    fm.sortKey = ['name', 'date', 'size', 'kind'].indexOf(s[0]) >= 0 ? s[0] : 'name'; fm.sortDir = s[1] === '-1' ? -1 : 1;
    fm.info = localStorage.getItem('fmInfo') === '1';
    var w = JSON.parse(localStorage.getItem('fmCols') || 'null');
    if (w) ['d', 's', 'k'].forEach(function (k) { if (w[k] >= 60 && w[k] <= 420) fm.cw[k] = w[k]; });
  } catch (_) {}
})();

var FM_IMG = 'jpg jpeg png gif webp bmp heic heif avif'.split(' ');
var FM_VID = 'mp4 mov m4v webm mkv 3gp avi'.split(' ');
var FM_AUD = 'mp3 wav flac ogg m4a aac opus'.split(' ');
var FM_DOC = 'pdf txt md doc docx odt rtf epub'.split(' ');
var FM_ARC = 'zip 7z tar gz rar bz2 xz'.split(' ');
var FM_CODE = 'py js ts html css json xml yaml yml sh c cpp h java rs go'.split(' ');
// a two-tone folder: the back flap is lighter than the front
var FM_FOLDER = '<svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true"><path d="M2.5 6.8a2.3 2.3 0 0 1 2.3-2.3h4.1c.6 0 1.2.3 1.6.8l1 1.2h7.7a2.3 2.3 0 0 1 2.3 2.3v1H2.5z" fill="currentColor" opacity=".55"/><path d="M2.5 9.4a2 2 0 0 1 2-2h15a2 2 0 0 1 2 2v7.8a2.3 2.3 0 0 1-2.3 2.3H4.8a2.3 2.3 0 0 1-2.3-2.3z" fill="currentColor"/></svg>';

function fmEl(id) { return document.getElementById(id); }
function fmSay(t) { var e = fmEl('fm-stl'); if (e) e.textContent = t || ''; }
function fmKindOf(it) {
  if (it.is_dir) return 'Folder';
  var e = it.ext;
  if (FM_IMG.indexOf(e) >= 0) return 'Image';
  if (FM_VID.indexOf(e) >= 0) return 'Video';
  if (FM_AUD.indexOf(e) >= 0) return 'Audio';
  if (e === 'pdf') return 'PDF';
  if (FM_DOC.indexOf(e) >= 0) return 'Document';
  if (FM_ARC.indexOf(e) >= 0) return 'Archive';
  if (FM_CODE.indexOf(e) >= 0) return 'Code';
  return e ? e.toUpperCase() + ' file' : 'File';
}
function fmKindClass(it) { var k = fmKindOf(it); return k.length > 5 && k.slice(-5) === ' file' || k === 'File' ? 'file' : k.toLowerCase(); }
function fmIconOf(it) {
  if (it.is_dir) return FM_FOLDER;
  var k = fmKindOf(it);
  return k === 'Image' ? UI_ICON.image : k === 'Video' ? UI_ICON.video : k === 'Audio' ? UI_ICON.music : (k === 'Document' || k === 'PDF') ? UI_ICON.doc : k === 'Archive' ? UI_ICON.archive : k === 'Code' ? UI_ICON.code : UI_ICON.file;
}
function fmIsMedia(it) { return !it.is_dir && (FM_IMG.indexOf(it.ext) >= 0 || FM_VID.indexOf(it.ext) >= 0); }
function fmShortDate(ts) {
  if (!ts) return '';
  var d = new Date(ts * 1000), n = new Date();
  var s = d.getDate() + ' ' + UI_MONTHS[d.getMonth()] + ' ' + d.getFullYear();
  var h = d.getHours(), m = d.getMinutes();
  return s + (d.getFullYear() === n.getFullYear() ? ', ' + (h % 12 || 12) + ':' + (m < 10 ? '0' : '') + m + ' ' + (h >= 12 ? 'PM' : 'AM') : '');
}
function fmCountText(it) {
  if (!it.is_dir) return uiSize(it.size);
  return it.child_count > 0 ? it.child_count + (it.child_count === 1 ? ' item' : ' items') : it.child_count === 0 ? 'Empty' : '';
}
function fmPlural(n, one, many) { return uiNum(n) + ' ' + (n === 1 ? one : many); }
function fmSelItems() { return fm.shown.filter(function (x) { return fm.sel.has(x.path); }); }
function fmIdx(path) { for (var i = 0; i < fm.shown.length; i++) if (fm.shown[i].path === path) return i; return -1; }
function fmRowOf(path) { var i = fmIdx(path); return i < 0 ? null : fmEl('lego-list').querySelector('.fm-row[data-i="' + i + '"]'); }
function fmLoc(path) { var up = path.split('/').slice(0, -1).join('/'); return (fm.isRoot ? '/' : 'Files/') + up; }
function fmCoarse() { return fm.ptype !== 'mouse'; }

// ── loading ───────────────────────────────────────────────
// loadLego('') is the top of the tree, loadLego() with no argument opens the default place (the home folder).
// opts: silent (do not touch the address), keep (no skeleton: a reload after an operation), select (paths to select afterwards)
async function loadLego(path, opts) {
  opts = opts || {};
  var dflt = path === undefined || path === null;
  path = dflt ? '' : path;
  var seq = ++fm.seq, list = fmEl('lego-list'), top = opts.keep ? list.scrollTop : 0;
  if (!dflt) { legoCurrentPath = path; if (!opts.silent) navSet('lego/' + path); }
  fm.q = ''; var qi = fmEl('fm-q'); if (qi) qi.value = '';
  fm.sel = new Set(opts.select || []); fm.anchor = fm.cursor = (opts.select || [])[0] || ''; fm.loading = true;
  if (!(opts.select && opts.select.length)) fm.pick = false;
  fmCloseMenu(); fmDrawer(false); fmPaintChrome();
  if (!opts.keep) {
    list.className = 'fm-list';
    list.innerHTML = '<div class="fm-skel">' + Array(9).fill('<div class="fm-skel-row"><i></i><b></b><s></s></div>').join('') + '</div>';
    fmSay('');
  }
  try {
    var r = await fetch('/api/lego/browse?' + (dflt ? '' : 'path=' + encodeURIComponent(path) + '&') + 'per_page=5000');
    var data = await r.json();
    if (seq !== fm.seq) return;
    if (data.error) throw new Error(data.error);
    fm.items = data.items || []; fm.loading = false; fm.disk = data.disk || null;
    fm.places = data.places || []; fm.backup = data.backup || ''; fm.trash = data.trash || ''; fm.isRoot = !!data.is_root;
    if (dflt) { legoCurrentPath = data.path || ''; if (!opts.silent) navReplace('lego/' + legoCurrentPath); }
    fm.good = legoCurrentPath;
    fmPaintChrome(data.breadcrumbs);
    fmRender();
    if (opts.keep) list.scrollTop = top;
    if (opts.select && opts.select.length) { var row = fmRowOf(opts.select[0]); if (row) row.scrollIntoView({ block: 'nearest' }); }
    fmLoadCounts(seq);
  } catch (e) {
    if (seq !== fm.seq) return;
    fm.loading = false; fm.items = []; fm.shown = [];
    list.className = 'fm-list';
    list.innerHTML = '<div class="fm-empty"><div class="fm-empty-ico">' + UI_ICON.folder + '</div><div class="fm-empty-t">Couldn\'t open this folder</div><div class="fm-empty-s"></div>' +
      '<div class="fm-empty-b"><button class="ui-btn ui-btn-main" onclick="loadLego(legoCurrentPath,{silent:true})">Try again</button>' +
      (fm.good !== legoCurrentPath ? '<button class="ui-btn" onclick="loadLego(fm.good)">Go back</button>' : '') + '</div></div>';
    list.querySelector('.fm-empty-s').textContent = e.message || 'Something went wrong.';
    fmPaintChrome(); fmPaintCmd(); fmStatus();
  }
}
function fmReload(select) { return loadLego(legoCurrentPath, { silent: true, keep: true, select: select || [] }); }

function fmCrumbs(bc) {
  if (!bc) { bc = [{ name: fm.isRoot ? 'Computer' : 'Files', path: '' }]; legoCurrentPath.split('/').filter(Boolean).forEach(function (p, i, a) { bc.push({ name: p, path: a.slice(0, i + 1).join('/') }); }); }
  var el = fmEl('lego-breadcrumbs');
  el.innerHTML = bc.map(function (b, i) {
    var last = i === bc.length - 1;
    return (i ? '<span class="fm-sep">' + UI_ICON.fwd + '</span>' : '') + '<button class="fm-crumb' + (last ? ' on' : '') + '" data-path="' + escapeHtml(b.path) + '"' + (last ? ' aria-current="page"' : '') + '>' + escapeHtml(b.name) + '</button>';
  }).join('');
  el.scrollLeft = el.scrollWidth;
}
// click on the empty part of the path bar (or Ctrl+L): type a path
function fmPathEdit() {
  var el = fmEl('lego-breadcrumbs'); if (el.querySelector('input')) return;
  el.innerHTML = '<input class="fm-path-in" spellcheck="false" autocomplete="off" aria-label="Path">';
  var inp = el.firstChild; inp.value = (fm.isRoot ? '/' : '') + legoCurrentPath; inp.focus(); inp.select();
  var done = false;
  function end(go) {
    if (done) return; done = true;
    var v = inp.value.trim().replace(/\\/g, '/').replace(/\/+/g, '/');
    if (!fm.isRoot) v = v.replace(/^Files(\/|$)/i, '');
    v = v.replace(/^\/+|\/+$/g, '');
    if (go && v !== legoCurrentPath) loadLego(v); else fmCrumbs();
  }
  inp.addEventListener('keydown', function (e) { e.stopPropagation(); if (e.key === 'Enter') { e.preventDefault(); end(true); } else if (e.key === 'Escape') end(false); });
  inp.addEventListener('blur', function () { end(false); });
}

var FM_PLACE_COLOR = { home: '#4f9cf7', desktop: '#8b7cf6', documents: '#4f8df7', downloads: '#2fb67c', pictures: '#a06cf0', music: '#ec5fa6', videos: '#ef5b6b', backup: '#e8903a', camera: '#3fb6c9', screenshots: '#3fb6c9', trash: '#8a93a3' };
function fmPaintChrome(bc) {
  fmCrumbs(bc);
  fmEl('fm-up').disabled = !legoCurrentPath;
  fmEl('fm-v-list').classList.toggle('on', fm.view === 'list'); fmEl('fm-v-grid').classList.toggle('on', fm.view === 'grid');
  fmEl('fm-back').disabled = !navCanBack();
  fmEl('fm-infob').classList.toggle('on', fm.info);
  var scr = fmEl('lego-screen');
  scr.classList.toggle('picking', fm.pick);
  ['d', 's', 'k'].forEach(function (k) { scr.style.setProperty('--fm-c' + k, fm.cw[k] + 'px'); });
  var info = fmEl('fm-info'); info.hidden = !fm.info; scr.classList.toggle('has-info', fm.info);
  // sidebar
  var cur = legoCurrentPath;
  var ICO = { home: UI_ICON.home, desktop: UI_ICON.file, documents: UI_ICON.doc, downloads: UI_ICON.download, pictures: UI_ICON.image, music: UI_ICON.music, videos: UI_ICON.video, backup: UI_ICON.archive, camera: UI_ICON.camera, screenshots: UI_ICON.screenshot, trash: UI_ICON.trash };
  function place(nm, path, ic, fixed, key) {
    var on = path === cur || (path && cur.indexOf(path + '/') === 0 && !fm.places.some(function (q) { return q.path.length > path.length && (cur === q.path || cur.indexOf(q.path + '/') === 0); }));
    return '<button class="fm-place' + (on ? ' on' : '') + '" data-path="' + escapeHtml(path) + '" data-name="' + escapeHtml(nm) + '"' + (fixed ? ' data-fixed="1"' : '') + (key ? ' style="--pc:' + (FM_PLACE_COLOR[key] || '#f0b13a') + '"' : '') + '>' + ic + '<span>' + escapeHtml(nm) + '</span></button>';
  }
  var h = '<div class="fm-side-scroll"><div class="fm-side-h">Places</div>';
  fm.places.forEach(function (p) { h += place(p.name, p.path, ICO[p.icon] || UI_ICON.folder, p.fixed, p.icon); });
  if (fm.isRoot) h += place('Computer', '', UI_ICON.server || UI_ICON.folder, true, 'backup');
  h += '<div class="fm-side-h">Library</div><button class="fm-place" data-go="gallery" style="--pc:#a06cf0">' + UI_ICON.photos + '<span>Gallery</span></button></div>';
  if (fm.disk && fm.disk.total) {
    var used = fm.disk.total - fm.disk.free, pct = Math.max(1, Math.min(100, Math.round(used / fm.disk.total * 100)));
    h += '<div class="fm-disk' + (pct >= 90 ? ' full' : '') + '"><div class="fm-disk-t">' + UI_ICON.server + '<span>Storage</span></div><div class="fm-disk-bar"><i style="width:' + pct + '%"></i></div><div class="fm-disk-s">' + uiSize(fm.disk.free) + ' free of ' + uiSize(fm.disk.total) + '</div></div>';
  }
  fmEl('fm-side').innerHTML = h;
}
function fmDrawer(open) { fmEl('lego-screen').classList.toggle('side-open', !!open); }
function fmSearching(on) {
  var bar = fmEl('fm-bar'), q = fmEl('fm-q');
  if (on) bar.classList.add('searching'); else if (!q.value) bar.classList.remove('searching');
}

// ── sort / filter ─────────────────────────────────────────
function fmSorted() {
  var q = fm.q.trim().toLowerCase(), k = fm.sortKey, d = fm.sortDir;
  var a = fm.items.filter(function (x) { return !q || x.name.toLowerCase().indexOf(q) >= 0; });
  a.sort(function (x, y) {
    if (x.is_dir !== y.is_dir) return x.is_dir ? -1 : 1;                                       // folders first
    var c = 0;
    if (k === 'date') c = (x.mtime || 0) - (y.mtime || 0);
    else if (k === 'size') c = (x.is_dir ? 0 : x.size) - (y.is_dir ? 0 : y.size);
    else if (k === 'kind') c = fmKindOf(x).localeCompare(fmKindOf(y));
    if (!c) c = x.name.localeCompare(y.name, undefined, { numeric: true, sensitivity: 'base' });
    return c * d;
  });
  return a;
}

function fmRowHtml(it, i) {
  var thumb = fmIsMedia(it) ? '<img class="fm-th-img" data-src="' + uiThumbUrl({ p: it.path, m: it.mtime }, fm.view === 'grid' ? 'md' : 'sm') + '" alt="" draggable="false" decoding="async">' : '';
  var cnt = fmCountText(it), when = fmShortDate(it.mtime), cut = fm.clip && fm.clip.move && fm.clip.set.has(it.path);
  return '<div class="fm-row k-' + fmKindClass(it) + (fm.sel.has(it.path) ? ' sel' : '') + (cut ? ' cut' : '') + '" role="option" tabindex="-1" data-i="' + i + '"' + (it.is_dir ? ' data-dir="1"' : '') + (fm.ptype === 'mouse' && matchMedia('(pointer:fine)').matches ? ' draggable="true"' : '') + '>' +
    '<span class="fm-lead"><span class="fm-icon"><span class="fm-ico">' + fmIconOf(it) + '</span>' + thumb + '</span><span class="fm-chk">' + UI_ICON.check + '</span></span>' +
    '<span class="fm-name"><b>' + escapeHtml(it.name) + '</b><small>' + escapeHtml(when + (cnt ? ' · ' + cnt : '')) + '</small></span>' +
    '<span class="fm-date">' + escapeHtml(when) + '</span><span class="fm-size">' + escapeHtml(cnt) + '</span><span class="fm-kind">' + escapeHtml(fmKindOf(it)) + '</span></div>';
}

function fmRender() {
  var list = fmEl('lego-list'), items = fm.shown = fmSorted(), top = list.scrollTop;
  list.className = 'fm-list ' + (fm.view === 'grid' ? 'fm-grid' : 'fm-rows');
  var html = [];
  if (fm.view === 'list' && items.length) {
    var th = function (key, label, col) {
      return '<div class="fm-hc' + (col ? ' hc-' + col : '') + '"><button class="fm-th' + (fm.sortKey === key ? ' on' : '') + '" data-sort="' + key + '">' + label + (fm.sortKey === key ? '<span class="fm-arrow">' + (fm.sortDir > 0 ? UI_ICON.up : UI_ICON.down) + '</span>' : '') + '</button>' + (col ? '<i class="fm-rs" data-col="' + col + '"></i>' : '') + '</div>';
    };
    html.push('<div class="fm-head"><span></span>' + th('name', 'Name') + th('date', 'Date Modified', 'd') + th('size', 'Size', 's') + th('kind', 'Kind', 'k') + '</div>');
  }
  if (!items.length) {
    list.innerHTML = '<div class="fm-empty"><div class="fm-empty-ico">' + (fm.q ? UI_ICON.search : UI_ICON.folder) + '</div><div class="fm-empty-t">' + (fm.q ? 'No results' : 'This folder is empty') + '</div><div class="fm-empty-s">' + (fm.q ? 'Nothing here matches "' + escapeHtml(fm.q) + '".' : (fm.clip ? 'Paste what you copied, or drop files here to upload them.' : 'Drop files here to upload them, or make a new folder.')) + '</div></div>';
    fmPaintSel(); return;
  }
  items.forEach(function (it, i) { html.push(fmRowHtml(it, i)); });
  list.innerHTML = html.join('');
  list.scrollTop = top;
  fmThumbs();
  fmPaintSel();
}

function fmThumbs() {
  if (fm.obs) fm.obs.disconnect();
  fm.obs = new IntersectionObserver(function (es) {
    es.forEach(function (e) {
      if (!e.isIntersecting) return;
      var im = e.target; fm.obs.unobserve(im);
      im.onload = function () { im.classList.add('in'); }; im.onerror = function () { im.remove(); };
      im.src = im.dataset.src; im.removeAttribute('data-src');
    });
  }, { root: fmEl('lego-list'), rootMargin: '400px 0px' });
  fmEl('lego-list').querySelectorAll('img[data-src]').forEach(function (im) { fm.obs.observe(im); });
}

function fmStatus() {
  var n = fm.shown.length, sel = fmSelItems(), t = '';
  if (sel.length) {
    var sz = sel.reduce(function (a, x) { return a + (x.is_dir ? 0 : x.size); }, 0);
    t = uiNum(sel.length) + ' of ' + fmPlural(n, 'item', 'items') + ' selected' + (sz ? ' · ' + uiSize(sz) : '');
  } else {
    var total = fm.shown.reduce(function (a, x) { return a + (x.is_dir ? 0 : x.size); }, 0);
    t = fmPlural(n, 'item', 'items') + (total ? ' · ' + uiSize(total) : '');
    if (fm.q) t += ' matching "' + fm.q + '"';
  }
  if (fm.clip) t += '  ·  ' + (fm.clip.move ? 'Cut ' : 'Copied ') + fmPlural(fm.clip.paths.length, 'item', 'items');
  fmSay(t);
  var r = fmEl('fm-str'); if (r) r.textContent = fm.disk ? uiSize(fm.disk.free) + ' free' : '';
}

// number of items inside each folder, fetched quietly (cached on the server)
async function fmLoadCounts(seq) {
  var dirs = fm.items.filter(function (x) { return x.is_dir && x.child_count === -1; }), i = 0;
  async function worker() {
    while (i < dirs.length) {
      if (seq !== fm.seq) return;
      var d = dirs[i++];
      try {
        var info = await (await fetch('/api/lego/folder_info?path=' + encodeURIComponent(d.path))).json();
        if (seq !== fm.seq) return;
        d.child_count = info.child_count || 0;
        var idx = fm.shown.indexOf(d);
        if (idx >= 0) {
          var row = fmEl('lego-list').querySelector('.fm-row[data-i="' + idx + '"]');
          if (row) { var txt = fmCountText(d); row.querySelector('.fm-size').textContent = txt; row.querySelector('small').textContent = fmShortDate(d.mtime) + ' · ' + txt; }
        }
      } catch (_) {}
    }
    if (seq === fm.seq) fmPaintInfo();
  }
  await Promise.all([worker(), worker(), worker()]);
}

function fmSearch(v) { fm.q = v || ''; fm.sel = new Set(); fm.pick = false; fmRender(); }
function fmSetView(v) { fm.view = v; try { localStorage.setItem('fmView', v); } catch (_) {} fmPaintChrome(); fmRender(); }
function fmSetSort(key) {
  if (fm.sortKey === key) fm.sortDir = -fm.sortDir; else { fm.sortKey = key; fm.sortDir = key === 'date' || key === 'size' ? -1 : 1; }
  try { localStorage.setItem('fmSort', fm.sortKey + ':' + fm.sortDir); } catch (_) {}
  fmRender();
}
function fmBack() { if (navCanBack()) history.back(); else if (legoCurrentPath) fmUp(); }
function fmUp() { if (!legoCurrentPath) return; loadLego(legoCurrentPath.split('/').slice(0, -1).join('/')); }

// ── selection ─────────────────────────────────────────────
function fmPaintSel() {
  var list = fmEl('lego-list');
  list.querySelectorAll('.fm-row').forEach(function (r) { var it = fm.shown[+r.dataset.i]; r.classList.toggle('sel', !!it && fm.sel.has(it.path)); });
  if (fm.pick && !fm.sel.size) fm.pick = false;
  fmEl('lego-screen').classList.toggle('picking', fm.pick);
  fmPaintCmd(); fmStatus(); fmPaintInfo();
}
function fmSelectOnly(path, scroll) {
  fm.sel = new Set(path ? [path] : []); fm.anchor = fm.cursor = path || ''; fmPaintSel();
  if (scroll && path) { var r = fmRowOf(path); if (r) r.scrollIntoView({ block: 'nearest' }); }
}
function fmSelectRange(toPath) {
  var a = fmIdx(fm.anchor || toPath), b = fmIdx(toPath); if (a < 0) a = b;
  fm.sel = new Set(); for (var k = Math.min(a, b); k <= Math.max(a, b); k++) fm.sel.add(fm.shown[k].path);
  fm.cursor = toPath; fmPaintSel();
  var r = fmRowOf(toPath); if (r) r.scrollIntoView({ block: 'nearest' });
}
function fmToggle(path) {
  if (fm.sel.has(path)) fm.sel.delete(path); else fm.sel.add(path);
  fm.anchor = fm.cursor = path; fmPaintSel();
}
function fmSelectAll() { fm.sel = new Set(fm.shown.map(function (x) { return x.path; })); fmPaintSel(); }

function fmOpenItem(it) {
  if (!it) return;
  if (it.is_dir) { loadLego(it.path); return; }
  if (fmIsMedia(it)) {
    var media = fm.shown.filter(fmIsMedia).map(fmAsMedia), idx = media.findIndex(function (m) { return m.p === it.path; }), folder = legoCurrentPath;
    glbOpen({
      items: media, index: Math.max(0, idx),
      hash: function (m) { return 'view/' + m.p; },
      parent: function () { return 'lego/' + folder; },
      onDelete: function (done) { fmForget(new Set(done.map(function (x) { return x.p; }))); },
    });
    return;
  }
  uiDownload([{ p: it.path }]);
  uiToast('Downloading ' + it.name + '…');
}
function fmAsMedia(it) { return { p: it.path, t: FM_VID.indexOf(it.ext) >= 0 ? 'v' : 'i', d: it.taken || it.mtime || 0, s: it.size, m: it.mtime }; }
// after the viewer / gallery deletes things
function fmForget(gone) {
  fm.items = fm.items.filter(function (x) { return !gone.has(x.path); });
  gone.forEach(function (p) { fm.sel.delete(p); });
  if (navActive('lego-screen')) fmRender();
}

// ── command bar: what you can do right now ────────────────
function fmPaintCmd() {
  var el = fmEl('fm-cmd'); if (!el) return;
  var sel = fmSelItems(), one = sel.length === 1 ? sel[0] : null, files = sel.filter(function (s) { return !s.is_dir; });
  function b(act, ic, label, o) { o = o || {}; return '<button class="fm-cb' + (o.danger ? ' danger' : '') + (o.main ? ' main' : '') + '" data-act="' + act + '" title="' + (o.tip || label) + '">' + UI_ICON[ic] + '<span>' + label + '</span></button>'; }
  var sep = '<i class="fm-csep"></i>', h = '';
  if (sel.length) {
    if (fm.pick) h += b('done', 'close', uiNum(sel.length) + ' selected', { tip: 'Done' }); else h += '<span class="fm-ccount">' + uiNum(sel.length) + ' selected</span>';
    h += sep;
    if (one) h += b('open', one.is_dir ? 'open' : fmIsMedia(one) ? 'image' : 'download', one.is_dir ? 'Open' : fmIsMedia(one) ? 'View' : 'Download');
    h += b('cut', 'cut', 'Cut', { tip: 'Cut (Ctrl+X)' }) + b('copy', 'copy', 'Copy', { tip: 'Copy (Ctrl+C)' });
    if (one && one.is_dir && fm.clip) h += b('paste-into', 'paste', 'Paste into');
    if (one) h += b('rename', 'edit', 'Rename', { tip: 'Rename (F2)' });
    if (files.length && !(one && !fmIsMedia(one))) h += b('download', 'download', files.length > 1 ? 'Download ' + files.length : 'Download');
    h += b('select-all', 'selectall', 'All', { tip: 'Select all (Ctrl+A)' });
    h += sep + (fmInTrash() ? b('delete', 'trash', 'Delete', { danger: true, tip: 'Delete permanently' }) : b('trash', 'trash', 'Trash', { danger: true, tip: 'Move to Trash (Delete)' }));
    h += '<button class="fm-cb fm-cmore" data-act="more" aria-label="More" title="More">' + UI_ICON.more + '</button>';
  } else {
    h += b('newfolder', 'folderplus', 'New folder', { main: true }) + b('upload', 'upload', 'Upload');
    if (fm.clip) h += sep + b('paste', 'paste', 'Paste ' + fmPlural(fm.clip.paths.length, 'item', 'items'), { main: true, tip: 'Paste here (Ctrl+V)' }) + b('clip-clear', 'close', 'Clear', { tip: 'Forget what was cut or copied' });
    if (fmInTrash()) h += sep + b('empty-trash', 'trash', 'Empty Trash', { danger: true });
  }
  el.innerHTML = h;
  el.classList.toggle('on', !!sel.length);
}

function fmAct(act) {
  var sel = fmSelItems(), one = sel.length === 1 ? sel[0] : null, btn = fmEl('fm-cmd').querySelector('[data-act="more"]');
  switch (act) {
    case 'done': fm.pick = false; fm.sel = new Set(); fmPaintSel(); break;
    case 'open': fmOpenItem(one); break;
    case 'newfolder': fmNewFolder(); break;
    case 'upload': fmUploadPick(); break;
    case 'cut': fmClip(true); break;
    case 'copy': fmClip(false); break;
    case 'paste': fmPasteClip(legoCurrentPath); break;
    case 'paste-into': if (one && one.is_dir) fmPasteClip(one.path); break;
    case 'clip-clear': fmClipClear(); break;
    case 'rename': if (one) fmRename(one); break;
    case 'download': var f = sel.filter(function (s) { return !s.is_dir; }); if (f.length) uiDownload(f.map(function (x) { return { p: x.path }; })); break;
    case 'select-all': fmSelectAll(); break;
    case 'trash': fmTrashSelected(); break;
    case 'delete': fmDeleteSelected(); break;
    case 'empty-trash': fmEmptyTrash(); break;
    case 'info': fm.info = !fm.info; try { localStorage.setItem('fmInfo', fm.info ? '1' : '0'); } catch (_) {} fmPaintChrome(); fmPaintInfo(); break;
    case 'more': if (btn) { var r = btn.getBoundingClientRect(); fmItemMenu(r.right - 220, r.bottom + 6); } break;
  }
}

// ── details pane ──────────────────────────────────────────
function fmPaintInfo() {
  var el = fmEl('fm-info'); if (!el || el.hidden) return;
  var sel = fmSelItems(), h = '';
  function row(k, v, stack) { return v ? '<div class="fm-ir' + (stack ? ' stack' : '') + '"><span>' + k + '</span><b>' + escapeHtml(String(v)) + '</b></div>' : ''; }
  function big(it) {
    var thumb = fmIsMedia(it) ? '<img src="' + uiThumbUrl({ p: it.path, m: it.mtime }, 'md') + '" alt="" onerror="this.remove()">' : '';
    return '<div class="fm-ipv k-' + fmKindClass(it) + '"><span class="fm-ico">' + fmIconOf(it) + '</span>' + thumb + '</div>';
  }
  if (sel.length === 1) {
    var it = sel[0];
    h = big(it) + '<div class="fm-in">' + escapeHtml(it.name) + '</div><div class="fm-ik">' + escapeHtml(fmKindOf(it)) + '</div>' +
      row(it.is_dir ? 'Contains' : 'Size', it.is_dir ? fmCountText(it) : uiSize(it.size) + (it.size >= 1024 ? ' (' + uiNum(it.size) + ' bytes)' : '')) +
      row('Modified', fmShortDate(it.mtime)) + row('Location', fmLoc(it.path), 1) + row('Extension', it.ext ? '.' + it.ext : '');
  } else if (sel.length > 1) {
    var sz = sel.reduce(function (a, x) { return a + (x.is_dir ? 0 : x.size); }, 0), nd = sel.filter(function (x) { return x.is_dir; }).length;
    h = '<div class="fm-ipv k-file"><span class="fm-ico">' + UI_ICON.copy + '</span></div><div class="fm-in">' + fmPlural(sel.length, 'item', 'items') + '</div>' +
      '<div class="fm-ik">' + (nd ? fmPlural(nd, 'folder', 'folders') + ', ' : '') + fmPlural(sel.length - nd, 'file', 'files') + '</div>' + row('Total size', sz ? uiSize(sz) : '') + row('Location', fmLoc(sel[0].path), 1);
  } else {
    var name = legoCurrentPath ? legoCurrentPath.split('/').pop() : (fm.isRoot ? 'Computer' : 'Files'), total = fm.items.reduce(function (a, x) { return a + (x.is_dir ? 0 : x.size); }, 0);
    h = '<div class="fm-ipv k-folder"><span class="fm-ico">' + FM_FOLDER + '</span></div><div class="fm-in">' + escapeHtml(name) + '</div><div class="fm-ik">Folder</div>' +
      row('Contains', fmPlural(fm.items.length, 'item', 'items')) + row('Files total', total ? uiSize(total) : '') + row('Location', legoCurrentPath ? fmLoc(legoCurrentPath) : '', 1) +
      (fm.disk ? row('Free space', uiSize(fm.disk.free) + ' of ' + uiSize(fm.disk.total)) : '');
  }
  el.innerHTML = '<div class="fm-info-h"><span>Details</span><button class="fm-ib" data-act="info" aria-label="Close">' + UI_ICON.close + '</button></div><div class="fm-info-b">' + h + '</div>';
}

// ── operations ────────────────────────────────────────────
async function fmTrashSelected() {
  var sel = fmSelItems();
  if (!sel.length) return;
  var folders = sel.filter(function (x) { return x.is_dir; }).length;
  var ok = await uiConfirm(sel.length === 1 ? 'Move "' + sel[0].name + '" to Trash?' : 'Move ' + sel.length + ' items to Trash?', 'They go to "file types / trash", so you can still get them back.' + (folders ? ' Folders are moved with everything inside.' : ''), 'Move to Trash', true);
  if (!ok) return;
  var done = new Set(), bad = [];
  for (var i = 0; i < sel.length; i++) { var r = await uiPost('/api/lego/trash', { path: sel[i].path }); if (r.ok) done.add(sel[i].path); else bad.push(r.error || 'failed'); }
  if (done.size) { fmForget(done); if (typeof glForget === 'function') glForget(Array.from(done).map(function (p) { return { p: p }; })); uiToast(done.size === 1 ? 'Moved to Trash' : done.size + ' moved to Trash'); }
  if (bad.length) setTimeout(function () { uiToast(bad.length === 1 ? bad[0] : bad.length + ' could not be moved: ' + bad[0]); }, done.size ? 2800 : 0);
}
function fmInTrash() { return !!fm.trash && (legoCurrentPath === fm.trash || legoCurrentPath.indexOf(fm.trash + '/') === 0); }
// in the Trash folder: delete the selected files for good (the server refuses anything that is not in Trash)
async function fmDeleteSelected() {
  var sel = fmSelItems();
  if (!sel.length) { uiToast('Select something first (Ctrl+A selects all)'); return; }
  var ok = await uiConfirm('Delete ' + (sel.length === 1 ? '"' + sel[0].name + '"' : sel.length + ' items') + ' permanently?', 'This cannot be undone.', 'Delete', true);
  if (!ok) return;
  var gone = new Set(), bad = 0;
  for (var i = 0; i < sel.length; i += 200) {
    var part = sel.slice(i, i + 200), r = await uiPost('/api/lego/delete', { paths: part.map(function (f) { return f.path; }) });
    if (r.ok && r.data.failed === 0) part.forEach(function (f) { gone.add(f.path); }); else { fmReload(); bad += ((r.data && r.data.failed) || part.length); }
  }
  if (gone.size) { fmForget(gone); uiToast(gone.size === 1 ? 'Deleted' : gone.size + ' deleted'); }
  if (bad) uiToast(bad + ' could not be deleted');
}
async function fmEmptyTrash() {
  var ok = await uiConfirm('Empty the Trash?', 'Every file in it is deleted for good. This cannot be undone.', 'Empty Trash', true);
  if (!ok) return;
  var r = await uiPost('/api/lego/delete', { empty: true });
  fmReload();
  uiToast(r.ok ? (r.data.deleted ? r.data.deleted + ' deleted' : 'Trash is already empty') : 'Could not empty the Trash');
}
async function markFolderDone() {
  if (!legoCurrentPath) return;
  var name = legoCurrentPath.split('/').pop();
  var ok = await uiConfirm('Mark "' + name + '" as done?', 'It is renamed to "' + name + ' (MANUALLY DONE)" and moved into "manually done".', 'Mark as done', false);
  if (!ok) return;
  var r = await uiPost('/api/lego/mark_done', { path: legoCurrentPath });
  if (!r.ok) { uiToast(r.error); return; }
  loadLego(legoCurrentPath.split('/').slice(0, -1).join('/'));
}

// new folder: made at once as "New folder", then its name is ready to type over
async function fmNewFolder() {
  if (fm.loading) return;
  var have = new Set(fm.items.map(function (x) { return x.name; })), name = 'New folder', n = 2;
  while (have.has(name)) name = 'New folder (' + n++ + ')';
  var r = await uiPost('/api/files/mkdir', { dir: legoCurrentPath, name: name });
  if (!r.ok) { uiToast(r.error); return; }
  await fmReload([r.data.path]);
  var it = fm.shown.find(function (x) { return x.path === r.data.path; });
  if (it) fmRename(it);
}
// rename in place: the name turns into a text box (the extension is left out of the selection)
function fmRename(it) {
  var row = fmRowOf(it.path); if (!row) return;
  var b = row.querySelector('.fm-name b'); if (!b) return;
  var inp = document.createElement('input'), done = false;
  inp.className = 'fm-rn'; inp.value = it.name; inp.spellcheck = false; inp.autocomplete = 'off'; inp.maxLength = 200; inp.setAttribute('aria-label', 'Name');
  b.replaceWith(inp); row.classList.add('renaming'); row.draggable = false;
  inp.focus();
  var dot = it.is_dir ? -1 : it.name.lastIndexOf('.'); inp.setSelectionRange(0, dot > 0 ? dot : it.name.length);
  async function commit(save) {
    if (done) return; done = true;
    var v = inp.value.trim();
    if (!save || !v || v === it.name) { fmRender(); return; }
    var r = await uiPost('/api/files/rename', { path: it.path, name: v });
    if (!r.ok) { uiToast(r.error); fmRender(); return; }
    await fmReload([r.data.path]);
  }
  ['click', 'dblclick', 'pointerdown', 'contextmenu'].forEach(function (t) { inp.addEventListener(t, function (e) { e.stopPropagation(); }); });
  inp.addEventListener('keydown', function (e) { e.stopPropagation(); if (e.key === 'Enter') { e.preventDefault(); commit(true); } else if (e.key === 'Escape') { e.preventDefault(); commit(false); } });
  inp.addEventListener('blur', function () { commit(true); });
}

// cut / copy / paste (the clipboard is kept in the page: it works across folders, not across reloads)
function fmClip(move) {
  var sel = fmSelItems(); if (!sel.length) return;
  fm.clip = { paths: sel.map(function (x) { return x.path; }), set: new Set(sel.map(function (x) { return x.path; })), move: move };
  uiToast((move ? 'Cut ' : 'Copied ') + fmPlural(sel.length, 'item', 'items'));
  fm.pick = false; fm.sel = new Set(); fmRender();
}
function fmClipClear() { fm.clip = null; fmRender(); }
async function fmPasteClip(dir) {
  if (!fm.clip) { uiToast('Nothing to paste: cut or copy something first'); return; }
  var c = fm.clip, ok = await fmPaste(c.paths, dir, c.move);
  if (ok && c.move) fm.clip = null;
  if (ok) fmRender();
}
// copy or move paths into a folder. Resolves true when at least one item went through.
async function fmPaste(paths, dir, move) {
  if (fm.busy) { uiToast('Still working on the last one'); return false; }
  fm.busy = true; fmSay((move ? 'Moving ' : 'Copying ') + fmPlural(paths.length, 'item', 'items') + '…');
  var r = await uiPost('/api/files/paste', { paths: paths, dir: dir, move: move });
  fm.busy = false;
  if (!r.ok) { uiToast(r.error); fmStatus(); return false; }
  var res = r.data.results || [], bad = res.filter(function (x) { return x.error; }), good = res.filter(function (x) { return x.to; });
  var msg = (move ? 'Moved ' : 'Copied ') + fmPlural(good.length, 'item', 'items') + (dir !== legoCurrentPath ? ' to ' + (dir ? dir.split('/').pop() : 'the top') : '');
  if (bad.length) msg += ' · ' + bad.length + ' failed: ' + bad[0].error;
  uiToast(msg);
  await fmReload(dir === legoCurrentPath ? good.map(function (x) { return x.to; }) : []);
  return true;
}

// ── menus ─────────────────────────────────────────────────
function fmCloseMenu() { var m = fmEl('fm-menu'); if (m) m.hidden = true; }
function fmShowMenu(x, y, entries) {
  var m = fmEl('fm-menu'); m.innerHTML = '';
  entries.forEach(function (e) {
    if (e === '-') { m.appendChild(document.createElement('hr')); return; }
    var b = document.createElement('button'); b.className = e.danger ? 'danger' : ''; if (e.on) b.classList.add('on');
    b.innerHTML = (e.icon || '<span class="fm-mi"></span>') + '<span class="fm-ml"></span>' + (e.hint ? '<span class="fm-hint"></span>' : '');
    b.querySelector('.fm-ml').textContent = e.label; if (e.hint) b.querySelector('.fm-hint').textContent = e.hint;
    b.addEventListener('click', function () { fmCloseMenu(); e.run(); });
    m.appendChild(b);
  });
  m.hidden = false;
  var r = m.getBoundingClientRect();
  m.style.left = Math.max(6, Math.min(x, window.innerWidth - r.width - 6)) + 'px';
  m.style.top = Math.max(6, Math.min(y, window.innerHeight - r.height - 6)) + 'px';
  var f = m.querySelector('button'); if (f && !fmCoarse()) f.focus({ preventScroll: true });
}
function fmSortMenu(ev) {
  var r = ev.currentTarget.getBoundingClientRect();
  var names = { name: 'Name', date: 'Date Modified', size: 'Size', kind: 'Kind' };
  var e = Object.keys(names).map(function (k) { return { label: names[k] + (fm.sortKey === k ? (fm.sortDir > 0 ? '  ↑' : '  ↓') : ''), on: fm.sortKey === k, run: function () { fmSetSort(k); } }; });
  fmShowMenu(r.right - 190, r.bottom + 6, e);
}
// ── upload: send files from this device into the folder that is open ──────────
var fmUpl = { busy: false };
function fmUploadPick() {
  if (fmUpl.busy) { uiToast('An upload is already running'); return; }
  var inp = document.createElement('input');
  inp.type = 'file'; inp.multiple = true;
  inp.onchange = function () { if (inp.files && inp.files.length) fmUploadFiles(Array.from(inp.files)); };
  inp.click();
}
function fmUploadOne(file, dir, onProgress) {
  return new Promise(function (resolve, reject) {
    var x = new XMLHttpRequest();
    x.open('PUT', '/api/files/upload?dir=' + encodeURIComponent(dir) + '&name=' + encodeURIComponent(file.name));
    var tok = sessionStorage.getItem('reclmr_session'), csrf = sessionStorage.getItem('reclmr_csrf');
    if (tok) x.setRequestHeader('Authorization', 'Bearer ' + tok);
    if (csrf) x.setRequestHeader('X-CSRF-Token', csrf);
    x.setRequestHeader('Content-Type', 'application/octet-stream');
    x.withCredentials = true;
    x.upload.onprogress = function (e) { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    x.onload = function () {
      var d = {}; try { d = JSON.parse(x.responseText); } catch (_) {}
      if (x.status >= 200 && x.status < 300) resolve(d); else reject(new Error(d.error || 'HTTP ' + x.status));
    };
    x.onerror = function () { reject(new Error('Connection lost')); };
    x.send(file);
  });
}
async function fmUploadFiles(files) {
  if (fmUpl.busy || !files.length) return;
  fmUpl.busy = true;
  var dir = legoCurrentPath, ok = 0, renamed = 0, failed = [], names = [];
  for (var i = 0; i < files.length; i++) {
    var f = files[i], pre = 'Uploading ' + (i + 1) + ' of ' + files.length + ' · ' + f.name + ' · ';
    fmSay(pre + '0%');
    try {
      var d = await fmUploadOne(f, dir, function (p) { fmSay(pre + Math.round(p * 100) + '%'); });
      ok++; if (d.name && d.name !== f.name) renamed++;
      if (d.name) names.push((dir ? dir + '/' : '') + d.name);
    } catch (e) { failed.push(f.name + ' (' + e.message + ')'); }
  }
  fmUpl.busy = false;
  if (legoCurrentPath === dir) fmReload(names); else fmStatus();
  var msg = ok ? 'Uploaded ' + ok + (ok === 1 ? ' file' : ' files') + (renamed ? ' (' + renamed + ' renamed, the name was taken)' : '') : 'Nothing uploaded';
  if (failed.length) msg += '. Failed: ' + failed.slice(0, 2).join(', ') + (failed.length > 2 ? ' and ' + (failed.length - 2) + ' more' : '');
  uiToast(msg);
}
// drop files from the desktop onto the file list
(function () {
  function hasFiles(e) { return e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], 'Files') >= 0; }
  document.addEventListener('dragover', function (e) {
    var m = document.getElementById('lego-screen');
    if (!m || !m.classList.contains('active') || !hasFiles(e)) return;
    e.preventDefault(); m.classList.add('fm-drop');
  });
  document.addEventListener('dragleave', function (e) { if (!e.relatedTarget) { var m = document.getElementById('lego-screen'); if (m) m.classList.remove('fm-drop'); } });
  document.addEventListener('drop', function (e) {
    var m = document.getElementById('lego-screen');
    if (!m || !m.classList.contains('active') || !hasFiles(e)) return;
    e.preventDefault(); m.classList.remove('fm-drop');
    fmUploadFiles(Array.from(e.dataTransfer.files).filter(function (f) { return f.size > 0 || f.type; }));        // folders arrive as empty "files": skipped
  });
})();

// ── Places: add a folder to the sidebar, or take one off ──
function fmIsPlace(path) { return fm.places.some(function (p) { return p.path === path; }); }
async function fmPlaceEdit(action, path, name) {
  var r = await uiPost('/api/files/places', { action: action, path: path, name: name });
  if (!r.ok) { uiToast(r.error || 'Could not change Places'); return; }
  await fmReload();
  uiToast(action === 'add' ? 'Added to Places' : action === 'reset' ? 'Places reset' : 'Removed from Places');
}
function fmPlaceMenu(ev, btn) {
  ev.preventDefault();
  var e = [{ label: 'Open', icon: UI_ICON.open, run: function () { loadLego(btn.dataset.path); } }];
  if (fm.clip) e.push({ label: 'Paste into this folder', icon: UI_ICON.paste, run: function () { fmPasteClip(btn.dataset.path); } });
  e.push('-');
  if (!btn.dataset.fixed) e.push({ label: 'Remove from Places', icon: UI_ICON.trash, danger: true, run: function () { fmPlaceEdit('remove', btn.dataset.path, btn.dataset.name); } });
  e.push({ label: 'Reset Places', icon: UI_ICON.refresh, run: function () { fmPlaceEdit('reset', '', ''); } });
  fmShowMenu(ev.clientX, ev.clientY, e);
}
function fmFolderEntries() {
  return [
    { label: 'New folder', icon: UI_ICON.folderplus, run: fmNewFolder },
    fm.clip ? { label: 'Paste', icon: UI_ICON.paste, hint: 'Ctrl+V', run: function () { fmPasteClip(legoCurrentPath); } } : null,
    { label: 'Upload files…', icon: UI_ICON.upload, run: fmUploadPick },
    '-',
    { label: 'Select all', icon: UI_ICON.selectall, hint: 'Ctrl+A', run: fmSelectAll },
    { label: fm.info ? 'Hide details' : 'Show details', icon: UI_ICON.panel, hint: 'Alt+Enter', run: function () { fmAct('info'); } },
    { label: 'Refresh', icon: UI_ICON.refresh, run: function () { fmReload(); } },
  ];
}
function fmMoreMenu(ev) {
  var r = ev.currentTarget.getBoundingClientRect(), e = fmFolderEntries().concat([
    '-',
    legoCurrentPath ? (fmIsPlace(legoCurrentPath) ? { label: 'Remove this folder from Places', icon: UI_ICON.folder, run: function () { fmPlaceEdit('remove', legoCurrentPath, (fm.places.filter(function (p) { return p.path === legoCurrentPath; })[0] || {}).name); } }
      : { label: 'Add this folder to Places', icon: UI_ICON.folder, run: function () { fmPlaceEdit('add', legoCurrentPath, ''); } }) : null,
    fmInTrash() ? { label: 'Empty Trash…', icon: UI_ICON.trash, danger: true, run: fmEmptyTrash } : null,
    { label: 'Swipe to clean up this folder', icon: UI_ICON.swipe, run: function () { openSwipeMode(legoCurrentPath); } },
    { label: 'Open Gallery', icon: UI_ICON.photos, run: function () { glOpen(); } },
  ]);
  if (fm.backup && legoCurrentPath.indexOf(fm.backup + '/') === 0 && legoCurrentPath.indexOf(fm.backup + '/manually done') !== 0) e.push('-', { label: 'Mark folder as done…', icon: UI_ICON.check, run: markFolderDone });
  fmShowMenu(r.right - 250, r.bottom + 6, e.filter(Boolean));
}
function fmItemMenu(x, y) {
  var sel = fmSelItems(), one = sel.length === 1 ? sel[0] : null, files = sel.filter(function (s) { return !s.is_dir; });
  if (!sel.length) { fmShowMenu(x, y, fmFolderEntries().filter(Boolean)); return; }
  var e = [];
  if (one) e.push({ label: one.is_dir ? 'Open' : fmIsMedia(one) ? 'View' : 'Download', icon: one.is_dir ? UI_ICON.open : fmIsMedia(one) ? UI_ICON.image : UI_ICON.download, hint: 'Enter', run: function () { fmOpenItem(one); } });
  if (one && one.is_dir && fm.clip) e.push({ label: 'Paste into folder', icon: UI_ICON.paste, run: function () { fmPasteClip(one.path); } });
  if (files.length && !(one && !fmIsMedia(one))) e.push({ label: files.length > 1 ? 'Download ' + files.length + ' files' : 'Download', icon: UI_ICON.download, run: function () { uiDownload(files.map(function (f) { return { p: f.path }; })); } });
  e.push('-', { label: 'Cut', icon: UI_ICON.cut, hint: 'Ctrl+X', run: function () { fmClip(true); } }, { label: 'Copy', icon: UI_ICON.copy, hint: 'Ctrl+C', run: function () { fmClip(false); } });
  if (one) e.push({ label: 'Rename…', icon: UI_ICON.edit, hint: 'F2', run: function () { fmRename(one); } });
  if (one && one.is_dir && !fmIsPlace(one.path)) e.push({ label: 'Add to Places', icon: UI_ICON.folder, run: function () { fmPlaceEdit('add', one.path, one.name); } });
  e.push({ label: fm.info ? 'Hide details' : 'Properties', icon: UI_ICON.info, hint: 'Alt+Enter', run: function () { fmAct('info'); } });
  e.push('-', fmInTrash() ? { label: 'Delete permanently…', icon: UI_ICON.trash, danger: true, hint: 'Del', run: fmDeleteSelected } : { label: 'Move to Trash', icon: UI_ICON.trash, danger: true, hint: 'Del', run: fmTrashSelected });
  fmShowMenu(x, y, e);
}

// ── events ────────────────────────────────────────────────
function fmDragDir(e) {                                   // the folder an internal drag is over, or null
  if (!fm.drag) return null;
  var t = e.target.closest && e.target.closest('.fm-row[data-dir], .fm-place[data-path], .fm-crumb[data-path]');
  if (!t) return null;
  var dir = t.classList.contains('fm-row') ? (fm.shown[+t.dataset.i] || {}).path : t.dataset.path;
  if (dir === undefined || dir === legoCurrentPath && !t.classList.contains('fm-row')) return null;
  if (fm.drag.indexOf(dir) >= 0) return null;               // onto itself
  return { el: t, dir: dir };
}
function fmDragClear() { document.querySelectorAll('.fm-screen .drop').forEach(function (x) { x.classList.remove('drop'); }); }

function fmInit() {
  var list = fmEl('lego-list'), screen = fmEl('lego-screen');
  if (!list) return;
  screen.addEventListener('pointerdown', function (e) { fm.ptype = e.pointerType || 'mouse'; }, true);

  // click / tap
  list.addEventListener('click', function (e) {
    var row = e.target.closest('.fm-row'); if (!row) return;
    var it = fm.shown[+row.dataset.i]; if (!it) return;
    if (fmCoarse()) { if (fm.pick) { fmToggle(it.path); } else fmOpenItem(it); return; }
    if (e.shiftKey && fm.anchor) fmSelectRange(it.path);
    else if (e.ctrlKey || e.metaKey) fmToggle(it.path);
    else fmSelectOnly(it.path);
  });
  list.addEventListener('dblclick', function (e) { var row = e.target.closest('.fm-row'); if (row && !fmCoarse()) fmOpenItem(fm.shown[+row.dataset.i]); });
  list.addEventListener('contextmenu', function (e) {
    e.preventDefault();
    if (fmCoarse()) return;                                   // touch: a long press picks (the menu is under the command bar's ⋯)
    var row = e.target.closest('.fm-row');
    if (row) { var it = fm.shown[+row.dataset.i]; if (!fm.sel.has(it.path)) fmSelectOnly(it.path); }
    else { fmSelectOnly(''); }
    fmItemMenu(e.clientX, e.clientY);
  });
  // long press on touch = start picking
  var lp = null;
  list.addEventListener('touchstart', function (e) {
    var row = e.target.closest('.fm-row'); if (!row || e.touches.length > 1) return;
    var t = e.touches[0], it = fm.shown[+row.dataset.i]; if (!it) return;
    lp = { x: t.clientX, y: t.clientY, it: it };
    lp.timer = setTimeout(function () { if (!lp) return; fm.ptype = 'touch'; fm.pick = true; if (!fm.sel.has(it.path)) { fm.sel.add(it.path); fm.anchor = fm.cursor = it.path; } fmPaintSel(); uiBuzz(14); lp.fired = true; }, 450);
  }, { passive: true });
  list.addEventListener('touchmove', function (e) { if (lp && !lp.fired) { var t = e.touches[0]; if (Math.hypot(t.clientX - lp.x, t.clientY - lp.y) > 9) { clearTimeout(lp.timer); lp = null; } } }, { passive: true });
  list.addEventListener('touchend', function (e) { if (lp) { clearTimeout(lp.timer); if (lp.fired && e.cancelable) e.preventDefault(); lp = null; } });
  list.addEventListener('touchcancel', function () { if (lp) { clearTimeout(lp.timer); lp = null; } });

  // sorting and column widths
  list.addEventListener('click', function (e) { var b = e.target.closest('[data-sort]'); if (b) fmSetSort(b.dataset.sort); });
  list.addEventListener('pointerdown', function (e) {
    var h = e.target.closest('.fm-rs'); if (!h) return;
    e.preventDefault(); e.stopPropagation();
    var col = h.dataset.col, x0 = e.clientX, w0 = fm.cw[col], scr = fmEl('lego-screen');
    h.setPointerCapture(e.pointerId); h.classList.add('on');
    function move(ev) { fm.cw[col] = Math.max(60, Math.min(420, w0 + (x0 - ev.clientX))); scr.style.setProperty('--fm-c' + col, fm.cw[col] + 'px'); }
    function up() { h.removeEventListener('pointermove', move); h.removeEventListener('pointerup', up); h.removeEventListener('pointercancel', up); h.classList.remove('on'); try { localStorage.setItem('fmCols', JSON.stringify(fm.cw)); } catch (_) {} }
    h.addEventListener('pointermove', move); h.addEventListener('pointerup', up); h.addEventListener('pointercancel', up);
  });

  // rubber-band selection: drag on the empty space
  var band = null;
  list.addEventListener('pointerdown', function (e) {
    if (e.pointerType !== 'mouse' || e.button !== 0 || e.target.closest('.fm-row, .fm-head, .fm-rn, .fm-empty')) return;
    var r = list.getBoundingClientRect();
    if (e.clientX > r.left + list.clientWidth) return;                                   // the scrollbar
    band = { x: e.clientX - r.left + list.scrollLeft, y: e.clientY - r.top + list.scrollTop, add: e.ctrlKey || e.metaKey || e.shiftKey, base: new Set(fm.sel), on: false, id: e.pointerId, cx: e.clientX, cy: e.clientY, el: null, raf: 0 };
  });
  list.addEventListener('pointermove', function (e) {
    if (!band || e.pointerId !== band.id) return;
    if (!band.on) { if (Math.hypot(e.clientX - band.cx, e.clientY - band.cy) < 5) return; band.on = true; list.setPointerCapture(e.pointerId); band.el = document.createElement('div'); band.el.className = 'fm-band'; list.appendChild(band.el); }
    band.cx = e.clientX; band.cy = e.clientY;
    if (!band.raf) band.raf = requestAnimationFrame(function () {
      band.raf = 0; if (!band || !band.on) return;
      var r = list.getBoundingClientRect();
      if (band.cy > r.bottom - 26) list.scrollTop += 16; else if (band.cy < r.top + 26) list.scrollTop -= 16;
      var x = band.cx - r.left + list.scrollLeft, y = band.cy - r.top + list.scrollTop;
      var l = Math.min(band.x, x), t = Math.min(band.y, y), w = Math.abs(band.x - x), h = Math.abs(band.y - y);
      band.el.style.cssText = 'left:' + l + 'px;top:' + t + 'px;width:' + w + 'px;height:' + h + 'px';
      var next = band.add ? new Set(band.base) : new Set();
      list.querySelectorAll('.fm-row').forEach(function (row) {
        if (row.offsetLeft < l + w && row.offsetLeft + row.offsetWidth > l && row.offsetTop < t + h && row.offsetTop + row.offsetHeight > t) { var it = fm.shown[+row.dataset.i]; if (it) next.add(it.path); }
      });
      fm.sel = next; fm.anchor = fm.cursor = ''; fmPaintSel();
    });
  });
  function bandEnd() {
    if (!band) return;
    if (band.raf) cancelAnimationFrame(band.raf);
    if (band.on) { if (band.el) band.el.remove(); } else if (!band.add) fmSelectOnly('');           // a plain click on the empty space
    band = null;
  }
  list.addEventListener('pointerup', bandEnd); list.addEventListener('pointercancel', bandEnd);

  // drag and drop: move (or Ctrl = copy) onto a folder, a place in the sidebar or a crumb in the path bar
  screen.addEventListener('dragstart', function (e) {
    var row = e.target.closest && e.target.closest('.fm-row'); if (!row) return;
    var it = fm.shown[+row.dataset.i]; if (!it) { e.preventDefault(); return; }
    if (!fm.sel.has(it.path)) fmSelectOnly(it.path);
    fm.drag = Array.from(fm.sel);
    e.dataTransfer.setData('application/x-reclmr-fm', '1'); e.dataTransfer.effectAllowed = 'copyMove';
    if (fm.drag.length > 1) { var g = document.createElement('div'); g.className = 'fm-ghost'; g.textContent = fm.drag.length + ' items'; document.body.appendChild(g); e.dataTransfer.setDragImage(g, 12, 12); setTimeout(function () { g.remove(); }, 0); }
    screen.classList.add('dragging');
  });
  screen.addEventListener('dragover', function (e) {
    if (!fm.drag) return;
    var t = fmDragDir(e); fmDragClear();
    if (!t) { e.dataTransfer.dropEffect = 'none'; return; }
    e.preventDefault(); e.dataTransfer.dropEffect = e.ctrlKey || e.altKey ? 'copy' : 'move'; t.el.classList.add('drop');
  });
  screen.addEventListener('drop', function (e) {
    if (!fm.drag) return;
    var t = fmDragDir(e), paths = fm.drag; fmDragClear();
    if (!t) return;
    e.preventDefault(); fm.drag = null; screen.classList.remove('dragging');
    fmPaste(paths, t.dir, !(e.ctrlKey || e.altKey));
  });
  screen.addEventListener('dragend', function () { fm.drag = null; fmDragClear(); screen.classList.remove('dragging'); });

  // toolbar pieces
  fmEl('fm-cmd').addEventListener('click', function (e) { var b = e.target.closest('[data-act]'); if (b) fmAct(b.dataset.act); });
  fmEl('fm-info').addEventListener('click', function (e) { var b = e.target.closest('[data-act]'); if (b) fmAct(b.dataset.act); });
  fmEl('lego-breadcrumbs').addEventListener('click', function (e) { var b = e.target.closest('[data-path]'); if (b) loadLego(b.dataset.path); else if (e.target.tagName !== 'INPUT') fmPathEdit(); });
  fmEl('fm-side').addEventListener('contextmenu', function (e) { var b = e.target.closest('.fm-place[data-path]'); if (b) fmPlaceMenu(e, b); });
  fmEl('fm-side').addEventListener('click', function (e) {
    var b = e.target.closest('.fm-place'); if (!b) return;
    if (b.dataset.go === 'gallery') glOpen(); else loadLego(b.dataset.path);
  });
  document.addEventListener('click', function (e) { if (!e.target.closest('#fm-menu') && !e.target.closest('#fm-sort,#fm-more,[data-act="more"]')) fmCloseMenu(); });
  window.addEventListener('blur', fmCloseMenu);
  list.addEventListener('scroll', fmCloseMenu, { passive: true });

  document.addEventListener('keydown', function (e) {
    if (!navActive('lego-screen') || glb.open || document.getElementById('ui-confirm')) return;
    var tag = e.target && e.target.tagName, k = e.key;
    if (tag === 'INPUT' || tag === 'TEXTAREA') {
      if (e.target.id !== 'fm-q') return;
      if (k === 'Escape') { e.target.value = ''; fmSearch(''); e.target.blur(); } else if (k === 'ArrowDown') { e.preventDefault(); e.target.blur(); if (fm.shown.length) fmSelectOnly(fm.shown[0].path, true); }
      return;
    }
    var menu = fmEl('fm-menu');
    if (!menu.hidden) {                                                   // keyboard inside an open menu
      var bs = Array.from(menu.querySelectorAll('button')), at = bs.indexOf(document.activeElement);
      if (k === 'Escape') { e.preventDefault(); fmCloseMenu(); list.focus(); }
      else if (k === 'ArrowDown' || k === 'ArrowUp') { e.preventDefault(); bs[(at + (k === 'ArrowDown' ? 1 : -1) + bs.length) % bs.length].focus(); }
      return;
    }
    var mod = e.ctrlKey || e.metaKey;
    if (e.altKey) {
      if (k === 'ArrowLeft') fmBack(); else if (k === 'ArrowRight') history.forward(); else if (k === 'ArrowUp') fmUp(); else if (k === 'Enter') fmAct('info'); else return;
      e.preventDefault(); return;
    }
    if (mod) {
      var low = k.toLowerCase();
      if (low === 'a') fmSelectAll();
      else if (low === 'c' && fm.sel.size) fmClip(false);
      else if (low === 'x' && fm.sel.size) fmClip(true);
      else if (low === 'v' && fm.clip) fmPasteClip(legoCurrentPath);
      else if (low === 'l') fmPathEdit();
      else if (low === 'f') fmEl('fm-q').focus();
      else return;
      e.preventDefault(); return;
    }
    var n = fm.shown.length, cur = fmIdx(fm.cursor), step = 0, grid = fm.view === 'grid';
    if (k === '/') { e.preventDefault(); fmEl('fm-q').focus(); return; }
    if (k === 'Escape') {
      if (fm.sel.size) { e.preventDefault(); fm.pick = false; fmSelectOnly(''); } else if (fm.clip) { e.preventDefault(); fmClipClear(); } else if (fmEl('lego-screen').classList.contains('side-open')) fmDrawer(false);
      return;
    }
    if (k === 'ArrowDown') step = grid ? fmCols() : 1; else if (k === 'ArrowUp') step = grid ? -fmCols() : -1;
    else if (k === 'ArrowRight' && grid) step = 1; else if (k === 'ArrowLeft' && grid) step = -1;
    else if (k === 'PageDown' || k === 'PageUp') { var rh = (list.querySelector('.fm-row') || { offsetHeight: 40 }).offsetHeight || 40; step = Math.max(1, Math.floor(list.clientHeight / rh) - 1) * (grid ? fmCols() : 1) * (k === 'PageUp' ? -1 : 1); }
    else if (k === 'Home' || k === 'End') {
      if (n) { var to = fm.shown[k === 'Home' ? 0 : n - 1].path; e.preventDefault(); if (e.shiftKey) fmSelectRange(to); else fmSelectOnly(to, true); }
      return;
    }
    if (step) {
      e.preventDefault(); if (!n) return;
      var j = cur < 0 ? (step > 0 ? 0 : n - 1) : Math.max(0, Math.min(n - 1, cur + step)), to2 = fm.shown[j].path;
      if (e.shiftKey) fmSelectRange(to2); else fmSelectOnly(to2, true);
      return;
    }
    var it = cur >= 0 ? fm.shown[cur] : null, many = fmSelItems();
    if (k === 'Enter' && it) { e.preventDefault(); fmOpenItem(it); }
    else if (k === 'F2' && many.length === 1) { e.preventDefault(); fmRename(many[0]); }
    else if (k === 'Backspace') { e.preventDefault(); fmUp(); }
    else if (k === 'Delete' && fm.sel.size) { e.preventDefault(); if (fmInTrash()) fmDeleteSelected(); else fmTrashSelected(); }
    else if (k === ' ' && !fm.ta && it && fmIsMedia(it)) { e.preventDefault(); fmOpenItem(it); }
    else if (k.length === 1 && n) {                                         // type a few letters to jump to a name
      e.preventDefault(); clearTimeout(fm.taT); fm.ta += k.toLowerCase(); fm.taT = setTimeout(function () { fm.ta = ''; }, 900);
      var from = Math.max(0, cur), hit = -1;
      for (var q = 0; q < n && hit < 0; q++) { var cand = (from + q + (fm.ta.length === 1 && cur >= 0 ? 1 : 0)) % n; if (fm.shown[cand].name.toLowerCase().indexOf(fm.ta) === 0) hit = cand; }
      if (hit >= 0) fmSelectOnly(fm.shown[hit].path, true);
    }
  });
}
function fmCols() {
  var rows = fmEl('lego-list').querySelectorAll('.fm-row'); if (rows.length < 2) return 1;
  var t = rows[0].offsetTop, c = 1; while (c < rows.length && rows[c].offsetTop === t) c++; return c;
}
if (fmEl('lego-list')) fmInit();

// the viewer routes (view/<path>) arrive here when a page is reloaded on a photo
async function fmRouteView(p) {
  var folder = p.indexOf('/') >= 0 ? p.slice(0, p.lastIndexOf('/')) : '';
  if (!navActive('lego-screen') || legoCurrentPath !== folder || fm.loading) { showScreen('lego-screen'); await loadLego(folder, { silent: true }); }
  var it = fm.shown.find(function (x) { return x.path === p; });
  if (!it) return;
  var media = fm.shown.filter(fmIsMedia).map(fmAsMedia), idx = media.findIndex(function (m) { return m.p === p; });
  glbOpen({
    items: media, index: Math.max(0, idx), hash: function (m) { return 'view/' + m.p; }, parent: function () { return 'lego/' + folder; },
    onDelete: function (done) { fmForget(new Set(done.map(function (x) { return x.p; }))); },
  }, true);
}

// ─── Swipe Mode (Tinder for files) ──────────────────────────
let swipeFolderPath = '';
let swipeCurrentPath = '';
let swipeTotal = 0;
let swipeStartX = 0;
let swipeStartY = 0;
let swipeCurrentX = 0;
let swipeCurrentY = 0;
let swipeDragging = false;
let swipeNextImg = new Image();  // preloaded next image

async function openSwipeMode(path) {
  swipeFolderPath = path || '';
  document.getElementById('lego-swipe').style.display = 'flex';
  document.getElementById('lego-swipe-empty').style.display = 'none';
  document.getElementById('lego-swipe-card').style.display = '';
  await loadNextSwipeImage();
}

function closeSwipeMode() {
  document.getElementById('lego-swipe').style.display = 'none';
  loadLego(legoCurrentPath);
}

async function loadNextSwipeImage() {
  const card = document.getElementById('lego-swipe-card');
  const info = document.getElementById('lego-swipe-info');
  card.className = 'lego-swipe-card';
  card.style.transform = '';
  card.style.opacity = '';

  // Use preloaded image if available (instant swap)
  if (swipeNextImg._path) {
    swipeCurrentPath = swipeNextImg._path;
    swipeTotal = swipeNextImg._total;
    document.getElementById('lego-swipe-img').src = swipeNextImg.src;
    document.getElementById('lego-swipe-card-name').textContent = swipeNextImg._name;
    info.textContent = `${swipeTotal} images left`;
    card.style.display = '';
    document.getElementById('lego-swipe-empty').style.display = 'none';
    const nextPath = swipeNextImg._path;
    swipeNextImg = new Image();  // clear it
    // Start preloading the next one in background
    preloadNextSwipeImage();
    return;
  }

  // No preload ready - fetch one
  info.textContent = 'Loading...';
  try {
    const r = await fetch(`/api/lego/random_image?path=${encodeURIComponent(swipeFolderPath)}`);
    const d = await r.json();
    if (d.error) {
      card.style.display = 'none';
      document.getElementById('lego-swipe-empty').style.display = '';
      info.textContent = 'No images';
      return;
    }
    swipeCurrentPath = d.path;
    swipeTotal = d.total_images;
    document.getElementById('lego-swipe-img').src = `/api/gallery/thumb?path=${encodeURIComponent(d.path)}&s=md`;
    document.getElementById('lego-swipe-card-name').textContent = d.name;
    info.textContent = `${swipeTotal} images left`;
    card.style.display = '';
    document.getElementById('lego-swipe-empty').style.display = 'none';
    preloadNextSwipeImage();
  } catch (e) {
    info.textContent = 'Error: ' + e.message;
  }
}

async function preloadNextSwipeImage() {
  try {
    const r = await fetch(`/api/lego/random_image?path=${encodeURIComponent(swipeFolderPath)}`);
    const d = await r.json();
    if (!d.error) {
      swipeNextImg = new Image();
      swipeNextImg.src = `/api/gallery/thumb?path=${encodeURIComponent(d.path)}&s=md`;
      swipeNextImg._path = d.path;
      swipeNextImg._name = d.name;
      swipeNextImg._total = d.total_images;
    }
  } catch (e) {}
}

async function swipeLeft() {
  if (!swipeCurrentPath) return;
  const card = document.getElementById('lego-swipe-card');
  card.className = 'lego-swipe-card fly-left';
  try {
    await fetch('/api/lego/trash', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: swipeCurrentPath }),
    });
  } catch (e) {}
  swipeCurrentPath = '';
  setTimeout(loadNextSwipeImage, 350);
}

async function swipeRight() {
  if (!swipeCurrentPath) return;
  const card = document.getElementById('lego-swipe-card');
  card.className = 'lego-swipe-card fly-right';
  try {
    await fetch('/api/lego/poof', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: swipeCurrentPath }),
    });
  } catch (e) {}
  swipeCurrentPath = '';
  setTimeout(loadNextSwipeImage, 350);
}

async function swipeShred() {
  if (!swipeCurrentPath) return;
  if (!confirm('Permanently shred this file?\nThis cannot be undone.')) return;
  const card = document.getElementById('lego-swipe-card');
  card.className = 'lego-swipe-card fly-up';
  try {
    await fetch('/api/lego/shred', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: swipeCurrentPath }),
    });
  } catch (e) {}
  swipeCurrentPath = '';
  setTimeout(loadNextSwipeImage, 350);
}

// Touch/drag gestures on the card
(function() {
  const card = document.getElementById('lego-swipe-card');
  if (!card) return;

  function onStart(e) {
    if (!swipeCurrentPath) return;
    swipeDragging = true;
    card.classList.add('swiping');
    const t = e.touches ? e.touches[0] : e;
    swipeStartX = t.clientX;
    swipeStartY = t.clientY;
    swipeCurrentX = 0;
    swipeCurrentY = 0;
  }

  function onMove(e) {
    if (!swipeDragging) return;
    e.preventDefault();
    const t = e.touches ? e.touches[0] : e;
    swipeCurrentX = t.clientX - swipeStartX;
    swipeCurrentY = t.clientY - swipeStartY;
    const rot = swipeCurrentX / 20;
    card.style.transform = `translate(${swipeCurrentX}px, ${swipeCurrentY * 0.3}px) rotate(${rot}deg)`;

    // Show hints based on drag direction
    const hintLeft = document.getElementById('lego-swipe-hint-left');
    const hintRight = document.getElementById('lego-swipe-hint-right');
    if (swipeCurrentX < -40) {
      hintLeft.style.opacity = Math.min(1, Math.abs(swipeCurrentX) / 100);
    } else {
      hintLeft.style.opacity = 0;
    }
    if (swipeCurrentX > 40) {
      hintRight.style.opacity = Math.min(1, swipeCurrentX / 100);
    } else {
      hintRight.style.opacity = 0;
    }
  }

  function onEnd(e) {
    if (!swipeDragging) return;
    swipeDragging = false;
    card.classList.remove('swiping');
    document.getElementById('lego-swipe-hint-left').style.opacity = 0;
    document.getElementById('lego-swipe-hint-right').style.opacity = 0;

    if (swipeCurrentX < -120) {
      swipeLeft();
    } else if (swipeCurrentX > 120) {
      swipeRight();
    } else {
      // Snap back
      card.style.transform = '';
    }
  }

  card.addEventListener('touchstart', onStart, { passive: true });
  card.addEventListener('touchmove', onMove, { passive: false });
  card.addEventListener('touchend', onEnd);
  card.addEventListener('mousedown', onStart);
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onEnd);
})();

// ─── Terminal ───────────────────────────────────────────
