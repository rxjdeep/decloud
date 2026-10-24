package io.decloud.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.drawable.Icon;
import android.media.MediaMetadata;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.net.wifi.WifiManager;
import android.os.IBinder;
import android.os.PowerManager;
import android.webkit.CookieManager;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Exists only while a song or chapter is playing (or just paused): it keeps the process alive so the WebView keeps playing with the screen
 * off, publishes the lock-screen / notification controls, and holds the CPU and Wi-Fi awake for the stream.
 */
public class MediaService extends Service {
    private static final String CHANNEL = "playback", UPDATE = "io.decloud.app.UPDATE";
    private static final int ID = 2;
    static volatile boolean running;
    static volatile boolean shuffle;
    static volatile String repeat = "none";              // off, all, one; "none" = no shuffle/repeat buttons (an audiobook)
    private static MediaService instance;
    private static final long IDLE_MS = 10_000;                // paused this long: the controls go away and the app may rest
    private final Runnable idleStop = new Runnable() { @Override public void run() {
        if (playing) return;
        Log.add(MediaService.this, "paused for a while: removing the controls");
        running = false;
        stopForeground(STOP_FOREGROUND_REMOVE); foreground = false;
        getSystemService(NotificationManager.class).cancel(ID);
        stopSelf();
    } };

    private MediaSession session;
    private PowerManager.WakeLock wake;
    private WifiManager.WifiLock wifi;
    private boolean foreground;
    private String title = "", artist = "", album = "", art = "", artShown = "";
    private boolean playing;
    private long pos, dur;
    private Bitmap cover;

    /** Called from the page (through Bridge). Starts the service when playback starts, updates it while it exists, and ignores a quiet app. */
    static void update(Context c, String title, String artist, String album, String art, boolean playing, long pos, long dur) {
        if (!running && !playing) return;
        Intent i = new Intent(c, MediaService.class).setAction(UPDATE)
            .putExtra("t", title).putExtra("a", artist).putExtra("l", album).putExtra("c", art)
            .putExtra("p", playing).putExtra("pos", pos).putExtra("dur", dur);
        try { c.startForegroundService(i); }
        catch (Exception e) { Log.add(c, "could not start the music service from the background: " + e.getClass().getSimpleName()); }
    }

    /** The page reports the modes whenever they change; the buttons are redrawn at once. */
    static void setModes(boolean sh, String rp) {
        shuffle = sh; repeat = rp == null ? "none" : rp;
        Web.H.post(new Runnable() { @Override public void run() { if (instance != null) instance.refresh(); } });
    }

    private void refresh() {
        if (session == null || title == null || title.isEmpty()) return;
        getSystemService(NotificationManager.class).notify(ID, build());
    }

    @Override public void onCreate() { super.onCreate(); instance = this; }

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public int onStartCommand(Intent i, int flags, int startId) {
        if (i == null || i.getAction() == null) { stopSelf(); return START_NOT_STICKY; }
        String a = i.getAction();
        if (!UPDATE.equals(a)) {                                       // a notification button
            if ("prev".equals(a)) Web.mediaAction("previoustrack", 0);
            else if ("next".equals(a)) Web.mediaAction("nexttrack", 0);
            else if ("toggle".equals(a)) Web.mediaAction(playing ? "pause" : "play", 0);
            else if ("shuffle".equals(a)) Web.mediaAction("shuffle", 0);
            else if ("repeat".equals(a)) Web.mediaAction("repeat", 0);
            return START_NOT_STICKY;
        }
        running = true;
        title = i.getStringExtra("t"); artist = i.getStringExtra("a"); album = i.getStringExtra("l");
        art = i.getStringExtra("c"); playing = i.getBooleanExtra("p", false);
        pos = i.getLongExtra("pos", 0); dur = i.getLongExtra("dur", 0);
        if (title == null || title.isEmpty()) { stopForeground(STOP_FOREGROUND_REMOVE); foreground = false; stopSelf(); return START_NOT_STICKY; }

        if (session == null) session = makeSession();
        Notification n = build();
        if (!foreground) {
            try { startForeground(ID, n, android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK); foreground = true; }
            catch (Exception e) {                                      // Android refuses this from the background; that must never take the app down
                Log.add(this, "could not enter foreground (" + e.getClass().getSimpleName() + "): showing the notification without it");
                getSystemService(NotificationManager.class).notify(ID, n);
            }
        } else getSystemService(NotificationManager.class).notify(ID, n);
        // Pausing does NOT leave the foreground at once. A song ends with a brief "paused" and a looped one restarts a moment later, and
        // re-entering the foreground from the background is forbidden: leaving it there is what crashed the app on every loop.
        // The service lets go only after a real pause.
        Web.H.removeCallbacks(idleStop);
        if (!playing) Web.H.postDelayed(idleStop, IDLE_MS);
        locks(playing);
        if (!art.isEmpty() && !art.equals(artShown)) loadArt(art);
        return START_NOT_STICKY;
    }

    private MediaSession makeSession() {
        MediaSession s = new MediaSession(this, "DeCloud");
        s.setCallback(new MediaSession.Callback() {
            @Override public void onPlay() { Web.mediaAction("play", 0); }
            @Override public void onPause() { Web.mediaAction("pause", 0); }
            @Override public void onStop() { Web.mediaAction("pause", 0); }
            @Override public void onSkipToNext() { Web.mediaAction("nexttrack", 0); }
            @Override public void onSkipToPrevious() { Web.mediaAction("previoustrack", 0); }
            @Override public void onSeekTo(long ms) { Web.mediaAction("seekto", ms / 1000.0); }
            @Override public void onCustomAction(String action, android.os.Bundle extras) { if ("shuffle".equals(action) || "repeat".equals(action)) Web.mediaAction(action, 0); }
        });
        s.setActive(true);
        return s;
    }

    private Notification build() {
        session.setMetadata(new MediaMetadata.Builder()
            .putString(MediaMetadata.METADATA_KEY_TITLE, title).putString(MediaMetadata.METADATA_KEY_ARTIST, artist)
            .putString(MediaMetadata.METADATA_KEY_ALBUM, album).putLong(MediaMetadata.METADATA_KEY_DURATION, dur)
            .putBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART, cover).build());
        PlaybackState.Builder ps = new PlaybackState.Builder()
            .setActions(PlaybackState.ACTION_PLAY | PlaybackState.ACTION_PAUSE | PlaybackState.ACTION_PLAY_PAUSE | PlaybackState.ACTION_SKIP_TO_NEXT
                | PlaybackState.ACTION_SKIP_TO_PREVIOUS | PlaybackState.ACTION_SEEK_TO | PlaybackState.ACTION_STOP)
            .setState(playing ? PlaybackState.STATE_PLAYING : PlaybackState.STATE_PAUSED, pos, playing ? 1f : 0f);      // stamped with the uptime clock by the framework; the calendar clock made every position negative, i.e. zero
        boolean modes = !"none".equals(repeat);
        if (modes) {
            ps.addCustomAction(new PlaybackState.CustomAction.Builder("shuffle", shuffle ? "Shuffle on" : "Shuffle off", icon(shuffle ? "ic_shuffle_on" : "ic_shuffle_off")).build());
            ps.addCustomAction(new PlaybackState.CustomAction.Builder("repeat", "Repeat " + repeat, icon("one".equals(repeat) ? "ic_repeat_one" : "all".equals(repeat) ? "ic_repeat_all" : "ic_repeat_off")).build());
        }
        session.setPlaybackState(ps.build());

        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Playback", NotificationManager.IMPORTANCE_LOW));
        Notification.Builder b = new Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle(title).setContentText(artist)
            .setLargeIcon(cover)
            .setVisibility(Notification.VISIBILITY_PUBLIC)
            .setOngoing(playing)
            .setContentIntent(PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE))
            .setStyle(new Notification.MediaStyle().setMediaSession(session.getSessionToken()).setShowActionsInCompactView(modes ? new int[]{1, 2, 3} : new int[]{0, 1, 2}));
        if (modes) b.addAction(action(icon(shuffle ? "ic_shuffle_on" : "ic_shuffle_off"), shuffle ? "Shuffle on" : "Shuffle off", "shuffle"));
        b.addAction(action(android.R.drawable.ic_media_previous, "Previous", "prev"))
         .addAction(action(playing ? android.R.drawable.ic_media_pause : android.R.drawable.ic_media_play, playing ? "Pause" : "Play", "toggle"))
         .addAction(action(android.R.drawable.ic_media_next, "Next", "next"));
        if (modes) b.addAction(action(icon("one".equals(repeat) ? "ic_repeat_one" : "all".equals(repeat) ? "ic_repeat_all" : "ic_repeat_off"), "off".equals(repeat) ? "Repeat off" : "all".equals(repeat) ? "Repeat all" : "Repeat one", "repeat"));
        return b.build();
    }

    private int icon(String name) { return getResources().getIdentifier(name, "drawable", getPackageName()); }

    private Notification.Action action(int icon, String label, String act) {
        PendingIntent pi = PendingIntent.getService(this, act.hashCode(), new Intent(this, MediaService.class).setAction(act), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Action.Builder(Icon.createWithResource(this, icon), label, pi).build();
    }

    private void locks(boolean on) {
        if (on) {
            if (wake == null) wake = getSystemService(PowerManager.class).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "decloud:play");
            if (!wake.isHeld()) wake.acquire(6 * 3600_000L);                // never forever, even if something goes wrong
            if (wifi == null) wifi = getSystemService(WifiManager.class).createWifiLock(WifiManager.WIFI_MODE_FULL_LOW_LATENCY, "decloud:play");
            if (!wifi.isHeld()) wifi.acquire();
        } else {
            if (wake != null && wake.isHeld()) wake.release();
            if (wifi != null && wifi.isHeld()) wifi.release();
        }
    }

    /** The cover needs your login cookie, so it is fetched here with it, only from your own server. */
    private void loadArt(final String url) {
        artShown = url;
        new Thread(new Runnable() { @Override public void run() {
            try {
                if (!android.net.Uri.parse(url).getHost().equalsIgnoreCase(Web.host())) return;
                HttpURLConnection h = (HttpURLConnection) new URL(url).openConnection();
                String ck = CookieManager.getInstance().getCookie(url);
                if (ck != null) h.setRequestProperty("Cookie", ck);
                h.setConnectTimeout(10_000); h.setReadTimeout(15_000);
                try (InputStream in = h.getInputStream()) {
                    BitmapFactory.Options o = new BitmapFactory.Options(); o.inSampleSize = 2;
                    Bitmap bmp = BitmapFactory.decodeStream(in, null, o);
                    if (bmp == null) return;
                    cover = bmp;
                    Web.H.post(new Runnable() { @Override public void run() {
                        if (session != null && running) getSystemService(NotificationManager.class).notify(ID, build());
                    } });
                }
            } catch (Exception e) { Log.add(MediaService.this, "cover not loaded: " + e.getClass().getSimpleName()); }
        } }, "cover").start();
    }

    /**
     * Swiped away from recents: stop for good. The song is paused first (so the player saves its place), the notification goes,
     * and the whole process is ended a moment later, so nothing of the app is left running in the background.
     */
    @Override public void onTaskRemoved(Intent root) {
        Log.add(this, "swiped away from recents: pausing and shutting down");
        Web.js("(function(){try{var a=document.querySelectorAll('audio');for(var i=0;i<a.length;i++)a[i].pause();}catch(e){}})()");
        running = false; playing = false; Web.playing = false;
        stopForeground(STOP_FOREGROUND_REMOVE); foreground = false;
        getSystemService(NotificationManager.class).cancel(ID);
        Web.H.postDelayed(new Runnable() { @Override public void run() { android.os.Process.killProcess(android.os.Process.myPid()); } }, 700);   // long enough for the pause to be saved
        stopSelf();
    }

    @Override public void onDestroy() {
        Web.H.removeCallbacks(idleStop);
        running = false;
        if (instance == this) instance = null;
        locks(false);
        if (session != null) { session.setActive(false); session.release(); session = null; }
        super.onDestroy();
    }
}
