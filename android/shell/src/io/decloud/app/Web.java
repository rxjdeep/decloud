package io.decloud.app;

import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Context;
import android.content.ContextWrapper;
import android.content.Intent;
import android.content.MutableContextWrapper;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.webkit.*;
import android.widget.Toast;

import java.util.Locale;

/**
 * The one WebView, kept for the life of the process rather than the life of the screen. Swiping the app away destroys its window but
 * not this, so music carries on (MediaService keeps the process alive while something plays), and opening the app again reattaches it.
 */
final class Web {
    static final Handler H = new Handler(Looper.getMainLooper());
    static WebView view;
    static MutableContextWrapper ctx;
    static MainActivity act;
    static volatile boolean playing, visible;
    static int insetTop, insetBottom;                  // CSS pixels, handed to the page
    static ValueCallback<Uri[]> chooser;
    static PermissionRequest pendingPerm;
    static View custom;
    static WebChromeClient.CustomViewCallback customCb;

    static final int REQ_FILE = 71, REQ_MIC = 72;

    static String host() { return Uri.parse(Config.BASE_URL).getHost(); }

    static WebView get(Context app) {
        if (view != null) return view;
        ctx = new MutableContextWrapper(app);
        view = new WebView(ctx);
        WebSettings s = view.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);      // the player starts the next song on its own
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setUserAgentString(s.getUserAgentString() + " DeCloudApp/1");
        view.setBackgroundColor(0xFF0A0A0F);
        view.setOverScrollMode(View.OVER_SCROLL_NEVER);
        CookieManager.getInstance().setAcceptCookie(true);
        view.addJavascriptInterface(new Bridge(app), "DeCloudApp");
        view.setWebViewClient(new Client());
        view.setWebChromeClient(new Chrome());
        view.setDownloadListener(new DownloadListener() {
            @Override public void onDownloadStart(String url, String ua, String cd, String mime, long len) { download(url, ua, cd, mime); }
        });
        Log.add(app, "WebView created, loading " + Config.BASE_URL);
        view.loadUrl(Config.BASE_URL);
        return view;
    }

    private static final Runnable SLEEP = new Runnable() { @Override public void run() { maybeSleep(); } };

    /**
     * The page reports "not playing" for a moment at the end of every song, just before it starts the loop or the next one.
     * Putting the page to sleep right then freezes it before it can do that, so the music stops until the app is opened again.
     * Wait a few seconds instead, and cancel if playing comes back.
     */
    static void sleepSoon(boolean playingNow) {
        H.removeCallbacks(SLEEP);
        if (!playingNow) H.postDelayed(SLEEP, 6000);
    }

    /** The WebView held the process alive for nothing: when nothing plays and the app is not on screen, let it rest. */
    static void maybeSleep() {
        if (view == null || visible || playing) return;
        view.onPause();
        view.pauseTimers();
    }

    static void wake() {
        if (view == null) return;
        view.onResume();
        view.resumeTimers();
    }

    static void js(String code) { if (view != null) view.evaluateJavascript(code, null); }

    static void go(String hash) {
        if (hash == null || !hash.matches("[A-Za-z0-9/_.~-]{0,200}")) return;
        js("location.hash = '" + hash + "'");
    }

    /** A button on the notification or lock screen: play, pause, stop, previoustrack, nexttrack, seekto. */
    static void mediaAction(final String action, final double value) {
        H.post(new Runnable() { @Override public void run() {
            wake();
            js("window.decloudMediaAction && decloudMediaAction('" + action + "'," + value + ")");
        } });
    }

    static void setInsets(int top, int bottom) {
        insetTop = top; insetBottom = bottom;
        pushInsets();
    }

    static void pushInsets() {
        js("(function(){var e=document.documentElement;if(!e)return;var r=e.style;r.setProperty('--safe-top','" + insetTop + "px');r.setProperty('--safe-bottom','" + insetBottom + "px');})()");
    }

    private static void download(String url, String ua, String cd, String mime) {
        Context c = ctx;
        try {
            String name = URLUtil.guessFileName(url, cd, mime);
            DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
            String cookie = CookieManager.getInstance().getCookie(url);
            if (cookie != null) r.addRequestHeader("Cookie", cookie);
            r.addRequestHeader("User-Agent", ua);
            r.setMimeType(mime);
            r.setTitle(name);
            r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
            ((DownloadManager) c.getSystemService(Context.DOWNLOAD_SERVICE)).enqueue(r);
            Toast.makeText(c, "Downloading " + name, Toast.LENGTH_SHORT).show();
            Log.add(c, "download started: " + name);
        } catch (Exception e) {
            Log.add(c, "download failed: " + e.getMessage());
            Toast.makeText(c, "Download failed: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private static boolean ours(Uri u) { return "https".equals(u.getScheme()) && host().equalsIgnoreCase(u.getHost()); }

    static final class Client extends WebViewClient {
        @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
            Uri u = r.getUrl();
            if (ours(u)) return false;
            try { ctx.startActivity(new Intent(Intent.ACTION_VIEW, u).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); } catch (Exception ignored) { }
            return true;                                       // anything that is not your server opens in the browser, never inside the app
        }

        @Override public void onPageFinished(WebView v, String url) {
            pushInsets();
            CookieManager.getInstance().flush();               // keeps you signed in across restarts
            v.evaluateJavascript("(function(){var d=document.createElement('div');d.style.cssText='position:fixed;padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)';document.body.appendChild(d);var c=getComputedStyle(d);var r=c.paddingTop+'/'+c.paddingBottom;d.remove();return r})()",
                new ValueCallback<String>() { @Override public void onReceiveValue(String val) { Log.add(ctx, "page loaded; insets pushed " + insetTop + "/" + insetBottom + " px, env() says " + val); } });
        }

        @Override public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
            if (!r.isForMainFrame()) return;
            Log.add(ctx, "page failed: " + e.getDescription() + " (" + r.getUrl() + ")");
            String html = "<body style='background:#0a0a0f;color:#e6e6ea;font-family:sans-serif;text-align:center;padding:30vh 24px 0'>"
                + "<h2>Can't reach Reclamr</h2><p style='opacity:.7'>" + android.text.TextUtils.htmlEncode(String.valueOf(e.getDescription())) + "</p>"
                + "<p><a href='" + Config.BASE_URL + "' style='color:#8ab4ff;font-size:18px'>Try again</a></p></body>";
            v.loadDataWithBaseURL(Config.BASE_URL, html, "text/html", "utf-8", null);
        }

        @Override public boolean onRenderProcessGone(WebView v, RenderProcessGoneDetail d) {
            Log.add(ctx, "the web renderer died" + (d.didCrash() ? " (crash)" : " (killed for memory)") + ": rebuilding the view");
            if (act != null) act.detachWeb();
            view = null;
            if (act != null) act.attachWeb();
            return true;
        }
    }

    static final class Chrome extends WebChromeClient {
        @Override public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams p) {
            if (act == null) { cb.onReceiveValue(null); return true; }
            if (chooser != null) chooser.onReceiveValue(null);
            chooser = cb;
            try { act.startActivityForResult(p.createIntent(), REQ_FILE); } catch (Exception e) { chooser = null; cb.onReceiveValue(null); }
            return true;
        }

        @Override public void onPermissionRequest(PermissionRequest r) {
            // only the microphone (Voice), only for your own server
            boolean mic = false;
            for (String res : r.getResources()) mic |= PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(res);
            if (!mic || r.getResources().length != 1 || !ours(r.getOrigin()) || act == null) { r.deny(); return; }
            if (act.checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED) { r.grant(r.getResources()); return; }
            pendingPerm = r;
            act.requestPermissions(new String[]{android.Manifest.permission.RECORD_AUDIO}, REQ_MIC);
        }

        @Override public void onShowCustomView(View v, CustomViewCallback cb) {          // a video taken fullscreen
            if (act == null) { cb.onCustomViewHidden(); return; }
            custom = v; customCb = cb;
            act.showCustom(v);
        }

        @Override public void onHideCustomView() {
            if (act != null && custom != null) act.hideCustom(custom);
            if (customCb != null) customCb.onCustomViewHidden();
            custom = null; customCb = null;
        }

        @Override public boolean onConsoleMessage(ConsoleMessage m) {                    // page errors end up in the app's log, which is how problems get found
            if (m.messageLevel() == ConsoleMessage.MessageLevel.ERROR)
                Log.add(ctx, "page error: " + m.message() + " (" + String.valueOf(m.sourceId()).replaceAll("^.*/", "") + ":" + m.lineNumber() + ")");
            return true;
        }

        @Override public boolean onJsAlert(WebView v, String url, String msg, final JsResult r) {
            if (act == null) { r.cancel(); return true; }
            new AlertDialog.Builder(act).setMessage(msg).setPositiveButton("OK", new android.content.DialogInterface.OnClickListener() {
                @Override public void onClick(android.content.DialogInterface d, int w) { r.confirm(); } }).setOnCancelListener(new android.content.DialogInterface.OnCancelListener() {
                @Override public void onCancel(android.content.DialogInterface d) { r.cancel(); } }).show();
            return true;
        }

        @Override public boolean onJsConfirm(WebView v, String url, String msg, final JsResult r) {
            if (act == null) { r.cancel(); return true; }
            new AlertDialog.Builder(act).setMessage(msg)
                .setPositiveButton("OK", new android.content.DialogInterface.OnClickListener() { @Override public void onClick(android.content.DialogInterface d, int w) { r.confirm(); } })
                .setNegativeButton("Cancel", new android.content.DialogInterface.OnClickListener() { @Override public void onClick(android.content.DialogInterface d, int w) { r.cancel(); } })
                .setOnCancelListener(new android.content.DialogInterface.OnCancelListener() { @Override public void onCancel(android.content.DialogInterface d) { r.cancel(); } }).show();
            return true;
        }
    }
}
