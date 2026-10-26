package io.reclmr.pusher;

import android.app.job.JobParameters;
import android.app.job.JobService;
import android.net.Uri;

public class PushJob extends JobService {
    private volatile boolean stop;

    @Override public boolean onStartJob(final JobParameters p) {
        stop = false;
        String why;
        switch (p.getJobId()) {
            case Sched.PERIODIC: why = "the 6-hourly safety net"; break;
            case Sched.ON_NEW_MEDIA: {
                Uri[] u = p.getTriggeredContentUris();
                why = "new files appeared (" + (u == null ? 0 : u.length) + " changes reported)"; break;
            }
            case Sched.CARRY_ON: why = "carrying on after the app was left"; break;
            case Sched.SEND_BIG: why = "you asked to send the large files"; break;
            default: why = "job " + p.getJobId();
        }
        if (getSharedPreferences("s", 0).getBoolean("paused", false) && (p.getJobId() == Sched.PERIODIC || p.getJobId() == Sched.ON_NEW_MEDIA)) {
            Log.add(this, "job " + p.getJobId() + " skipped: backups are paused");
            if (p.getJobId() == Sched.ON_NEW_MEDIA) Sched.onNewMedia(this);
            return false;
        }
        Log.add(this, "job " + p.getJobId() + " started: " + why);
        Sync.note(this, "running...");
        new Thread(new Runnable() { @Override public void run() {
            boolean retry = false;
            try { Sync.run(PushJob.this, new Sync.Stop() { @Override public boolean now() { return stop; } }, p.getExtras().getInt("big", 0) == 1); }
            catch (Exception e) {
                Log.add(PushJob.this, "PASS FAILED: " + e.getClass().getSimpleName() + ": " + e.getMessage() + " (Android will retry later)");
                Sync.note(PushJob.this, "failed: " + e.getMessage());
                Notif.failed(PushJob.this, "Couldn't reach the server (" + e.getClass().getSimpleName() + "). It will try again soon.");
                retry = true;                                  // the system retries with backoff
            }
            if (p.getJobId() == Sched.ON_NEW_MEDIA) Sched.onNewMedia(PushJob.this);
            Log.add(PushJob.this, "job " + p.getJobId() + " finished" + (retry && !stop ? ", will be retried" : ""));
            jobFinished(p, retry && !stop);
        } }, "push").start();
        return true;                                       // work continues on that thread
    }

    @Override public boolean onStopJob(JobParameters p) {      // network or battery went away, or Android needs the resources: stop now, run again later
        stop = true;
        Log.add(this, "job " + p.getJobId() + " stopped by Android: " + (android.os.Build.VERSION.SDK_INT >= 31 ? reason(p.getStopReason()) : "reason not available on this Android version"));
        return true;
    }

    private static String reason(int r) {
        String[] names = {"unknown", "cancelled by the app", "preempted by another job", "ran out of time", "device state", "battery low", "not charging",
            "network lost", "device not idle", "storage low", "quota used up", "background restriction", "app standby", "user", "system processing"};
        return r >= 0 && r < names.length ? names[r] : "code " + r;
    }
}
