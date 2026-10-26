package io.reclmr.pusher;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** The notification buttons: "Send now" for large files, and "Stop" on the progress notification. A receiver cannot do the upload itself, so Send now hands it to an expedited job. */
public class ActionReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent i) {
        if ("io.reclmr.pusher.STOP".equals(i.getAction())) {
            Log.add(c, "you tapped Stop on the notification: stopping and pausing backups");
            c.getSharedPreferences("s", 0).edit().putBoolean("paused", true).apply();
            State.cancel = true;
            Notif.runningClear(c);
            return;
        }
        Log.add(c, "you tapped Send now on the notification: sending the large files too");
        c.getSharedPreferences("s", 0).edit().putBoolean("paused", false).apply();
        Sched.sendBig(c);
    }
}
