package io.decloud.pusher;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Environment;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.*;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.text.DateFormat;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;

/** One pass: find files the server has not confirmed yet, ask it which it lacks, send those. Never deletes or changes anything on the phone. Everything it does goes to the log. */
final class Sync {
    interface Stop { boolean now(); }

    /** {folder on the phone, folder name on the server} */
    static final String[][] SOURCES = {{"DCIM/Camera", "Camera"}, {"DCIM/Screenshots", "Screenshots"}, {"Download", "Downloads"}, {"Documents", "Documents"}};
    static final int BATCH = 400;
    // Android hides a photo until the camera has finished writing it (its name starts with .pending-), which this skips, so only a few seconds of margin are needed
    static final long SETTLE_MS = 8_000;
    private static final AtomicBoolean BUSY = new AtomicBoolean();
    private static volatile boolean again, bigNext;

    /** One pass at a time. A request that arrives while a pass is running makes it look once more when it finishes, instead of starting a second one.
     *  allBig: also send files over the mobile-data limit now (the notification's "Send now"). */
    static void run(Context c, Stop stop, boolean allBig) throws IOException {
        if (!BUSY.compareAndSet(false, true)) { again = true; if (allBig) bigNext = true; Log.add(c, "a pass is already running; it will look again when it finishes"); return; }
        State.begin();
        try { do { again = false; boolean big = allBig || bigNext; bigNext = false; pass(c, stop, big); } while (again && !stop.now()); }
        finally { State.end(); BUSY.set(false); }
    }

    /** Files over this many bytes wait for Wi-Fi (or your OK) when the network is mobile data or any metered connection. */
    static long bigLimit(Context c) { return c.getSharedPreferences("s", 0).getInt("bigmb", 25) * 1024L * 1024L; }

    static boolean metered(Context c) { return c.getSystemService(android.net.ConnectivityManager.class).isActiveNetworkMetered(); }

    private static void pass(Context c, Stop stop, boolean allBig) throws IOException {
        if (!Environment.isExternalStorageManager()) throw new IOException("the app has no 'All files access' (open the app and tap Files access)");
        long t0 = System.currentTimeMillis();
        File sd = Environment.getExternalStorageDirectory();
        Set<String> done = loadDone(c);
        Log.add(c, "pass started; " + done.size() + " files already confirmed by the server");

        State.phase = "scanning folders";
        List<String[]> todo = new ArrayList<>();           // {server path, phone path, size, mtime}
        long now = System.currentTimeMillis();
        int eligible = 0;
        for (String[] s : SOURCES) {
            int before = todo.size();
            List<String> fresh = new ArrayList<>();
            int n = walk(new File(sd, s[0]), s[1], todo, done, now, fresh);
            eligible += n;
            Log.add(c, "  scanned " + s[0] + ": " + n + " files, " + (todo.size() - before) + " not confirmed yet");
            for (String f : fresh) Log.add(c, "    waiting: " + f);
        }
        for (int i = 0; i < Math.min(8, todo.size()); i++) Log.add(c, "    new: " + todo.get(i)[0] + " (" + human(Long.parseLong(todo.get(i)[2])) + ")");
        if (todo.size() > 8) Log.add(c, "    ... and " + (todo.size() - 8) + " more");
        Log.add(c, "  scan took " + (System.currentTimeMillis() - now) + " ms");

        State.total = todo.size();
        boolean metered = metered(c);
        long limit = bigLimit(c);
        Log.add(c, "  network is " + (metered ? "metered (mobile data)" : "unmetered") + (metered && !allBig ? ": files over " + human(limit) + " wait for Wi-Fi or your OK" : allBig && metered ? ": sending big files too, as you asked" : ""));
        int sent = 0, have = 0, failed = 0, held = 0;
        long heldBytes = 0;
        boolean complete = true;
        for (int i = 0; i < todo.size(); i += BATCH) {
            if (stop.now()) { complete = false; break; }
            List<String[]> part = todo.subList(i, Math.min(todo.size(), i + BATCH));
            State.phase = "asking the server (" + Math.min(i + BATCH, todo.size()) + "/" + todo.size() + ")";
            long q = System.currentTimeMillis();
            Set<String> need = check(part);
            Log.add(c, "  server check: " + part.size() + " files, it needs " + need.size() + " (" + (System.currentTimeMillis() - q) + " ms)");
            for (String[] f : part) {
                if (stop.now()) { complete = false; break; }
                if (!need.contains(f[0])) { mark(c, done, f); have++; State.haveFiles = have; continue; }
                long size = Long.parseLong(f[2]);
                if (metered && !allBig && size > limit) { held++; heldBytes += size; if (held <= 5) Log.add(c, "  held " + f[0] + " (" + human(size) + ", over the mobile-data limit)"); continue; }
                State.phase = "uploading";
                if (upload(c, f, stop)) { mark(c, done, f); sent++; State.sentFiles = sent; } else if (!stop.now()) { failed++; State.failedFiles = failed; }
            }
        }
        if (held > 0) { Notif.held(c, held, heldBytes); Log.add(c, "  " + held + " large files (" + human(heldBytes) + ") are waiting: notification posted"); }
        else if (complete) Notif.clear(c);                 // nothing is waiting any more
        SharedPreferences sp = c.getSharedPreferences("s", 0);
        SharedPreferences.Editor ed = sp.edit().putInt("sent", sp.getInt("sent", 0) + sent).putInt("held", held).putLong("heldb", heldBytes).putInt("conf", done.size());
        if (complete && !stop.now()) ed.putLong("last", System.currentTimeMillis());
        ed.apply();
        String sum = (stop.now() ? "stopped early: " : "") + sent + " sent (" + human(State.sentBytes) + "), " + have + " already on the server, " + failed + " failed, " + held + " held for Wi-Fi, "
            + eligible + " files looked at, " + ((System.currentTimeMillis() - t0) / 1000) + " s";
        Log.add(c, "pass finished: " + sum);
        note(c, sum);
    }

    static void note(Context c, String s) {
        c.getSharedPreferences("s", 0).edit().putString("note", DateFormat.getDateTimeInstance().format(new Date()) + ": " + s).apply();
    }

    static String human(long b) {
        if (b < 1024) return b + " B";
        if (b < 1024 * 1024) return String.format(Locale.US, "%.1f KB", b / 1024.0);
        if (b < 1024L * 1024 * 1024) return String.format(Locale.US, "%.1f MB", b / 1048576.0);
        return String.format(Locale.US, "%.2f GB", b / 1073741824.0);
    }

    /** Returns how many eligible files the folder holds; the ones the server has not confirmed are added to out. */
    private static int walk(File dir, String top, List<String[]> out, Set<String> done, long now, List<String> fresh) {
        File[] kids = dir.listFiles();
        if (kids == null) return 0;
        String base = dir.getPath();
        int count = 0;
        Deque<File> stack = new ArrayDeque<>(Arrays.asList(kids));
        while (!stack.isEmpty()) {
            File f = stack.pop();
            String n = f.getName();
            if (n.startsWith(".") || n.contains(".pending-") || n.contains(".trashed-")) continue;     // hidden, half-written or in Android's trash
            if (f.isDirectory()) { File[] k = f.listFiles(); if (k != null) stack.addAll(Arrays.asList(k)); continue; }
            String low = n.toLowerCase(Locale.ROOT);
            if (low.endsWith(".tmp") || low.endsWith(".part") || low.endsWith(".crdownload") || low.endsWith(".download")) continue;
            long size = f.length(), m = f.lastModified();
            if (size == 0) continue;
            if (m > now - SETTLE_MS) { if (fresh.size() < 5) fresh.add(top + "/" + n + " (" + (now - m) / 1000 + " s old, still settling)"); continue; }
            count++;
            String rel = top + "/" + f.getPath().substring(base.length() + 1);
            if (!done.contains(key(rel, size, m))) out.add(new String[]{rel, f.getPath(), Long.toString(size), Long.toString(m)});
        }
        return count;
    }

    private static Set<String> check(List<String[]> part) throws IOException {
        JSONArray files = new JSONArray();
        for (String[] f : part) files.put(new JSONArray().put(f[0]).put(Long.parseLong(f[2])));
        byte[] body;
        try { body = new JSONObject().put("files", files).toString().getBytes(StandardCharsets.UTF_8); } catch (Exception e) { throw new IOException(e); }
        HttpURLConnection h = open("/api/backup/check", "POST");
        h.setFixedLengthStreamingMode(body.length);
        h.setRequestProperty("Content-Type", "application/json");
        try (OutputStream o = h.getOutputStream()) { o.write(body); }
        String reply = read(h);
        Set<String> need = new HashSet<>();
        try { JSONArray a = new JSONObject(reply).getJSONArray("need"); for (int i = 0; i < a.length(); i++) need.add(a.getString(i)); }
        catch (Exception e) { throw new IOException("the server's reply was not understood: " + reply); }
        return need;
    }

    private static boolean upload(Context c, String[] f, Stop stop) throws IOException {
        File file = new File(f[1]);
        long size = Long.parseLong(f[2]);
        State.file = f[0]; State.fileSize = size; State.fileSent = 0; State.fileStart = System.currentTimeMillis();
        Log.add(c, "  up   " + f[0] + " (" + human(size) + ")");
        HttpURLConnection h = open("/api/backup/file?p=" + URLEncoder.encode(f[0], "UTF-8") + "&m=" + f[3], "PUT");
        h.setFixedLengthStreamingMode(size);               // streams from disk; nothing is held in memory
        h.setRequestProperty("Content-Type", "application/octet-stream");
        long sentBytes = 0;
        int quarter = 0;
        try (InputStream in = new FileInputStream(file); OutputStream o = h.getOutputStream()) {
            byte[] buf = new byte[64 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) {
                if (stop.now()) { h.disconnect(); Log.add(c, "  stop " + f[0] + " at " + human(sentBytes) + " of " + human(size) + " (Android asked us to stop)"); return false; }
                o.write(buf, 0, n); sentBytes += n; State.fileSent = sentBytes;
                if (size > 20L * 1024 * 1024 && sentBytes * 4 / size > quarter && quarter < 3) { quarter++; Log.add(c, "       " + f[0] + " " + quarter * 25 + "%"); }
            }
        }
        State.sentBytes += sentBytes;
        if (sentBytes != size) { h.disconnect(); Log.add(c, "  skip " + f[0] + ": it changed while being read, next pass"); return false; }
        int code = h.getResponseCode();
        String reply = read(h, false);
        double secs = Math.max(0.001, (System.currentTimeMillis() - State.fileStart) / 1000.0);
        if (code / 100 == 2) {
            boolean existed = reply.contains("\"existed\":true");
            Log.add(c, (existed ? "  has  " : "  done ") + f[0] + (existed ? " (the server already had it)" : " in " + String.format(Locale.US, "%.1f", secs) + " s (" + human((long) (size / secs)) + "/s)"));
            return true;
        }
        Log.add(c, "  FAIL " + f[0] + ": HTTP " + code + " " + clip(reply));
        return false;
    }

    private static HttpURLConnection open(String path, String method) throws IOException {
        HttpURLConnection h = (HttpURLConnection) new URL(Config.BASE_URL + path).openConnection();
        h.setRequestMethod(method);
        h.setDoOutput(true);
        h.setConnectTimeout(20_000);
        h.setReadTimeout(120_000);
        h.setRequestProperty("Authorization", "Bearer " + Config.TOKEN);
        return h;
    }

    private static String read(HttpURLConnection h) throws IOException { return read(h, true); }

    private static String read(HttpURLConnection h, boolean strict) throws IOException {
        int code = h.getResponseCode();
        InputStream s = code / 100 == 2 ? h.getInputStream() : h.getErrorStream();
        String body = "";
        if (s != null) {
            try (ByteArrayOutputStream b = new ByteArrayOutputStream()) {
                byte[] buf = new byte[8192]; int n;
                while ((n = s.read(buf)) > 0) b.write(buf, 0, n);
                body = b.toString("UTF-8");
            }
        }
        if (strict && code / 100 != 2) throw new IOException("the server answered HTTP " + code + " " + clip(body));
        return body;
    }

    private static String clip(String s) { s = s.replace('\n', ' ').trim(); return s.length() > 160 ? s.substring(0, 160) + "..." : s; }

    // ── what the server has confirmed, so a pass only looks at new files ──
    private static String key(String rel, long size, long m) { return rel + "|" + size + "|" + m; }
    private static File doneFile(Context c) { return new File(c.getFilesDir(), "done.txt"); }

    private static Set<String> loadDone(Context c) {
        Set<String> s = new HashSet<>();
        try (BufferedReader r = new BufferedReader(new FileReader(doneFile(c)))) { String l; while ((l = r.readLine()) != null) s.add(l); }
        catch (IOException ignored) { }
        return s;
    }

    private static void mark(Context c, Set<String> done, String[] f) throws IOException {
        String k = key(f[0], Long.parseLong(f[2]), Long.parseLong(f[3]));
        done.add(k);
        try (FileWriter w = new FileWriter(doneFile(c), true)) { w.write(k + "\n"); }
    }
}
