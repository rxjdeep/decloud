package io.decloud.pusher;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent i) {
        Log.add(c, "received " + i.getAction() + ": arming the jobs");
        Sched.all(c);
    }
}
