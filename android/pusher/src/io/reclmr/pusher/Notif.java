package io.reclmr.pusher;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.graphics.drawable.Icon;

/** The "ask me" for large files on mobile data. One notification, updated in place; it goes away once nothing is waiting. */
final class Notif {
    private static final String CHANNEL = "big", ACTION = "io.reclmr.pusher.SEND_BIG";
    private static final int ID = 1;
    private static int lastCount = -1;
    private static long lastBytes = -1;

    static void held(Context c, int n, long bytes) {
        if (n == lastCount && bytes == lastBytes) return;      // already showing exactly this; do not alert again
        lastCount = n; lastBytes = bytes;
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Large files waiting", NotificationManager.IMPORTANCE_DEFAULT));
        int flags = PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT;
        PendingIntent open = PendingIntent.getActivity(c, 0, new Intent(c, MainActivity.class), flags);
        PendingIntent send = PendingIntent.getBroadcast(c, 0, new Intent(c, ActionReceiver.class).setAction(ACTION), flags);
        Notification.Action go = new Notification.Action.Builder(Icon.createWithResource(c, android.R.drawable.stat_sys_upload), "Send now", send).build();
        nm.notify(ID, new Notification.Builder(c, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setContentTitle(n + (n == 1 ? " large file" : " large files") + " (" + Sync.human(bytes) + ") waiting")
            .setContentText("Held back so it doesn't use your mobile data. They go on their own once you're on Wi-Fi.")
            .setStyle(new Notification.BigTextStyle().bigText("Held back so it doesn't use your mobile data. They go on their own once you're on Wi-Fi, or tap Send now to send them over mobile data."))
            .setContentIntent(open).addAction(go).setAutoCancel(true).setOnlyAlertOnce(true).build());
    }

    private static long lastRun;

    /** The ongoing "Sending..." notification with a Stop button, so a slow upload can be stopped without opening the app. */
    static void running(Context c, String text) {
        long now = System.currentTimeMillis();
        if (now - lastRun < 1500) return;
        lastRun = now;
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("run", "Backup in progress", NotificationManager.IMPORTANCE_LOW));
        int flags = PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT;
        PendingIntent open = PendingIntent.getActivity(c, 2, new Intent(c, MainActivity.class), flags);
        PendingIntent stop = PendingIntent.getBroadcast(c, 2, new Intent(c, ActionReceiver.class).setAction("io.reclmr.pusher.STOP"), flags);
        nm.notify(3, new Notification.Builder(c, "run").setSmallIcon(android.R.drawable.stat_sys_upload).setContentTitle("Backing up").setContentText(text)
            .addAction(new Notification.Action.Builder(Icon.createWithResource(c, android.R.drawable.ic_media_pause), "Stop", stop).build())
            .setOngoing(true).setOnlyAlertOnce(true).setContentIntent(open).build());
    }

    static void runningClear(Context c) { lastRun = 0; c.getSystemService(NotificationManager.class).cancel(3); }

    private static String lastFail = "";

    /** Tells you when files could not be sent, instead of leaving it to be found by opening the app. */
    static void failed(Context c, String text) {
        if (text.equals(lastFail)) return;
        lastFail = text;
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("fail", "Backup problems", NotificationManager.IMPORTANCE_DEFAULT));
        PendingIntent open = PendingIntent.getActivity(c, 1, new Intent(c, MainActivity.class), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        nm.notify(2, new Notification.Builder(c, "fail").setSmallIcon(android.R.drawable.stat_notify_error)
            .setContentTitle("Backup didn't finish").setContentText(text).setStyle(new Notification.BigTextStyle().bigText(text))
            .setContentIntent(open).setAutoCancel(true).setOnlyAlertOnce(true).build());
    }

    static void failedClear(Context c) {
        if (lastFail.isEmpty()) return;
        lastFail = "";
        c.getSystemService(NotificationManager.class).cancel(2);
    }

    static void clear(Context c) {
        if (lastCount == 0) return;
        lastCount = 0; lastBytes = 0;
        c.getSystemService(NotificationManager.class).cancel(ID);
    }
}
