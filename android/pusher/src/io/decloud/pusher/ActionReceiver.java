package io.decloud.pusher;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** The notification's "Send now" button. A receiver cannot do the upload itself, so it hands the work to an expedited job. */
public class ActionReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent i) {
        Log.add(c, "you tapped Send now on the notification: sending the large files too");
        Sched.sendBig(c);
    }
}
