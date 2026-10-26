package io.reclmr.app;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebView;
import android.widget.FrameLayout;

/** reclmr itself: the shared WebView, drawn edge to edge under transparent system bars. */
public class MainActivity extends Activity {
    private FrameLayout root;
    private WebView web;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        Web.act = this;
        getWindow().setDecorFitsSystemWindows(false);          // the page goes behind the status and navigation bars
        root = new FrameLayout(this);
        root.setBackgroundColor(0xFF0A0A0F);
        root.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() {
            @Override public WindowInsets onApplyWindowInsets(View v, WindowInsets in) {
                float d = getResources().getDisplayMetrics().density;
                android.graphics.Insets bars = in.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                android.graphics.Insets ime = in.getInsets(WindowInsets.Type.ime());
                boolean keyboard = in.isVisible(WindowInsets.Type.ime());
                root.setPadding(0, 0, 0, keyboard ? ime.bottom : 0);      // the keyboard pushes the page up instead of covering it
                Web.setInsets(Math.round(bars.top / d), keyboard ? 0 : Math.round(bars.bottom / d));
                return in;
            }
        });
        setContentView(root);
        // Android's own back swipe would swallow the page's edge-swipe back. Android lets an app claim at most 200dp of height per edge,
        // so a 40dp-wide strip in the middle of the left edge is the page's; the rest of the edge keeps the system gesture.
        root.addOnLayoutChangeListener(new View.OnLayoutChangeListener() {
            @Override public void onLayoutChange(View v, int l, int t, int r, int b, int ol, int ot, int or, int ob) {
                float d = getResources().getDisplayMetrics().density;
                int h = b - t, strip = Math.round(200 * d), top = Math.max(0, (h - strip) / 2);
                v.setSystemGestureExclusionRects(java.util.Collections.singletonList(new android.graphics.Rect(0, top, Math.round(40 * d), top + Math.min(strip, h))));
            }
        });
        WindowInsetsController c = getWindow().getInsetsController();       // only exists once the window has content: asking earlier is what crashed the first build
        if (c != null) c.setSystemBarsAppearance(0, WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);   // white icons
        attachWeb();

        if (android.os.Build.VERSION.SDK_INT >= 33 && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS}, 1);
        route(getIntent());
    }

    void attachWeb() {
        web = Web.get(getApplicationContext());
        Web.ctx.setBaseContext(this);                          // dialogs and pickers need an activity
        if (web.getParent() instanceof ViewGroup) ((ViewGroup) web.getParent()).removeView(web);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        Web.pushInsets();
    }

    void detachWeb() {
        if (web != null && web.getParent() == root) root.removeView(web);
    }

    void showCustom(View v) {
        root.addView(v, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        WindowInsetsController c = getWindow().getInsetsController();
        if (c != null) c.hide(WindowInsets.Type.systemBars());
    }

    void hideCustom(View v) {
        root.removeView(v);
        WindowInsetsController c = getWindow().getInsetsController();
        if (c != null) c.show(WindowInsets.Type.systemBars());
    }

    @Override protected void onNewIntent(Intent i) { super.onNewIntent(i); route(i); }

    private void route(Intent i) {                             // launcher shortcuts: long-press the icon for Music, Gallery, Files
        if (i == null) return;
        if (i.hasExtra("hash")) Web.go(i.getStringExtra("hash"));
        else if (Intent.ACTION_SEND.equals(i.getAction()) && i.getStringExtra(Intent.EXTRA_TEXT) != null) {     // shared text: search for it
            String t = i.getStringExtra(Intent.EXTRA_TEXT).trim();
            if (t.length() > 200) t = t.substring(0, 200);
            Web.js("if (window.mzShareSearch) mzShareSearch(" + org.json.JSONObject.quote(t) + "); else addEventListener('load', function () { mzShareSearch && mzShareSearch(" + org.json.JSONObject.quote(t) + "); });");
        }
    }

    // The system back swipe at the left edge would swallow the page's own edge swipe, so while reclmr is on screen its sensitivity is 0.
    // The value you had is kept in preferences and put back the moment the app leaves the screen (or, if it died first, the next time it opens).
    private static final String[] EDGES = { "back_gesture_inset_scale_left" };
    private static float sane(float v) { return v < 0.05f ? 1f : v; }          // 0 is what this app sets, never what you chose: a stray 0 is never kept as "your" value
    private void edges(boolean off) {
        try {
            android.content.SharedPreferences sp = getSharedPreferences("edge", MODE_PRIVATE);
            android.content.ContentResolver cr = getContentResolver();
            if (checkSelfPermission("android.permission.WRITE_SECURE_SETTINGS") != PackageManager.PERMISSION_GRANTED) return;
            if (sp.contains("orig")) { sp.edit().putFloat("orig_" + EDGES[0], sp.getFloat("orig", 1f)).remove("orig").apply(); }       // the first version kept one value
            for (String k : EDGES) {
                String key = "orig_" + k;
                if (off) {
                    if (!sp.contains(key)) sp.edit().putFloat(key, sane(android.provider.Settings.Secure.getFloat(cr, k, 1f))).apply();
                    android.provider.Settings.Secure.putFloat(cr, k, 0f);
                } else if (sp.contains(key)) {
                    android.provider.Settings.Secure.putFloat(cr, k, sane(sp.getFloat(key, 1f)));
                    sp.edit().remove(key).apply();
                }
            }
        } catch (Exception ignored) { }
    }

    @Override protected void onResume() { super.onResume(); Web.visible = true; Web.wake(); edges(true); }

    @Override protected void onPause() { super.onPause(); Web.visible = false; Web.maybeSleep(); edges(false); }

    @Override protected void onDestroy() {
        super.onDestroy();
        detachWeb();
        if (Web.ctx != null) Web.ctx.setBaseContext(getApplicationContext());
        if (Web.act == this) Web.act = null;
    }

    @Override public void onBackPressed() {
        if (Web.custom != null) { WebChromeClient c = web.getWebChromeClient(); if (c != null) c.onHideCustomView(); return; }
        if (web.canGoBack()) web.goBack(); else moveTaskToBack(true);       // the app stays alive, the player keeps its place
    }

    @Override protected void onActivityResult(int req, int res, Intent data) {
        super.onActivityResult(req, res, data);
        if (req == Web.REQ_FILE && Web.chooser != null) {
            Web.chooser.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(res, data));
            Web.chooser = null;
        }
    }

    @Override public void onRequestPermissionsResult(int req, String[] perms, int[] results) {
        if (req == Web.REQ_MIC && Web.pendingPerm != null) {
            boolean ok = results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED;
            if (ok) Web.pendingPerm.grant(Web.pendingPerm.getResources()); else Web.pendingPerm.deny();
            Web.pendingPerm = null;
        }
    }
}
