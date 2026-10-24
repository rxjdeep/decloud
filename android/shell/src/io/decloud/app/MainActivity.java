package io.decloud.app;

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

/** DeCloud itself: the shared WebView, drawn edge to edge under transparent system bars. */
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
        if (i != null && i.hasExtra("hash")) Web.go(i.getStringExtra("hash"));
    }

    @Override protected void onResume() { super.onResume(); Web.visible = true; Web.wake(); }

    @Override protected void onPause() { super.onPause(); Web.visible = false; Web.maybeSleep(); }

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
