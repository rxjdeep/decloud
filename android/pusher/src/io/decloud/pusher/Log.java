package io.decloud.pusher;

import android.content.Context;

import java.io.*;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/** The app's own log: one text file in its private storage, trimmed to its newest part when it grows past ~400 KB. */
final class Log {
    private static final long MAX = 400_000, KEEP = 250_000;
    private static final SimpleDateFormat F = new SimpleDateFormat("MM-dd HH:mm:ss", Locale.US);

    private static File file(Context c) { return new File(c.getApplicationContext().getFilesDir(), "log.txt"); }

    static synchronized void add(Context c, String msg) {
        android.util.Log.i("pusher", msg);
        File f = file(c);
        try {
            if (f.length() > MAX) trim(f);
            try (FileWriter w = new FileWriter(f, true)) { w.write(F.format(new Date()) + "  " + msg + "\n"); }
        } catch (IOException ignored) { }
    }

    static synchronized String tail(Context c, int chars) {
        File f = file(c);
        if (!f.exists()) return "";
        try (RandomAccessFile r = new RandomAccessFile(f, "r")) {
            long start = Math.max(0, r.length() - chars);
            r.seek(start);
            byte[] b = new byte[(int) (r.length() - start)];
            r.readFully(b);
            String s = new String(b, "UTF-8");
            if (start > 0) { int nl = s.indexOf('\n'); if (nl >= 0) s = s.substring(nl + 1); }
            return s;
        } catch (IOException e) { return "(cannot read the log: " + e.getMessage() + ")"; }
    }

    static synchronized void clear(Context c) { file(c).delete(); }

    private static void trim(File f) throws IOException {
        byte[] keep;
        try (RandomAccessFile r = new RandomAccessFile(f, "r")) { r.seek(r.length() - KEEP); keep = new byte[(int) KEEP]; r.readFully(keep); }
        try (FileOutputStream o = new FileOutputStream(f)) { o.write(keep); }
    }
}
