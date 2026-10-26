package io.reclmr.pusher;

import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.provider.MediaStore;

/** The jobs. The automatic ones run on any network with a battery that is not low (the pass itself holds large files back on mobile data); Android decides the exact moment. */
final class Sched {
    static final int PERIODIC = 1, ON_NEW_MEDIA = 2, CARRY_ON = 3, SEND_BIG = 4;

    static void all(Context c) {
        JobScheduler js = c.getSystemService(JobScheduler.class);
        // jobs survive an app update with the rules they were armed with, so a new rule set needs the periodic job armed again
        android.content.SharedPreferences sp = c.getSharedPreferences("s", 0);
        if (sp.getInt("jobsv", 0) != 2) { js.cancel(PERIODIC); sp.edit().putInt("jobsv", 2).apply(); }
        // safety net for files the media store never announces; persisted by the system across reboots. Not re-armed if already pending, or its timer would restart.
        if (js.getPendingJob(PERIODIC) == null) {
            int r = js.schedule(new JobInfo.Builder(PERIODIC, name(c))
                .setPeriodic(6 * 3600_000L, 3600_000L)
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setRequiresBatteryNotLow(true)
                .setPersisted(true).build());
            Log.add(c, "armed the 6-hourly job: " + (r == JobScheduler.RESULT_SUCCESS ? "ok" : "REFUSED by Android"));
        }
        onNewMedia(c);
    }

    /** Fires shortly after anything new lands in shared storage. It runs once per arming, so every run (and every boot) arms it again. */
    static void onNewMedia(Context c) {
        JobScheduler js = c.getSystemService(JobScheduler.class);
        int r = js.schedule(new JobInfo.Builder(ON_NEW_MEDIA, name(c))
            .addTriggerContentUri(new JobInfo.TriggerContentUri(MediaStore.Files.getContentUri("external"), JobInfo.TriggerContentUri.FLAG_NOTIFY_FOR_DESCENDANTS))
            .setTriggerContentUpdateDelay(15_000)            // wait for the camera to finish writing...
            .setTriggerContentMaxDelay(2 * 60_000)           // ...but not forever during a burst
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setRequiresBatteryNotLow(true).build());
        if (r != JobScheduler.RESULT_SUCCESS) Log.add(c, "arming the new-files job was REFUSED by Android");
    }

    /** The app was left while a pass was running (swiped away): let Android finish it. Expedited, so it starts at once. */
    static void carryOn(Context c) { expedited(c, CARRY_ON, false); }

    /** The notification's Send now: a pass that also sends the files held back on mobile data. */
    static void sendBig(Context c) { expedited(c, SEND_BIG, true); }

    private static void expedited(Context c, int id, boolean big) {
        android.os.PersistableBundle x = new android.os.PersistableBundle();
        x.putInt("big", big ? 1 : 0);
        JobInfo.Builder b = new JobInfo.Builder(id, name(c)).setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setExtras(x);
        if (android.os.Build.VERSION.SDK_INT >= 31) b.setExpedited(true);
        int r = c.getSystemService(JobScheduler.class).schedule(b.build());
        Log.add(c, "asked Android to continue in the background (job " + id + "): " + (r == JobScheduler.RESULT_SUCCESS ? "accepted" : "REFUSED"));
    }

    private static ComponentName name(Context c) { return new ComponentName(c, PushJob.class); }
}
