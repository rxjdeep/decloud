package io.decloud.pusher;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.graphics.drawable.Icon;

/** The "ask me" for large files on mobile data. One notification, updated in place; it goes away once nothing is waiting. */
final class Notif {
    private static final String CHANNEL = "big", ACTION = "io.decloud.pusher.SEND_BIG";
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

    static void clear(Context c) {
        if (lastCount == 0) return;
        lastCount = 0; lastBytes = 0;
        c.getSystemService(NotificationManager.class).cancel(ID);
    }
}
