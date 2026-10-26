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

    /** Stop everything: Android asked, the user pressed Stop, or the server refused in a way that makes more uploads pointless. */
    static boolean halt(Stop s) { return s.now() || State.cancel || !State.fatal.isEmpty(); }

    // ── files the user chose to skip, or that failed too often, so they are not tried again and again ──
    static Set<String> skipped(Context c) { return new HashSet<>(c.getSharedPreferences("s", 0).getStringSet("skip", new HashSet<String>())); }
    static void skip(Context c, String rel, long size, long mtime) {
        Set<String> k = skipped(c); k.add(key(rel, size, mtime));
        c.getSharedPreferences("s", 0).edit().putStringSet("skip", k).apply();
        Log.add(c, "skipped for good (until you undo it): " + rel);
    }
    static void clearSkips(Context c) { c.getSharedPreferences("s", 0).edit().remove("skip").apply(); Log.add(c, "the skipped files will be tried again"); }
    private static int bump(Context c, String key, boolean ok) {
        SharedPreferences sp = c.getSharedPreferences("f", 0);
        int n = ok ? 0 : sp.getInt(key, 0) + 1;
        if (ok) sp.edit().remove(key).apply(); else sp.edit().putInt(key, n).apply();
        return n;
    }

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
        try { do { again = false; boolean big = allBig || bigNext; bigNext = false; pass(c, stop, big); } while (again && !halt(stop)); }
        finally { State.end(); Notif.runningClear(c); BUSY.set(false); }
    }

    /** Files over this many bytes wait for Wi-Fi (or your OK) when the network is mobile data or any metered connection. */
    static long bigLimit(Context c) { return c.getSharedPreferences("s", 0).getInt("bigmb", 25) * 1024L * 1024L; }

    /** Mobile data counts as metered even when the carrier flags the connection as unmetered (unlimited plans do), so large files still ask first. */
    static boolean metered(Context c) {
        android.net.ConnectivityManager cm = c.getSystemService(android.net.ConnectivityManager.class);
        if (cm.isActiveNetworkMetered()) return true;
        android.net.Network n = cm.getActiveNetwork();
        android.net.NetworkCapabilities nc = n == null ? null : cm.getNetworkCapabilities(n);
        return nc != null && nc.hasTransport(android.net.NetworkCapabilities.TRANSPORT_CELLULAR) && !nc.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI);
    }

    private static void pass(Context c, Stop stop, boolean allBig) throws IOException {
        if (!Environment.isExternalStorageManager()) throw new IOException("the app has no 'All files access' (open the app and tap Files access)");
        long t0 = System.currentTimeMillis();
        File sd = Environment.getExternalStorageDirectory();
        Set<String> done = loadDone(c);
        Set<String> skip = skipped(c);
        Log.add(c, "pass started; " + done.size() + " files already confirmed by the server");

        State.phase = "scanning folders";
        List<String[]> todo = new ArrayList<>();           // {server path, phone path, size, mtime}
        long now = System.currentTimeMillis();
        int eligible = 0;
        for (String[] s : SOURCES) {
            int before = todo.size();
            List<String> fresh = new ArrayList<>();
            int n = walk(new File(sd, s[0]), s[1], todo, done, skip, now, fresh);
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
            if (halt(stop)) { complete = false; break; }
            List<String[]> part = todo.subList(i, Math.min(todo.size(), i + BATCH));
            State.phase = "asking the server (" + Math.min(i + BATCH, todo.size()) + "/" + todo.size() + ")";
            long q = System.currentTimeMillis();
            Set<String> need = check(part);
            Log.add(c, "  server check: " + part.size() + " files, it needs " + need.size() + " (" + (System.currentTimeMillis() - q) + " ms)");
            for (String[] f : part) {
                if (halt(stop)) { complete = false; break; }
                if (!need.contains(f[0])) { mark(c, done, f); have++; State.haveFiles = have; continue; }
                long size = Long.parseLong(f[2]);
                if (metered && !allBig && size > limit) { held++; heldBytes += size; if (held <= 5) Log.add(c, "  held " + f[0] + " (" + human(size) + ", over the mobile-data limit)"); continue; }
                State.phase = "uploading";
                Notif.running(c, "Sending " + f[0].substring(f[0].lastIndexOf('/') + 1) + " (" + (sent + have + failed + 1) + " of " + todo.size() + ")");
                if (upload(c, f, stop)) { mark(c, done, f); sent++; State.sentFiles = sent; bump(c, "f:" + key(f[0], size, Long.parseLong(f[3])), true); }
                else if (State.abortFile) { State.abortFile = false; }                                   // skipped by the user: neither sent nor failed
                else if (!halt(stop)) {
                    failed++; State.failedFiles = failed;
                    if (bump(c, "f:" + key(f[0], size, Long.parseLong(f[3])), false) >= 5) { skip(c, f[0], size, Long.parseLong(f[3])); Log.add(c, "  gave up on " + f[0] + " after 5 failed passes: it is skipped now"); }
                }
            }
        }
        if (failed > 0) Notif.failed(c, failed + (failed == 1 ? " file" : " files") + " could not be sent. The connection may be weak; it will carry on from where it stopped on the next try.");
        else if (complete) Notif.failedClear(c);
        if (held > 0) { Notif.held(c, held, heldBytes); Log.add(c, "  " + held + " large files (" + human(heldBytes) + ") are waiting: notification posted"); }
        else if (complete) Notif.clear(c);                 // nothing is waiting any more
        SharedPreferences sp = c.getSharedPreferences("s", 0);
        SharedPreferences.Editor ed = sp.edit().putInt("sent", sp.getInt("sent", 0) + sent).putInt("held", held).putLong("heldb", heldBytes).putInt("conf", done.size());
        if (complete && !halt(stop)) ed.putLong("last", System.currentTimeMillis());
        ed.apply();
        if (!State.fatal.isEmpty()) { Log.add(c, "  STOPPED: " + State.fatal); Notif.failed(c, State.fatal); }
        String sum = (halt(stop) ? "stopped early: " : "") + sent + " sent (" + human(State.sentBytes) + "), " + have + " already on the server, " + failed + " failed, " + held + " held for Wi-Fi, "
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
    private static int walk(File dir, String top, List<String[]> out, Set<String> done, Set<String> skip, long now, List<String> fresh) {
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
            if (!done.contains(key(rel, size, m)) && !skip.contains(key(rel, size, m))) out.add(new String[]{rel, f.getPath(), Long.toString(size), Long.toString(m)});
        }
        return count;
    }

    private static java.util.Map<String, Long> partial = new java.util.HashMap<>();      // path -> bytes the server already has of an interrupted upload

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
        try {
            JSONObject o = new JSONObject(reply);
            JSONArray a = o.getJSONArray("need");
            for (int i = 0; i < a.length(); i++) need.add(a.getString(i));
            JSONObject pa = o.optJSONObject("partial");
            if (pa != null) { java.util.Iterator<String> k = pa.keys(); while (k.hasNext()) { String key = k.next(); partial.put(key, pa.getLong(key)); } }
        }
        catch (Exception e) { throw new IOException("the server's reply was not understood: " + reply); }
        return need;
    }

    /** Sends one file. A cut connection keeps what arrived: the next attempt asks how far it got and sends only the rest, so a weak mobile uplink still gets there. */
    private static boolean upload(Context c, String[] f, Stop stop) throws IOException {
        for (int attempt = 1; attempt <= 4; attempt++) {
            if (halt(stop) || State.abortFile) return false;
            if (attempt > 1) {
                try { Thread.sleep(attempt * 3000L); } catch (InterruptedException e) { return false; }
                try { List<String[]> one = new ArrayList<>(); one.add(f); if (!check(one).contains(f[0])) return true; }       // the server has it after all
                catch (IOException e) { Log.add(c, "  could not ask the server before retry " + attempt + ": " + e.getMessage()); continue; }
                Log.add(c, "  retry " + attempt + " of 4: " + f[0]);
            }
            if (uploadOnce(c, f, stop)) return true;
            if (State.abortFile) return false;
        }
        return false;
    }

    private static boolean uploadOnce(Context c, String[] f, Stop stop) throws IOException {
        File file = new File(f[1]);
        long size = Long.parseLong(f[2]);
        Long have = partial.remove(f[0]);
        long off = have != null && have > 0 && have < size ? have : 0;
        State.file = f[0]; State.fileRel = f[0]; State.fileSize = size; State.fileSent = off; State.fileOff = off; State.fileMtime = Long.parseLong(f[3]); State.fileStart = System.currentTimeMillis();
        Log.add(c, "  up   " + f[0] + " (" + human(size) + ")" + (off > 0 ? ", carrying on from " + human(off) : ""));
        HttpURLConnection h;
        try {
            h = open("/api/backup/file?p=" + URLEncoder.encode(f[0], "UTF-8") + "&m=" + f[3] + "&total=" + size + "&off=" + off, "PUT");
            h.setFixedLengthStreamingMode(size - off);               // streams from disk; nothing is held in memory
            h.setRequestProperty("Content-Type", "application/octet-stream");
        } catch (IOException e) { Log.add(c, "  FAIL " + f[0] + ": " + e.getClass().getSimpleName() + ": " + e.getMessage()); return false; }
        long sentBytes = 0;
        int quarter = 0;
        try (InputStream in = new FileInputStream(file); OutputStream o = h.getOutputStream()) {
            if (off > 0 && in.skip(off) != off) throw new IOException("could not skip to " + off);
            byte[] buf = new byte[64 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) {
                if (halt(stop) || State.abortFile) { h.disconnect(); Log.add(c, "  stop " + f[0] + " at " + human(off + sentBytes) + " of " + human(size) + (State.cancel || State.abortFile ? " (you stopped it" : " (Android asked us to stop") + "; the server keeps what arrived)"); return false; }
                o.write(buf, 0, n); sentBytes += n; State.fileSent = off + sentBytes;
                if (size > 20L * 1024 * 1024 && (off + sentBytes) * 4 / size > quarter && quarter < 3) { quarter++; Log.add(c, "       " + f[0] + " " + quarter * 25 + "%"); }
            }
        } catch (IOException e) {
            State.sentBytes += sentBytes;
            Log.add(c, "  cut  " + f[0] + " after " + human(off + sentBytes) + " of " + human(size) + ": " + e.getClass().getSimpleName() + ": " + e.getMessage());
            h.disconnect();
            return false;
        }
        State.sentBytes += sentBytes;
        if (off + sentBytes != size) { h.disconnect(); Log.add(c, "  skip " + f[0] + ": it changed while being read, next pass"); return false; }
        int code;
        String reply;
        try { code = h.getResponseCode(); reply = read(h, false); }
        catch (IOException e) { Log.add(c, "  no answer for " + f[0] + ": " + e.getClass().getSimpleName() + ": " + e.getMessage()); return false; }
        double secs = Math.max(0.001, (System.currentTimeMillis() - State.fileStart) / 1000.0);
        if (code / 100 == 2) {
            boolean existed = reply.contains("\"existed\":true");
            Log.add(c, (existed ? "  has  " : "  done ") + f[0] + (existed ? " (the server already had it)" : " in " + String.format(Locale.US, "%.1f", secs) + " s (" + human((long) (sentBytes / secs)) + "/s)"));
            return true;
        }
        Log.add(c, "  FAIL " + f[0] + ": HTTP " + code + " " + clip(reply));
        if (code == 413) { skip(c, f[0], size, Long.parseLong(f[3])); State.abortFile = true; }        // bigger than the server accepts: asking again would never help
        else if (code == 507) State.fatal = "The server is out of disk space";
        else if (code == 401 || code == 403) State.fatal = "The server refused the backup token (is the app out of date? rebuild it)";
        else if (code == 429) State.fatal = "The server paused this phone after too many wrong tries; it works again in 10 minutes";
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
