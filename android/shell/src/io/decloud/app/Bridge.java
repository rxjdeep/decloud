package io.decloud.app;

import android.content.Context;
import android.content.Intent;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.webkit.JavascriptInterface;
import org.json.JSONArray;

/** What the page can ask of the app. Deliberately tiny: it can tell the app what is playing and open two screens, nothing else. */
final class Bridge {
    private final Context app;
    Bridge(Context app) { this.app = app; }

    @JavascriptInterface public int version() { return 1; }

    @JavascriptInterface public void nowPlaying(String title, String artist, String album, String art, boolean playing, double pos, double dur) {
        Web.playing = playing && title != null && !title.isEmpty();
        MediaService.update(app, clip(title), clip(artist), clip(album), clip(art), playing, (long) (pos * 1000), (long) (dur * 1000));
        Web.sleepSoon(Web.playing);
    }

    /**
     * navigator.vibrate for the page: a JSON array of milliseconds, vibrate / pause / vibrate ..., exactly as the web API takes it.
     * A new call replaces the one in progress and [] or 0 stops it, like in Chrome. Only while the app is on screen.
     */
    /** The screen stays on while the full-screen player is open and music plays. Needs no permission: it is a window flag. */
    @JavascriptInterface public void keepScreenOn(final boolean on) {
        Web.H.post(new Runnable() { @Override public void run() {
            if (Web.act == null) return;
            if (on) Web.act.getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            else Web.act.getWindow().clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        } });
    }

    @JavascriptInterface public void vibrate(String pattern) {
        try {
            Vibrator v = app.getSystemService(Vibrator.class);
            if (v == null || !v.hasVibrator()) return;
            JSONArray a = new JSONArray(pattern);
            int n = Math.min(a.length(), 40);
            long[] t = new long[n + 1];                    // Android's waveforms start with a pause; the web API's pattern starts with a vibration
            long total = 0;
            for (int i = 0; i < n; i++) { t[i + 1] = Math.max(0, Math.min(a.getLong(i), 5000)); total += t[i + 1]; }
            if (total == 0) { v.cancel(); return; }
            if (!Web.visible) return;
            if (n == 1) v.vibrate(VibrationEffect.createOneShot(t[1], VibrationEffect.DEFAULT_AMPLITUDE));
            else v.vibrate(VibrationEffect.createWaveform(t, -1));
        } catch (Exception ignored) { }
    }

    /** The app's own log: page errors, bar sizes, music-service messages. */
    @JavascriptInterface public void openLog() {
        Web.H.post(new Runnable() { @Override public void run() {
            app.startActivity(new Intent(app, LogActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        } });
    }

    /** The backup is a separate app (io.decloud.pusher); this only opens it if it is installed. */
    /** Shuffle on/off and repeat ("off", "all", "one", or "none" when the buttons do not apply, as for an audiobook). */
    @JavascriptInterface public void modes(boolean shuffle, String repeat) {
        MediaService.setModes(shuffle, repeat);
    }

    @JavascriptInterface public void openBackup() {
        Web.H.post(new Runnable() { @Override public void run() {
            try { app.startActivity(new Intent().setClassName("io.decloud.pusher", "io.decloud.pusher.MainActivity").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); }
            catch (Exception e) { android.widget.Toast.makeText(app, "The reclmr Backup app is not installed", android.widget.Toast.LENGTH_LONG).show(); }
        } });
    }

    private static String clip(String s) { return s == null ? "" : s.length() > 300 ? s.substring(0, 300) : s; }
}
