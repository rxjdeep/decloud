package io.decloud.pusher;

/** What the running pass is doing right now, for the screen. Lives in memory only: when Android has stopped our process, the app is idle. */
final class State {
    static volatile boolean running;
    static volatile String phase = "idle";          // scanning, asking the server, uploading
    static volatile String file = "";
    static volatile long fileSize, fileSent, fileStart;
    static volatile long runStart, sentBytes;
    static volatile int sentFiles, haveFiles, failedFiles, total;

    static void begin() { running = true; phase = "starting"; file = ""; fileSize = fileSent = 0; runStart = System.currentTimeMillis(); sentBytes = 0; sentFiles = haveFiles = failedFiles = total = 0; }
    static void end() { running = false; phase = "idle"; file = ""; fileSize = fileSent = 0; }
}
