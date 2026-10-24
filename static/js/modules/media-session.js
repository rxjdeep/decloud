// ===== Module: media-session =====
// Media Session API integration - feeds iOS lock screen / Dynamic Island / Android notification
// with title, artist, artwork, progress bar, and play/pause/seek controls

var mediaSessionActive = false;
var appMeta = null, appPlaying = false;       // what the DeCloud Android app is told, whether or not the browser has a Media Session

// Which player do the OS media controls (KDE systray, lock screen, headset keys) belong to?
// The one that is playing; if none is, the one that last published its metadata.
var mediaSessionLast = 'music';

function mediaTarget() {
  var bookOn = typeof player !== 'undefined' && player && player.src && !player.paused;
  var musicOn = typeof musicAudio !== 'undefined' && musicAudio && musicAudio.src && !musicAudio.paused;
  if (typeof mz !== 'undefined' && mz.remote) return 'music';          // mirroring another device: still control the music
  if (bookOn && !musicOn) return 'book';
  if (musicOn && !bookOn) return 'music';
  return mediaSessionLast;
}

var mediaActions = {};

function initMediaSession() {
  // Android's WebView may not have navigator.mediaSession; the handlers are still kept so the DeCloud app's own notification can press them.
  var ms = 'mediaSession' in navigator ? navigator.mediaSession : null;
  function on(action, fn) { mediaActions[action] = fn; if (ms) { try { ms.setActionHandler(action, fn); } catch (e) { /* action not supported by this browser */ } } }

  on('play', function() {
    if (mediaTarget() === 'book') { if (typeof player !== 'undefined' && player && player.paused && typeof togglePlay === 'function') togglePlay(); }
    else if (typeof mzMediaPlay === 'function') mzMediaPlay();
  });

  on('pause', function() {
    if (mediaTarget() === 'book') { if (typeof player !== 'undefined' && player && !player.paused && typeof togglePlay === 'function') togglePlay(); }
    else if (typeof mzMediaPause === 'function') mzMediaPause();
  });

  on('stop', function() {
    if (mediaTarget() === 'book') { if (typeof player !== 'undefined' && player && !player.paused && typeof togglePlay === 'function') togglePlay(); }
    else if (typeof mzStop === 'function') mzStop();
  });

  on('seekbackward', function(details) {
    var skip = (details && details.seekOffset) || 15;
    if (mediaTarget() === 'music' && typeof mzMediaSeekBy === 'function') { mzMediaSeekBy(-skip); return; }
    var audio = getActiveAudio();
    if (audio) audio.currentTime = Math.max(0, audio.currentTime - skip);
  });

  on('seekforward', function(details) {
    var skip = (details && details.seekOffset) || 15;
    if (mediaTarget() === 'music' && typeof mzMediaSeekBy === 'function') { mzMediaSeekBy(skip); return; }
    var audio = getActiveAudio();
    if (audio) audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + skip);
  });

  on('seekto', function(details) {
    if (!details || details.seekTime == null) return;
    if (mediaTarget() === 'music' && typeof mzMediaSeekTo === 'function') { mzMediaSeekTo(details.seekTime); return; }
    var audio = getActiveAudio();
    if (audio) audio.currentTime = details.seekTime;
  });

  on('previoustrack', function() {
    if (mediaTarget() === 'book') { if (typeof prevChapter === 'function') prevChapter(); }
    else if (typeof prevSong === 'function') prevSong();
  });

  on('nexttrack', function() {
    if (mediaTarget() === 'book') { if (typeof nextChapter === 'function') nextChapter(); }
    else if (typeof nextSong === 'function') nextSong();
  });

  mediaSessionActive = !!ms;
}

function getActiveAudio() {
  var book = typeof player !== 'undefined' ? player : null;
  var music = typeof musicAudio !== 'undefined' ? musicAudio : null;
  // Prefer whichever is currently playing
  if (book && !book.paused && book.src) return book;
  if (music && !music.paused && music.src) return music;
  // Otherwise the one that last published its metadata
  var order = mediaSessionLast === 'book' ? [book, music] : [music, book];
  for (var i = 0; i < order.length; i++) if (order[i] && order[i].src) return order[i];
  return null;
}

// Called by audiobook player when a chapter starts playing
function updateMediaSessionBook(bookTitle, chapterTitle, coverUrl) {
  mediaSessionLast = 'book';
  appMeta = { title: chapterTitle || 'Unknown Chapter', artist: bookTitle || 'Audiobook', album: 'DeCloud Audiobooks', art: coverUrl || '' };
  if (!mediaSessionActive) { appMediaPush(true); return; }

  var artwork = [];
  if (coverUrl) {
    artwork = [
      { src: coverUrl, sizes: '256x256', type: 'image/png' },
      { src: coverUrl, sizes: '512x512', type: 'image/png' }
    ];
  }

  navigator.mediaSession.metadata = new MediaMetadata({
    title: chapterTitle || 'Unknown Chapter',
    artist: bookTitle || 'Audiobook',
    album: 'DeCloud Audiobooks',
    artwork: artwork
  });

  updateMediaSessionPosition();
  appMediaPush(true);
}

// Called by music player when a song starts playing
function updateMediaSessionMusic(songName, artistName, coverUrl) {
  mediaSessionLast = 'music';
  appMeta = { title: songName || 'Unknown Track', artist: artistName || 'DeCloud Music', album: 'DeCloud Music', art: coverUrl || '' };
  if (!mediaSessionActive) { appMediaPush(true); return; }

  var artwork = [];
  if (coverUrl) {
    artwork = [
      { src: coverUrl, sizes: '256x256', type: 'image/png' },
      { src: coverUrl, sizes: '512x512', type: 'image/png' }
    ];
  }

  navigator.mediaSession.metadata = new MediaMetadata({
    title: songName || 'Unknown Track',
    artist: artistName || 'DeCloud Music',
    album: 'DeCloud Music',
    artwork: artwork
  });

  updateMediaSessionPosition();
  appMediaPush(true);
}

// Update the position state (progress bar on lock screen)
function updateMediaSessionPosition() {
  if (!mediaSessionActive) { appMediaPush(false); return; }
  if (!('setPositionState' in navigator.mediaSession)) return;

  var audio = getActiveAudio();
  if (!audio || !audio.duration || isNaN(audio.duration)) return;

  try {
    navigator.mediaSession.setPositionState({
      duration: audio.duration,
      playbackRate: audio.playbackRate || 1,
      position: Math.min(audio.currentTime, audio.duration)
    });
  } catch (e) {
    // Some browsers throw if duration is not finite
  }
  appMediaPush(false);
}

// Set the playback state
function setMediaSessionPlaying(playing) {
  appPlaying = !!playing;
  if (!mediaSessionActive) { appMediaPush(true); return; }
  navigator.mediaSession.playbackState = playing ? 'playing' : 'paused';
  if (playing) updateMediaSessionPosition();
  appMediaPush(true);
}

// ─── Native Android app (window.DeCloudApp is injected by the app's WebView) ──
// The app draws the lock-screen / notification controls itself, so it is told what is playing and can press the same handlers a browser would.
var appMedia = { sig: '', pos: 0, at: 0, dur: 0 };

function appMediaPush(force) {
  var app = window.DeCloudApp;
  if (!app || !app.nowPlaying || !appMeta) return;
  appModesPush();
  var a = getActiveAudio();
  if (!a) return;
  var playing = appPlaying;
  var pos = a.currentTime || 0, dur = isFinite(a.duration) ? a.duration : 0, now = Date.now();
  var sig = appMeta.title + '|' + appMeta.artist + '|' + playing;
  var expected = appMedia.pos + (appMedia.sig.slice(-4) === 'true' ? (now - appMedia.at) / 1000 : 0);
  // nothing the lock screen would not work out for itself; the length counts too, since it is often not known until the file has loaded
  if (!force && sig === appMedia.sig && Math.abs(pos - expected) < 2.5 && Math.abs(dur - appMedia.dur) < 1) return;
  appMedia = { sig: sig, pos: pos, at: now, dur: dur };
  try { app.nowPlaying(appMeta.title, appMeta.artist, appMeta.album, appMeta.art ? new URL(appMeta.art, location.href).href : '', playing, pos, dur); } catch (e) { /* the app went away */ }
}

// shuffle and repeat, for the app's player buttons. Only music has them: for an audiobook the app is told 'none' and shows neither.
var appModes = '';
function appModesPush() {
  var app = window.DeCloudApp;
  if (!app || !app.modes || typeof mz === 'undefined') return;
  var music = mediaTarget() === 'music';
  var sh = music && !!mz.shuffle, rp = music ? (mz.repeat || 'off') : 'none';
  if (sh + '|' + rp === appModes) return;
  appModes = sh + '|' + rp;
  try { app.modes(sh, rp); } catch (e) { /* the app went away */ }
}
mediaActions.shuffle = function() { if (typeof mzToggleShuffle === 'function') mzToggleShuffle(); };
mediaActions.repeat = function() { if (typeof mzToggleRepeat === 'function') mzToggleRepeat(); };

// called by the app's notification and lock-screen buttons: play, pause, stop, previoustrack, nexttrack, seekto (value = seconds), shuffle, repeat
function decloudMediaAction(action, value) {
  var fn = mediaActions[action];
  if (fn) fn(value == null ? {} : { seekTime: value, seekOffset: value });
}

// Boot
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initMediaSession);
} else {
  initMediaSession();
}
