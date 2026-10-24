package io.decloud.pusher;

import android.app.Activity;
import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.app.usage.UsageStatsManager;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.res.Configuration;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RadialGradient;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.LayerDrawable;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.PowerManager;
import android.provider.Settings;
import android.text.format.DateUtils;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.text.NumberFormat;
import java.util.List;
import java.util.Locale;

/**
 * One calm screen: a status, one button, a short list. The technical detail (what Android allows, the log) is one tap away under Details.
 * Opening it arms the jobs (a freshly installed app gets no boot broadcast until it has been opened once).
 */
public class MainActivity extends Activity {
    private boolean night;
    private int text, text2, glassFill, glassEdge, blue, green, orange;
    private float d;

    private FrameLayout root;
    private LinearLayout home, details;
    private TextView icon, headline, sub, button, filesVal, limitVal, batteryVal, heldLabel, info, logView;
    private View glow, batteryRow, heldGroup;
    private Bar bar;
    private ScrollView logScroll;
    private String shownLog = "";
    private int shownState = -1;
    private final Handler ui = new Handler();
    private final Runnable tick = new Runnable() { @Override public void run() { refresh(); ui.postDelayed(this, 1000); } };

    private static final int IDLE = 0, SENDING = 1, PERMISSION = 2, ERROR = 3;
    private int state = IDLE;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        d = getResources().getDisplayMetrics().density;
        night = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
        text = night ? 0xFFFFFFFF : 0xFF333333;
        text2 = night ? 0xFF9A9A9A : 0xFF7A7A7A;
        glassFill = night ? 0x33FFFFFF : 0x22000000;          // here: the track of the progress line and the faint button fill
        glassEdge = night ? 0x33FFFFFF : 0x26000000;          // the thin dividers
        blue = 0xFF33B5E5;                                    // Holo blue
        green = 0xFF99CC00;                                   // Holo green
        orange = 0xFFFFBB33;                                  // Holo orange

        root = new FrameLayout(this);
        root.addView(new Wall(this), new FrameLayout.LayoutParams(-1, -1));
        home = buildHome();
        details = buildDetails();
        details.setVisibility(View.GONE);
        root.addView(home, new FrameLayout.LayoutParams(-1, -1));
        root.addView(details, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);

        getWindow().setDecorFitsSystemWindows(false);
        root.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() {
            @Override public WindowInsets onApplyWindowInsets(View v, WindowInsets in) {
                android.graphics.Insets bars = in.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                home.setPadding((int) (20 * d), bars.top + (int) (28 * d), (int) (20 * d), bars.bottom + (int) (18 * d));
                details.setPadding((int) (16 * d), bars.top + (int) (14 * d), (int) (16 * d), bars.bottom + (int) (12 * d));
                return WindowInsets.CONSUMED;
            }
        });
        WindowInsetsController c = getWindow().getInsetsController();
        if (c != null) {
            int light = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
            c.setSystemBarsAppearance(night ? 0 : light, light);
        }

        Sched.all(this);
        if (android.os.Build.VERSION.SDK_INT >= 33 && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS}, 1);      // so it can ask about large files
    }

    // ─── the home screen ───────────────────────────────────────────────

    private LinearLayout buildHome() {
        LinearLayout h = new LinearLayout(this);
        h.setOrientation(LinearLayout.VERTICAL);

        TextView title = label("Backup", 22, false, text);
        title.setTypeface(Typeface.create("sans-serif-light", Typeface.NORMAL));
        title.setPadding(dp(2), 0, 0, dp(10));
        h.addView(title);
        View rule = new View(this);
        rule.setBackgroundColor(blue);
        h.addView(rule, new LinearLayout.LayoutParams(-1, dp(2)));

        LinearLayout center = new LinearLayout(this);
        center.setOrientation(LinearLayout.VERTICAL);
        center.setGravity(Gravity.CENTER);
        FrameLayout iconBox = new FrameLayout(this);
        glow = new View(this);
        iconBox.addView(glow, new FrameLayout.LayoutParams(dp(1), dp(1), Gravity.CENTER));
        icon = label("", 72, false, green);
        icon.setTypeface(Typeface.create("sans-serif-thin", Typeface.NORMAL));
        icon.setGravity(Gravity.CENTER);
        iconBox.addView(icon, new FrameLayout.LayoutParams(dp(120), dp(120), Gravity.CENTER));
        center.addView(iconBox, new LinearLayout.LayoutParams(dp(120), dp(120)));
        headline = label("", 26, false, text);
        headline.setTypeface(Typeface.create("sans-serif-light", Typeface.NORMAL));
        headline.setGravity(Gravity.CENTER);
        center.addView(headline);
        sub = label("", 16, false, text2);
        sub.setGravity(Gravity.CENTER);
        sub.setPadding(dp(24), dp(4), dp(24), 0);
        center.addView(sub);
        bar = new Bar(this);
        LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(dp(200), dp(3));
        bp.topMargin = dp(14);
        center.addView(bar, bp);
        h.addView(center, new LinearLayout.LayoutParams(-1, 0, 1f));

        button = label("", 17, true, Color.WHITE);
        button.setGravity(Gravity.CENTER);
        button.setBackground(capsule());
        button.setPadding(0, dp(16), 0, dp(16));
        LinearLayout.LayoutParams bl = new LinearLayout.LayoutParams(-1, -2);
        bl.bottomMargin = dp(14);
        h.addView(button, bl);

        // large files waiting (only shown when there are some)
        LinearLayout hg = group();
        heldLabel = new TextView(this);
        hg.addView(row("", heldLabel, "Send", new View.OnClickListener() { @Override public void onClick(View v) { syncNow(true); } }, true));
        heldGroup = hg;
        h.addView(hg, groupParams());

        LinearLayout g = group();
        filesVal = new TextView(this);
        g.addView(row("Files on server", filesVal, null, null, false));
        limitVal = new TextView(this);
        g.addView(row("Mobile data limit", limitVal, null, new View.OnClickListener() { @Override public void onClick(View v) { nextLimit(); } }, false));
        batteryVal = new TextView(this);
        batteryRow = row("Battery unrestricted", batteryVal, null, new View.OnClickListener() { @Override public void onClick(View v) {
            startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
        } }, false);
        g.addView(batteryRow);
        g.addView(row("Details", new TextView(this), null, new View.OnClickListener() { @Override public void onClick(View v) { showDetails(true); } }, false));
        h.addView(g, groupParams());
        return h;
    }

    private void nextLimit() {
        int[] opts = {5, 25, 100, 500};
        int cur = getSharedPreferences("s", 0).getInt("bigmb", 25), next = opts[0];
        for (int i = 0; i < opts.length; i++) if (opts[i] == cur && i + 1 < opts.length) next = opts[i + 1];
        getSharedPreferences("s", 0).edit().putInt("bigmb", next).apply();
        Log.add(this, "mobile-data limit set to " + next + " MB per file");
        refresh();
    }

    /** Runs the pass right here: the app is open, so there is nothing to wait for from Android's scheduler. The screen stays on until it is done. */
    private void syncNow(final boolean allBig) {
        getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        new Thread(new Runnable() { @Override public void run() {
            Log.add(MainActivity.this, allBig ? "Send big files: running a pass right here, large files included" : "Sync now: running a pass right here");
            try { Sync.run(MainActivity.this, new Sync.Stop() { @Override public boolean now() { return false; } }, allBig); }
            catch (Exception e) {
                Log.add(MainActivity.this, "PASS FAILED: " + e.getClass().getSimpleName() + ": " + e.getMessage());
                Sync.note(MainActivity.this, "failed: " + e.getMessage());
            }
            runOnUiThread(new Runnable() { @Override public void run() { getWindow().clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON); } });
        } }, "sync-now").start();
        refresh();
    }

    // ─── details: what Android allows, and the log ──────────────────────

    private LinearLayout buildDetails() {
        LinearLayout v = new LinearLayout(this);
        v.setOrientation(LinearLayout.VERTICAL);
        TextView back = label("‹ Backup", 17, true, blue);
        back.setPadding(dp(4), dp(8), dp(12), dp(10));
        back.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View x) { showDetails(false); } });
        v.addView(back);

        info = label("", 12, false, text);
        info.setTypeface(Typeface.MONOSPACE);
        info.setPadding(dp(4), dp(14), dp(4), dp(14));
        info.setBackground(glass(0, false));
        v.addView(info, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout actions = new LinearLayout(this);
        actions.setPadding(0, dp(10), 0, dp(10));
        actions.addView(small("Send big files", new View.OnClickListener() { @Override public void onClick(View x) { syncNow(true); } }));
        actions.addView(small("Files access", new View.OnClickListener() { @Override public void onClick(View x) { openFilesAccess(); } }));
        actions.addView(small("Copy log", new View.OnClickListener() { @Override public void onClick(View x) {
            ((ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("pusher log", Log.tail(MainActivity.this, 400_000)));
            Toast.makeText(MainActivity.this, "Log copied", Toast.LENGTH_SHORT).show();
        } }));
        actions.addView(small("Clear", new View.OnClickListener() { @Override public void onClick(View x) { Log.clear(MainActivity.this); shownLog = ""; refresh(); } }));
        v.addView(actions);

        logScroll = new ScrollView(this);
        logView = label("", 10.5f, false, text2);
        logView.setTypeface(Typeface.MONOSPACE);
        logView.setTextIsSelectable(true);
        logView.setPadding(dp(4), 0, dp(4), 0);
        logScroll.addView(logView);
        v.addView(logScroll, new LinearLayout.LayoutParams(-1, 0, 1f));
        return v;
    }

    private void showDetails(boolean on) {
        details.setVisibility(on ? View.VISIBLE : View.GONE);
        home.setVisibility(on ? View.GONE : View.VISIBLE);
        refresh();
    }

    @Override public void onBackPressed() { if (details.getVisibility() == View.VISIBLE) showDetails(false); else super.onBackPressed(); }

    private void openFilesAccess() {
        startActivity(new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:" + getPackageName())));
    }

    // ─── keeping the screen current ─────────────────────────────────────

    @Override protected void onResume() { super.onResume(); ui.post(tick); }
    @Override protected void onPause() { super.onPause(); ui.removeCallbacks(tick); }

    /** Left while a pass is running (home, or swiped away from recents): hand it to Android so it still finishes. */
    @Override protected void onStop() { super.onStop(); if (State.running) Sched.carryOn(this); }

    private void refresh() {
        if (details.getVisibility() == View.VISIBLE) { refreshDetails(); return; }
        SharedPreferences sp = getSharedPreferences("s", 0);
        String note = sp.getString("note", "");
        boolean perm = Environment.isExternalStorageManager();
        state = !perm ? PERMISSION : State.running ? SENDING : note.contains(": failed") ? ERROR : IDLE;

        int color = state == SENDING ? blue : state == IDLE ? green : orange;
        icon.setTextColor(color);
        icon.setText(state == IDLE ? "✓" : state == SENDING ? "↑" : "!");
        shownState = state;
        NumberFormat nf = NumberFormat.getIntegerInstance();

        if (state == PERMISSION) {
            headline.setText("Needs one thing");
            sub.setText("Allow access to your files so photos can be backed up");
            setButton("Allow files access", new View.OnClickListener() { @Override public void onClick(View v) { openFilesAccess(); } });
        } else if (state == SENDING) {
            headline.setText("Backing up");
            int done = State.sentFiles + State.haveFiles + State.failedFiles;
            sub.setText(State.total > 0 ? Math.min(done + 1, State.total) + " of " + nf.format(State.total) + " files" : State.phase);
            setButton(null, null);
        } else if (state == ERROR) {
            headline.setText("Couldn't back up");
            String why = note.substring(note.indexOf(": failed") + 8).replaceFirst("^:\\s*", "");
            sub.setText(why.isEmpty() ? "Check the connection and try again" : why.length() > 70 ? why.substring(0, 70) + "…" : why);
            setButton("Try again", new View.OnClickListener() { @Override public void onClick(View v) { syncNow(false); } });
        } else {
            headline.setText("Backed up");
            long last = sp.getLong("last", 0);
            sub.setText(last > 0 ? "Last backup " + DateUtils.getRelativeTimeSpanString(last, System.currentTimeMillis(), DateUtils.MINUTE_IN_MILLIS).toString().toLowerCase(Locale.getDefault())
                : "Waiting for the first backup");
            setButton("Back up now", new View.OnClickListener() { @Override public void onClick(View v) { syncNow(false); } });
        }

        if (state == SENDING && State.fileSize > 0) { bar.set(State.fileSent / (float) State.fileSize); bar.setVisibility(View.VISIBLE); }
        else bar.setVisibility(state == SENDING ? View.VISIBLE : View.GONE);
        if (state == SENDING && State.fileSize <= 0) bar.set(0);

        int held = sp.getInt("held", 0);
        heldGroup.setVisibility(held > 0 ? View.VISIBLE : View.GONE);
        heldLabel.setText(held > 0 ? held + (held == 1 ? " large file" : " large files") + " waiting for Wi-Fi" : "");
        int conf = sp.getInt("conf", 0);
        filesVal.setText(conf > 0 ? nf.format(conf) : "–");
        limitVal.setText(sp.getInt("bigmb", 25) + " MB");
        boolean batt = getSystemService(PowerManager.class).isIgnoringBatteryOptimizations(getPackageName());
        batteryRow.setVisibility(batt ? View.GONE : View.VISIBLE);
        batteryVal.setText("Not yet");
        for (TextView t : new TextView[]{filesVal, limitVal, batteryVal}) { t.setTextColor(text2); t.setTextSize(17); }
    }

    private void setButton(String label, View.OnClickListener l) {
        if (label == null) { button.setVisibility(View.GONE); return; }
        button.setVisibility(View.VISIBLE);
        button.setText(label);
        button.setOnClickListener(l);
    }

    private void refreshDetails() {
        info.setText(diagnostics());
        String log = Log.tail(this, 60_000);
        if (!log.equals(shownLog)) {                                   // leave the text alone when nothing changed, so selecting it works
            boolean atEnd = logScroll.getChildAt(0).getBottom() - (logScroll.getHeight() + logScroll.getScrollY()) < 120;
            logView.setText(log.isEmpty() ? "(the log is empty)" : log);
            shownLog = log;
            if (atEnd) logScroll.post(new Runnable() { @Override public void run() { logScroll.fullScroll(View.FOCUS_DOWN); } });
        }
    }

    /** Everything that decides whether Android lets a pass run now, so a quiet app can be told apart from a blocked one. */
    private String diagnostics() {
        StringBuilder s = new StringBuilder();
        s.append("Files access : ").append(Environment.isExternalStorageManager() ? "granted" : "NOT GRANTED (tap Files access)").append('\n');
        s.append("Server       : ").append(Config.BASE_URL).append('\n');

        ConnectivityManager cm = getSystemService(ConnectivityManager.class);
        Network n = cm.getActiveNetwork();
        NetworkCapabilities nc = n == null ? null : cm.getNetworkCapabilities(n);
        String net = "none";
        if (nc != null) {
            net = nc.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ? "Wi-Fi" : nc.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) ? "mobile data" : nc.hasTransport(NetworkCapabilities.TRANSPORT_VPN) ? "VPN" : "other";
            net += nc.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED) ? ", unmetered: everything is sent" : ", METERED: files over " + getSharedPreferences("s", 0).getInt("bigmb", 25) + " MB wait";
            if (nc.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) net += ", via VPN";
        }
        s.append("Network      : ").append(net).append('\n');
        int ds = cm.getRestrictBackgroundStatus();
        if (ds == ConnectivityManager.RESTRICT_BACKGROUND_STATUS_ENABLED) s.append("Data Saver   : ON and the app is NOT exempt: no background data on mobile (allow it under Settings > Connections > Data usage)\n");

        Intent bi = registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if (bi != null) {
            int lvl = bi.getIntExtra(BatteryManager.EXTRA_LEVEL, -1), scale = bi.getIntExtra(BatteryManager.EXTRA_SCALE, 100);
            int pct = scale > 0 ? lvl * 100 / scale : -1;
            boolean plugged = bi.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0;
            s.append("Battery      : ").append(pct).append("%").append(plugged ? ", charging" : "").append(pct >= 0 && pct <= 15 && !plugged ? "  (LOW: automatic jobs wait)" : "").append('\n');
        }

        UsageStatsManager um = getSystemService(UsageStatsManager.class);
        int bucket = um.getAppStandbyBucket();
        String bn = bucket <= 10 ? "active" : bucket <= 20 ? "working set" : bucket <= 30 ? "frequent" : bucket <= 40 ? "rare (jobs can be delayed for hours)" : bucket <= 45 ? "restricted (jobs heavily delayed)" : "never";
        PowerManager pm = getSystemService(PowerManager.class);
        s.append("Standby      : ").append(bn).append('\n');
        s.append("Doze         : ").append(pm.isDeviceIdleMode() ? "device is dozing (jobs wait for a maintenance window)" : "awake").append(pm.isIgnoringBatteryOptimizations(getPackageName()) ? ", battery unrestricted" : ", battery optimised").append('\n');

        JobScheduler js = getSystemService(JobScheduler.class);
        List<JobInfo> jobs = js.getAllPendingJobs();
        if (jobs.isEmpty()) s.append("Jobs         : NONE ARMED (open the app once, or reinstall)\n");
        for (JobInfo j : jobs) {
            String what = j.getId() == Sched.PERIODIC ? "every " + j.getIntervalMillis() / 3600_000 + " h" : "when new files appear";
            s.append("Job ").append(j.getId()).append("        : ").append(what).append(j.getNetworkType() == JobInfo.NETWORK_TYPE_UNMETERED ? ", Wi-Fi only" : ", any network").append(j.isPersisted() ? ", survives reboot" : "").append('\n');
        }
        s.append("State        : ").append(State.running ? State.phase : "idle").append('\n');
        s.append(String.format(Locale.US, "Sent so far  : %d files total", getSharedPreferences("s", 0).getInt("sent", 0)));
        if (State.running) s.append(String.format(Locale.US, "  (this pass: %d sent, %d on server, %d failed)", State.sentFiles, State.haveFiles, State.failedFiles));
        return s.toString();
    }

    // ─── the look: a soft wallpaper and frosted panels ──────────────────

    private int dp(int v) { return Math.round(v * d); }

    private TextView label(String t, float sp, boolean bold, int color) {
        TextView v = new TextView(this);
        v.setText(t);
        v.setTextSize(sp);
        v.setTextColor(color);
        v.setTypeface(Typeface.create("sans-serif", bold ? Typeface.BOLD : Typeface.NORMAL));
        return v;
    }

    /** Holo draws no cards: a panel is just the screen itself, so this is an empty background (kept so the callers stay simple). */
    private android.graphics.drawable.Drawable glass(int radius, boolean oval) { return new android.graphics.drawable.ColorDrawable(Color.TRANSPARENT); }

    /** The flat Holo button: a square-cornered block of the accent colour. */
    private GradientDrawable capsule() {
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(2));
        g.setColor(0xFF0099CC);
        return g;
    }

    private static int blend(int a, int b, float t) {
        return Color.rgb((int) (Color.red(a) * (1 - t) + Color.red(b) * t), (int) (Color.green(a) * (1 - t) + Color.green(b) * t), (int) (Color.blue(a) * (1 - t) + Color.blue(b) * t));
    }

    private LinearLayout group() {
        LinearLayout g = new LinearLayout(this);
        g.setOrientation(LinearLayout.VERTICAL);
        GradientDrawable line = new GradientDrawable();
        line.setColor(glassEdge);
        line.setSize(1, Math.max(1, dp(1)));
        g.setDividerDrawable(line);
        g.setShowDividers(LinearLayout.SHOW_DIVIDER_MIDDLE | LinearLayout.SHOW_DIVIDER_END);
        return g;
    }

    private LinearLayout.LayoutParams groupParams() {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2);
        p.bottomMargin = dp(12);
        return p;
    }

    /** A list row: the label on the left, a value or action on the right. {@code end} is a chevron or a blue action word. */
    private View row(String name, TextView value, String end, View.OnClickListener click, boolean actionWord) {
        LinearLayout r = new LinearLayout(this);
        r.setGravity(Gravity.CENTER_VERTICAL);
        r.setPadding(dp(6), dp(15), dp(6), dp(15));
        TextView l = actionWord ? value : label(name, 17, false, text);
        if (actionWord) { value.setTextColor(text); value.setTextSize(17); }
        r.addView(l, new LinearLayout.LayoutParams(0, -2, 1f));
        if (!actionWord) {
            value.setTextColor(text2);
            value.setTextSize(17);
            r.addView(value);
        }
        if (end != null) {
            TextView e = label(end, actionWord ? 17 : 20, actionWord, actionWord ? blue : text2);
            e.setPadding(dp(8), 0, 0, 0);
            r.addView(e);
        }
        if (click != null) r.setOnClickListener(click);
        return r;
    }

    private TextView small(String t, View.OnClickListener l) {
        TextView b = label(t, 12, true, text);
        b.setGravity(Gravity.CENTER);
        b.setBackground(glass(dp(20), false));
        b.setPadding(dp(10), dp(9), dp(10), dp(9));
        b.setOnClickListener(l);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(0, -2, 1f);
        p.rightMargin = dp(6);
        b.setLayoutParams(p);
        return b;
    }

    /** The Holo background: black at the top fading to a dark blue-grey at the bottom (measured from the Android 4 Settings screenshot: #000000 down to #252A30). */
    private final class Wall extends View {
        private final Paint p = new Paint();
        Wall(Context c) { super(c); }
        @Override protected void onDraw(Canvas cv) {
            p.setShader(new android.graphics.LinearGradient(0, 0, 0, getHeight(),
                night ? 0xFF000000 : 0xFFFFFFFF, night ? 0xFF252A30 : 0xFFE3E8EC, Shader.TileMode.CLAMP));
            cv.drawRect(0, 0, getWidth(), getHeight(), p);
        }
    }

    /** The coloured glow behind the status icon. */
    private static final class GlowDrawable extends android.graphics.drawable.Drawable {
        private final Paint p = new Paint();
        private final int color;
        GlowDrawable(int color) { this.color = color; }
        @Override public void draw(Canvas c) {
            float r = Math.min(getBounds().width(), getBounds().height()) / 2f;
            p.setShader(new RadialGradient(getBounds().exactCenterX(), getBounds().exactCenterY(), r, new int[]{(color & 0x00FFFFFF) | 0x66000000, color & 0x00FFFFFF}, null, Shader.TileMode.CLAMP));
            c.drawCircle(getBounds().exactCenterX(), getBounds().exactCenterY(), r, p);
        }
        @Override public void setAlpha(int a) {}
        @Override public void setColorFilter(android.graphics.ColorFilter f) {}
        @Override public int getOpacity() { return android.graphics.PixelFormat.TRANSLUCENT; }
    }

    /** The thin progress line under the status. */
    private final class Bar extends View {
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        private float v;
        Bar(Context c) { super(c); }
        void set(float x) { v = Math.max(0, Math.min(1, x)); invalidate(); }
        @Override protected void onDraw(Canvas cv) {
            float r = getHeight() / 2f;
            RectF all = new RectF(0, 0, getWidth(), getHeight());
            p.setColor(glassFill); cv.drawRoundRect(all, r, r, p);
            p.setColor(blue); cv.drawRoundRect(new RectF(0, 0, Math.max(getHeight(), getWidth() * v), getHeight()), r, r, p);
        }
    }
}
