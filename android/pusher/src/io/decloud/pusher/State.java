package io.decloud.pusher;

/** What the running pass is doing right now, for the screen. Lives in memory only: when Android has stopped our process, the app is idle. */
final class State {
    static volatile boolean running;
    static volatile String phase = "idle";          // scanning, asking the server, uploading
    static volatile String file = "";
    static volatile long fileSize, fileSent, fileStart;
    static volatile long runStart, sentBytes;
    static volatile int sentFiles, haveFiles, failedFiles, total;
    static volatile long fileOff, fileMtime;                 // where this attempt of the file started, and the file's identity (for "skip this file")
    static volatile boolean cancel, abortFile;               // the user pressed Stop (the whole pass) / Skip this file (just the current upload)
    static volatile String fatal = "", fileRel = "";         // a reason that makes every further upload pointless (server full, token refused)

    static void begin() { cancel = false; abortFile = false; fatal = ""; running = true; phase = "starting"; file = ""; fileSize = fileSent = 0; runStart = System.currentTimeMillis(); sentBytes = 0; sentFiles = haveFiles = failedFiles = total = 0; }
    static void end() { running = false; phase = "idle"; file = ""; fileSize = fileSent = 0; }
}
